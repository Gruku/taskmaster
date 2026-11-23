# User intent: move a solo Taskmaster store onto the team server once, losing nothing (team-server-014): `pack` freezes and
# snapshots the laptop's store into a bundle and untracks `.taskmaster/` with a local plumbing commit; `init` installs the
# bundle on the host clone. Both are resumable by journal, never push, and never delete the operator's files.
"""`taskmaster serve init pack|--undo|--from` (design docs/specs/2026-11-07-team-server-014-migration-design.md).

The order in `pack` is the point: freeze (marker + latch), settle, snapshot under a write lock, refuse late writes, stop
the coordinator, re-check the store read-only, and only then move refs. `CHECKPOINT` is a test seam called between stages.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path

from taskmaster import server_fence, server_marker
from taskmaster.native import cutover
from taskmaster.native import projection as native_projection
from taskmaster.native.schema import SUPPORTED

from . import bundle as bundles
from . import state_export as se
from . import untrack
from .config import ServeRefused, ServeUnavailable
from taskmaster.coordinator.ownership import _is_link, private_directory_at

from .principals import replace_private

PACK_JOURNAL = "pack.json"
INIT_JOURNAL = "init.json"
MAX_TRIES = 3
SEQ_SQL = "SELECT COALESCE(MAX(seq),0) FROM domain_events"
REFUSE_TEXT = "the backlog moved to the team server at {url}; nothing was written"
LOCAL_SKIP = frozenset({"store.db", "store.db-wal", "store.db-shm", "PROGRESS.md", "serve", "coordinator", "integrity-check.json"})
IN_PROGRESS = ("MERGE_HEAD", "rebase-merge", "rebase-apply", "CHERRY_PICK_HEAD", "REVERT_HEAD", "BISECT_LOG")

FENCE_RELEASE: str | None = None        # set to the release that ships the split-brain fence (design O5)

ROLLOUT_RULE = """Before you push the commit that adds .taskmaster-server.json:

1. Everyone who works in a clone of this repository must run {floor} or later: a build with the split-brain fence,
   where `taskmaster serve marker show` exists. Older builds don't know the marker. They keep opening, or quietly
   creating, a local backlog, and their work never reaches the server. Check every machine, including CI runners
   and other machines of your own.
2. Anyone with backlog changes that aren't in your store yet (edits in their own local store, or .taskmaster/
   changes on unmerged branches) hands them over first. The server starts from your store as it is at the pack,
   and nothing else is migrated.

After the push: anyone still on an older build updates before their next session in this repository. Until they
have their own API key, their taskmaster tools refuse here, and nothing is written locally. When merging the new
main into an older branch, keep the deletion of .taskmaster/ (`git rm -r --cached .taskmaster`); never add it back."""

PUSH_REJECTED_RULE = """If the push is rejected, don't pull or rebase over the pack commit. Teammates pushed something after your
last fetch. Merging it now would drop their .taskmaster/ changes, or track .taskmaster/ again. Instead:
1. taskmaster serve init pack --undo --not-pushed --root <this clone>
2. git pull
3. backlog_sync
4. run the pack again."""

LAPTOP_WARNING = ("Don't check out a branch that still tracks .taskmaster/ in this clone: Git would overwrite the fenced "
                  "files with that branch's copies (the store and the bundle are unaffected).")

SINGLE_USE = ("This bundle is single-use: it carries the host identity. Install it in one host clone only; before any "
              "reinstall, stop and delete the old host clone. Delete the bundle here and on the host once the server runs.")

HOST_PULL_RULE = """Pulling on the host: first `git fetch`. Then `git diff --name-only HEAD @{u} -- .taskmaster` must print nothing
and `git ls-tree @{u} -- .taskmaster` must be empty. Only then run `git merge --ff-only @{u}`. If either prints something,
upstream re-tracked .taskmaster/: fix main from the laptop (`git rm -r --cached .taskmaster`, commit, push) and don't pull.
Never run checkout, switch, reset to another commit, bisect or stash in the host clone: ignored files are overwritten
without -f. See docs/runbooks/team-server-migration.md#host-head."""

UNDO_WARNING = ("If the pack commit is already public, undoing unfences this laptop while every other clone stays fenced "
                "and the host may already serve. Only use --undo before the push.")


def rollout_rule() -> str:
    floor = (f"taskmaster {FENCE_RELEASE}" if FENCE_RELEASE
             else "the first Taskmaster release that has `taskmaster serve marker`")
    return ROLLOUT_RULE.format(floor=floor)


class MigrationRefused(ServeRefused):
    """A pre-flight or settle refusal: exit 2."""


class MigrationFailed(ServeRefused):
    """Something changed under the migration or a rerun can't be trusted: exit 1, nothing more is done."""
    exit_code = 1


def CHECKPOINT(stage: str) -> None:        # noqa: N802 -- replaced by tests to inject a crash or an interleaving
    return None


def _checkpoint(stage: str) -> None:
    CHECKPOINT(stage)


# -- small helpers -----------------------------------------------------------------------------------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_path(path: Path) -> str | None:
    try:
        return sha256_bytes(Path(path).read_bytes())
    except OSError:
        return None


def _same_path(left, right) -> bool:
    return os.path.normcase(os.path.realpath(os.fspath(left))) == os.path.normcase(os.path.realpath(os.fspath(right)))


def _lf(data: bytes) -> bytes:
    return data.replace(b"\r\n", b"\n")


def journal_path(root: Path, name: str) -> Path:
    return untrack.init_directory(root) / name


def load_journal(root: Path, name: str) -> dict | None:
    try:
        value = json.loads(journal_path(root, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if isinstance(value, dict) else None


def save_journal(root: Path, name: str, journal: dict) -> None:
    replace_private(journal_path(root, name), (json.dumps(journal, indent=1, sort_keys=True) + "\n").encode("utf-8"))


def _stage(root: Path, journal: dict, stage: str, **more) -> None:
    journal.update(more, stage=stage)
    save_journal(root, PACK_JOURNAL, journal)


def _high(connection) -> int:
    return int(connection.execute(SEQ_SQL).fetchone()[0])


def _is_active(journal: dict | None) -> bool:
    return journal is not None and journal.get("stage") not in ("done", "undone")


def _project(root) -> Path:
    try:
        resolved = Path(root).resolve(strict=True)
    except FileNotFoundError:
        raise MigrationRefused(f"{root} does not exist") from None
    if not (resolved / ".taskmaster" / "local").is_dir():
        raise MigrationRefused(f"{resolved} has no .taskmaster/local/: it is not a project with a local store. A "
                               "files-only or legacy project runs the native cutover first "
                               "(docs/runbooks/native-cutover.md)")
    return resolved


# -- the store ---------------------------------------------------------------------------------------

def store_facts(path: Path, *, expect_store: str | None = None, expect_high: int | None = None,
                deep: bool = False, root: Path | None = None) -> dict:
    """Read-only facts of the store file at `path`: schema, store_id, high seq, projection watermark, counts, claims.

    `deep` adds integrity_check and the native-store assertion (used on the snapshot copy and on the host's staging copy).
    MigrationRefused when it isn't an activated native store of a supported schema."""
    from taskmaster.native import db
    from taskmaster.admission import UnsupportedStoreError
    try:
        with closing(cutover.connect_readonly(path)) as connection:
            state = cutover.classify(connection)
            if not state["native"] or state["invalid"] or state["migration_state"] != "ready":
                raise MigrationRefused(_not_native_text(state))
            manifest = dict(connection.execute("SELECT key,value FROM native_manifest"))
            schema = int(manifest.get("schema_version", "0"))
            if schema not in SUPPORTED:
                raise MigrationRefused(f"the store is schema {schema}; this build supports {sorted(SUPPORTED)}: "
                                       "a newer store needs a newer plugin")
            if deep:
                if [tuple(row) for row in connection.execute("PRAGMA integrity_check").fetchall()] != [("ok",)]:
                    raise MigrationRefused("the store failed integrity_check")
                try:
                    db.assert_native(connection)
                except UnsupportedStoreError as exc:
                    raise MigrationRefused(f"the store is not an activated native store ({exc})") from None
            facts = {"schema": schema, "store_id": manifest.get("store_id"), "high": _high(connection),
                     "watermark": native_projection.watermark(connection)}
            if expect_store is not None and facts["store_id"] != expect_store:
                raise MigrationRefused("the store's identity differs from the one recorded")
            if expect_high is not None and (facts["high"] != expect_high or facts["watermark"] != expect_high):
                raise MigrationFailed(f"the store is at seq {facts['high']} (watermark {facts['watermark']}), "
                                      f"not the {expect_high} recorded")
            if deep:
                facts.update(_describe(root or Path(path).parent, connection))
            return facts
    except MigrationRefused:
        raise
    except Exception as exc:                       # sqlite3.Error and friends: an unreadable store refuses
        if isinstance(exc, ServeRefused):
            raise
        raise MigrationRefused(f"cannot read the store at {path}: {exc}") from None


def _not_native_text(state: dict) -> str:
    if state.get("newer"):
        return "the store is newer than this build understands; update the plugin"
    return ("the project's store is not an activated native store (legacy or staging authority, a fenced "
            "migration, or damaged): run the native cutover first (docs/runbooks/native-cutover.md)")


def _describe(root: Path, connection) -> dict:
    counts, claims, team = {}, [], None
    try:
        counts = {kind: int(n) for kind, n in connection.execute(
            "SELECT kind, COUNT(*) FROM entity_core WHERE deleted=0 GROUP BY kind")}
        counts["external_document"] = int(connection.execute("SELECT COUNT(*) FROM external_documents").fetchone()[0])
    except Exception:  # noqa: BLE001 -- counts are informational
        pass
    try:
        claims = [{"task": task, "holder": holder, "expires_at": expires} for task, holder, expires in connection.execute(
            "SELECT c.public_id, json_extract(o.locked_by_json,'$'), (SELECT json_extract(e.value_json,'$') FROM "
            "entity_extensions e WHERE e.entity_key=c.entity_key AND e.field='claim_expires') FROM entity_core c "
            "JOIN task_operational o ON o.entity_key=c.entity_key WHERE c.kind='task' AND c.deleted=0 "
            "AND COALESCE(json_extract(o.locked_by_json,'$'),'')<>'' ORDER BY c.public_id")]
    except Exception:  # noqa: BLE001
        pass
    try:
        from taskmaster import project
        from taskmaster.coordinator import identity_gate
        team = bool(project.team_mode_enabled(root, project_doc=identity_gate._project_doc(connection)))
    except Exception:  # noqa: BLE001
        team = None
    return {"counts": counts, "claims": claims, "team_mode": team}


def _verify_projection_files(root: Path, connection) -> None:
    base = root / ".taskmaster"
    for rel, content_hash in connection.execute(
            "SELECT file, content_hash FROM projection WHERE file NOT LIKE 'local/%' ORDER BY file").fetchall():
        try:
            data = (base / Path(*str(rel).split("/"))).read_bytes()
        except FileNotFoundError:
            raise MigrationRefused(f".taskmaster/{rel} is missing: the export isn't in step with the store; "
                                   "run backlog_sync, then pack again") from None
        if not native_projection.matches_record(data, content_hash):
            raise MigrationRefused(f".taskmaster/{rel} differs from the store (edited by hand, or not exported yet): "
                                   "run backlog_sync, then pack again")


# -- pre-flight --------------------------------------------------------------------------------------

class Preflight:
    def __init__(self, git, current, tips, new_tips):
        self.git, self.current, self.tips, self.new_tips = git, current, tips, new_tips


def _root_kind_refusal(git, root: Path) -> str | None:
    code, top = git.text("rev-parse", "--show-toplevel")
    if code or not top or not _same_path(top, root):
        return f"{root} is not the top level of a Git checkout; run this from the repository root"
    _, gitdir = git.text("rev-parse", "--absolute-git-dir")
    _, common = git.text("rev-parse", "--git-common-dir")
    if not _same_path(gitdir, root / common):
        return f"{root} is a linked worktree; run this in the main checkout"
    if not (root / ".git").is_dir():
        return (f"{root} has a .git that isn't a directory (a separate git dir or a submodule): the latch needs "
                "<root>/.git/ to be a directory")
    _, sup = git.text("rev-parse", "--show-superproject-working-tree")
    if sup:
        return f"{root} is a submodule; run this in a standalone repository"
    return None


def _gitignore_owned(git, root: Path, journal: dict | None) -> bool:
    """P4's journal-owned `.gitignore` (R2): its pre-pack bytes, or the new commit's bytes up to line endings."""
    if not _is_active(journal) or journal.get("stage") != "refs":
        return False
    path = root / ".gitignore"
    before = journal.get("gitignore_before_sha256")
    working = sha256_path(path)
    if working == before:
        return True
    new = (journal.get("commits") or {}).get(journal.get("current_branch"))
    if not new or working is None:
        return False
    try:
        entries = {entry.name: entry for entry in untrack.root_entries(git, new)}
        blob = untrack.blob(git, entries[untrack.GITIGNORE].sha)
        return _lf(path.read_bytes()) == _lf(blob)
    except (KeyError, OSError, untrack.UntrackError):
        return False


def _marker_owned(root: Path, journal: dict | None) -> bool:
    return _is_active(journal) and journal.get("marker_sha256") is not None and \
        sha256_path(server_marker.marker_path(root)) == journal["marker_sha256"]


def _bundle_owned(out: Path, journal: dict | None) -> bool:
    return _is_active(journal) and journal.get("bundle_sha256") is not None and sha256_path(out) == journal["bundle_sha256"]


def _ladder_label_problems(store: Path) -> list[str]:
    """Merge-ladder labels in the store's project manifest that break the 027 D8 rule (they would reach hook text)."""
    from taskmaster.project import invalid_merge_labels
    try:
        with closing(cutover.connect_readonly(store)) as connection:
            row = connection.execute(
                "SELECT value_json FROM configuration WHERE field='conventions' AND entity_key="
                "(SELECT entity_key FROM entity_core WHERE kind='project' AND public_id='__project__')").fetchone()
    except sqlite3.Error:
        return []
    try:
        conventions = json.loads(row[0]) if row else None
    except ValueError:
        return []
    return invalid_merge_labels({"conventions": conventions}) if isinstance(conventions, dict) else []


def preflight_pack(root: Path, *, url: str, out: Path, branches, journal: dict | None, record=None) -> Preflight:
    """Design §6.1, P1-P12. Reads only (apart from the empty `serve/init/` directory); MigrationRefused names the problem."""
    git = untrack.git_for(root, record=record)
    problem = _root_kind_refusal(git, root)
    if problem:
        raise MigrationRefused(problem)
    owned_marker = _marker_owned(root, journal)
    for each in server_fence.fence_roots(root):
        marker_here = os.path.lexists(server_marker.marker_path(each))
        latch_here = os.path.lexists(server_marker.latch_path(each))
        if (marker_here and not owned_marker) or (latch_here and not (marker_here and owned_marker)):
            raise MigrationRefused(f"{each} is already in server mode (a marker or latch is present); "
                                   "this project has already been migrated or is a clone of a served project")
    code, ref = git.text("symbolic-ref", "-q", "HEAD")
    current = untrack.current_branch(git)
    if current is None:
        raise MigrationRefused("HEAD is detached or unborn; check out the branch to migrate")
    gitdir = Path(git.text("rev-parse", "--absolute-git-dir")[1])
    for name in IN_PROGRESS:
        if (gitdir / name).exists():
            raise MigrationRefused(f"a merge, rebase, cherry-pick, revert or bisect is in progress ({name}): "
                                   "finish or abort it, then pack")
    if _is_active(journal) and journal.get("current_branch") != current:
        raise MigrationRefused(f"HEAD is on {current}, but the pack in progress is for {journal.get('current_branch')}; "
                               "check that branch out, or `--undo --not-pushed`")
    _, status = git.text("status", "--porcelain=v1", "-z", "--untracked-files=no", "--", untrack.GITIGNORE)
    if status.strip("\0") and not _gitignore_owned(git, root, journal):
        raise MigrationRefused(".gitignore has staged or unstaged changes; commit or discard them, then pack")
    if os.path.lexists(server_marker.marker_path(root)) and not owned_marker:
        raise MigrationRefused(f"{server_marker.MARKER_NAME} already exists and isn't from this pack")
    store = root / ".taskmaster" / "local" / "store.db"
    if not store.is_file():
        raise MigrationRefused("there is no store at .taskmaster/local/store.db: a files-only or legacy project runs "
                               "the native cutover first (docs/runbooks/native-cutover.md)")
    store_facts(store)
    labels = _ladder_label_problems(store)
    if labels:
        raise MigrationRefused("merge ladder labels must be branch-like ([A-Za-z0-9._/-], up to 64 characters, no "
                               "leading '-'); rename these with backlog_project_set, then pack: "
                               + ", ".join(repr(label) for label in labels))
    problem = server_marker.url_problem(url)
    if problem:
        raise MigrationRefused(f"--url: {problem}")
    out = Path(out)
    if out.exists() and not _bundle_owned(out, journal):
        raise MigrationRefused(f"{out} already exists; choose a new --out path")
    if not out.parent.is_dir():
        raise MigrationRefused(f"the directory for --out doesn't exist: {out.parent}")
    resolved = Path(os.path.realpath(out.parent)) / out.name
    if _same_path(resolved, root) or Path(os.path.normcase(str(resolved))).is_relative_to(os.path.normcase(str(Path(os.path.realpath(root))))):
        raise MigrationRefused("--out must be outside the repository's working tree (the bundle holds the host identity)")
    if git.run("var", "GIT_COMMITTER_IDENT")[0] != 0:
        raise MigrationRefused("Git has no committer identity: set user.name and user.email, then pack")
    _, objects = git.text("rev-parse", "--show-object-format")
    _, refs = git.text("rev-parse", "--show-ref-format")
    if objects not in ("", "sha1") or refs == "reftable":
        raise MigrationRefused("the repository uses the sha256 object format or the reftable ref format; the export on "
                               "the host supports sha1 and files refs only")
    owned_tips = frozenset((journal.get("commits") or {}).values()) if _is_active(journal) else frozenset()
    tips = {}
    for name in dict.fromkeys(branches):
        problem = untrack.branch_problem(git, root, name, current=current, owned=owned_tips)
        if problem:
            raise MigrationRefused(f"--branch {problem}")
        tips[name] = untrack.tip(git, name)
    old_current = untrack.tip(git, current)
    if old_current not in owned_tips:
        problem = untrack.branch_refusal(git, current, old_current)
        if problem:
            raise MigrationRefused(problem)
    tips[current] = old_current
    if (root / untrack.GITIGNORE).exists() and not (_is_active(journal) and journal.get("stage") == "refs"):
        if untrack.GITIGNORE not in {entry.name for entry in untrack.root_entries(git, old_current)}:
            raise MigrationRefused(".gitignore exists in the working tree but not in HEAD (untracked or ignored): "
                                   "commit it or move it aside first, because the pack rewrites it from the index")
    if not (_is_active(journal) and journal.get("stage") == "refs"):       # after the refs moved it is only a report line
        _upstream_check(git, current, (journal or {}).get("branches", {}).get(current) if _is_active(journal) else None)
    return Preflight(git, current, tips, owned_tips)


UPSTREAM_MOVED = "upstream moved during pack: `serve init pack --undo --not-pushed`, pull, backlog_sync, pack again (never merge over the pack commit)"


def _upstream_check(git, current: str, recorded_old: str | None, *, strict: bool = True) -> str | None:
    """P12: the upstream's last-fetched tip must be an ancestor of what is being packed. Returns a warning, or None.
    `strict=False` (the report, after the refs moved) turns the refusal into the UPSTREAM_MOVED warning."""
    code, upstream = git.text("rev-parse", "--symbolic-full-name", "@{u}")
    if code != 0 or not upstream:
        return "this branch has no upstream configured: make sure nobody pushed since your last fetch"
    code, up_tip = git.text("rev-parse", "--verify", "-q", "@{u}^{commit}")
    if code != 0 or not untrack.HEX.fullmatch(up_tip):
        return None
    base = recorded_old or "HEAD"
    code, _ = git.text("merge-base", "--is-ancestor", up_tip, base)
    if code != 0:
        if not strict:
            return UPSTREAM_MOVED
        raise MigrationRefused("your branch is behind its upstream: `git fetch`, merge it, run `backlog_sync` so the "
                               "store takes any .taskmaster/ changes teammates pushed, then pack. A pack over unmerged "
                               "upstream changes would lose them when you merge after the push.")
    return None


# -- pack --------------------------------------------------------------------------------------------

def _readonly_check(root: Path, journal: dict) -> bool:
    try:
        store_facts(root / ".taskmaster" / "local" / "store.db", expect_store=journal["store_id"],
                    expect_high=journal["high"])
    except (MigrationRefused, MigrationFailed):
        return False
    return True


def _abandon(root: Path, journal: dict | None) -> None:
    """Undo the freeze after a refusal that came before any ref moved: marker (if ours) and latch go, stage `undone`."""
    if journal is None or journal.get("stage") in ("refs", "done"):      # once a ref moved, only --undo may unfence
        return
    _clean_temp_copies(root)
    if _marker_owned(root, journal):
        try:
            server_marker.marker_path(root).unlink()
        except OSError:
            pass
    server_marker.clear_latch(root)
    _stage(root, journal, "undone")


def _settle(owner, root: Path, timeout: float) -> int:
    with closing(owner._connect(readonly=True)) as connection:
        high = _high(connection)
    result = owner.flush(high, timeout=timeout)
    if result.get("state") != "exported" or result.get("notices"):
        notices = "; ".join(result.get("notices") or ["the export did not settle in time"])
        raise MigrationRefused(f"the store's export isn't settled ({notices}); fix that (backlog_sync, resolve any held "
                               "files), then pack again")
    with closing(owner._connect(readonly=True)) as connection:
        _verify_projection_files(root, connection)
    return high


def _snapshot(owner, root: Path, high: int, temp: Path, url: str) -> bool:
    """Copy the store under a write lock with the writer paused; False when the store moved (nothing copied)."""
    owner.pause_writer()
    try:
        with owner.execution:        # waits out a command already past admission; none can start until writes are refused
            with closing(owner._connect()) as connection:
                connection.execute("BEGIN IMMEDIATE")
                try:
                    if _high(connection) != high:
                        return False
                    cutover.copy_database(root / ".taskmaster" / "local" / "store.db", temp, prepare=_drop_machine_state)
                finally:
                    if connection.in_transaction:
                        connection.execute("ROLLBACK")
            _checkpoint("snapshot_taken")
            owner.refuse_writes(REFUSE_TEXT.format(url=url))
        return True
    finally:
        owner.resume_writer()


def _drop_machine_state(destination) -> None:
    """The bundle's copy loses this laptop's checkout records (absolute paths) and session rows; the host registers its own."""
    # Without secure_delete (off by default on some SQLite builds, e.g. Python 3.11's) the deleted rows' bytes
    # stay in freed pages and the laptop paths still travel in the bundle.
    destination.execute("PRAGMA secure_delete=ON")
    destination.execute("DELETE FROM sync_state WHERE key>=? AND key<?", ("checkout.", "checkout/"))
    # team-server-026: session rows hold this laptop's host name, cwd and pid; the host registers its own.
    destination.execute("DELETE FROM sessions")


def _temp_copy(root: Path) -> Path:
    return untrack.init_directory(root) / f"store-{os.getpid()}-{time.monotonic_ns()}.db"


def _clean_temp_copies(root: Path) -> None:
    for item in untrack.init_directory(root).glob("store-*.db*"):
        try:
            item.unlink()
        except OSError:
            pass


def _acquire_lease(root: Path):
    try:
        return server_fence.host_lease(root)
    except server_fence.HostLeaseHeld:
        raise ServeUnavailable("another `taskmaster serve` process (a pack, the daemon, export run or init) holds the "
                               "host lease for this root: another pack is running; wait for it, or stop it first") from None


def pack(root, *, url: str, out, branches=(), flush_timeout: float = 120.0, record=None) -> dict:
    """Freeze, snapshot and bundle the solo store, untrack `.taskmaster/`; returns the report. Design §6."""
    root = _project(root)
    out = Path(out)
    branches = tuple(dict.fromkeys(branches))
    journal = load_journal(root, PACK_JOURNAL) if (root / ".taskmaster" / "local" / "serve" / "init").is_dir() else None
    if journal is not None and journal.get("stage") == "done":
        return journal["report"]
    if journal is not None and journal.get("stage") == "undone":
        journal = None
    if _is_active(journal) and (journal["url"] != url or Path(journal["out"]) != out
                                or sorted(journal["branches"]) != sorted([*branches, journal["current_branch"]])):
        raise MigrationRefused("a pack is in progress for different arguments (--url, --out or --branch); rerun with "
                               "the same ones, or `taskmaster serve init pack --undo --not-pushed`")
    pre = preflight_pack(root, url=url, out=out, branches=branches, journal=journal, record=record)
    lease = _acquire_lease(root)
    try:
        return _pack_locked(root, lease, pre, url=url, out=out, branches=branches, journal=journal,
                            flush_timeout=flush_timeout)
    finally:
        lease.release()


def _pack_locked(root, lease, pre, *, url, out, branches, journal, flush_timeout) -> dict:
    git, current = pre.git, pre.current
    fresh = not _is_active(journal)
    if fresh:
        host_id = server_marker.new_host_id()
        journal = {"stage": "pending", "url": url, "host_id": host_id, "out": str(out), "current_branch": current,
                   "branches": dict(pre.tips), "extra": list(branches), "started": _now(),
                   "marker_sha256": sha256_bytes(server_marker.render(server_marker.Marker(url, host_id)).encode("utf-8")),
                   "gitignore_before_sha256": sha256_path(root / ".gitignore")}
    stage = journal["stage"]
    store_path = root / ".taskmaster" / "local" / "store.db"
    resume_refs = False
    try:
        if stage in ("bundled", "refs") and _readonly_check(root, journal) and (
                stage == "refs" or _bundle_owned(out, journal)):
            resume_refs = True
        elif stage == "refs":
            raise MigrationFailed("the store was written after the bundle (a pre-009 build?); "
                                  "`serve init pack --undo --not-pushed`, then pack again")
        if not resume_refs:
            facts, files, temp = _freeze_and_snapshot(root, lease, journal, fresh, flush_timeout)
            commits = _build_commits(root, git, journal, pre, facts, files)
            written = _write_bundle(root, journal, out, facts, files, temp, commits)
            journal["bundle_sha256"] = written["sha256"]
            _stage(root, journal, "bundled")
            _checkpoint("bundled")
    except MigrationFailed:
        raise
    except (MigrationRefused, untrack.UntrackRefused, untrack.SigningFailed, bundles.BundleRefused) as exc:
        if _is_active(journal) and journal.get("stage") != "pending":
            _abandon(root, journal)
        raise MigrationRefused(str(exc)) from None
    return _move_refs(root, git, journal, pre)          # outside the handler above: nothing here may unfence


def _freeze_and_snapshot(root: Path, lease, journal: dict, fresh: bool, flush_timeout: float):
    store_path = root / ".taskmaster" / "local" / "store.db"
    tries = 0
    while True:
        tries += 1
        if tries > MAX_TRIES:
            raise MigrationRefused("the store kept changing while it was snapshotted: stop every session that writes "
                                   "to this project, then pack again")
        _clean_temp_copies(root)
        temp = _temp_copy(root)
        with se.offline_owner(root, lease=lease) as owner:
            high = _settle(owner, root, flush_timeout)
            if journal["stage"] == "pending":
                _stage(root, journal, "frozen", store_id=owner.identity["store_id"], high=high)
                server_marker.publish_marker(root, journal["url"], journal["host_id"])
                _checkpoint("frozen")
            else:
                server_marker.publish_marker(root, journal["url"], journal["host_id"])     # refreshes a missing latch
            taken = _snapshot(owner, root, high, temp, journal["url"])
        if not taken:
            continue
        _checkpoint("stopped")
        try:
            store_facts(store_path, expect_store=journal["store_id"], expect_high=high)
        except MigrationFailed:
            temp.unlink(missing_ok=True)                  # a commit slipped past the gate: take it again
            continue
        facts = store_facts(temp, expect_store=journal["store_id"], expect_high=high, deep=True, root=root)
        _stage(root, journal, "snapshot", high=high)
        _checkpoint("snapshot")
        return facts, _read_tree(root, temp), temp


def _read_tree(root: Path, temp: Path) -> list[tuple[str, str, bytes, int]]:
    base = root / ".taskmaster"
    files: list[tuple[str, str, bytes, int]] = []
    with closing(cutover.connect_readonly(temp)) as connection:
        _verify_projection_files(root, connection)
        for entry, data, _ in cutover.projection_reads(root, connection):
            if entry["path"].startswith("local/"):
                continue
            files.append(("projection", entry["path"], data, (base / Path(*entry["path"].split("/"))).stat().st_mtime_ns))
    have = {rel for _, rel, _, _ in files}
    for name in sorted(bundles.CONFIG_FILES - have):
        path = base / name
        if path.is_file():
            files.append(("config", name, path.read_bytes(), path.stat().st_mtime_ns))
    progress = base / Path(*bundles.PROGRESS_PATH.split("/"))
    if progress.is_file():
        files.append(("progress", bundles.PROGRESS_PATH, progress.read_bytes(), progress.stat().st_mtime_ns))
    return files


def _not_migrated(root: Path, git, tip: str, files) -> dict:
    carried = {rel for _, rel, _, _ in files}
    tracked = [name[len(".taskmaster/"):] for name in untrack.tracked_listing(git, tip) if name.startswith(".taskmaster/")]
    left = sorted(name for name in tracked if name not in carried and not name.startswith("local/"))
    local_dir = root / ".taskmaster" / "local"
    local = sorted((entry.name + ("/" if entry.is_dir() else "")) for entry in local_dir.iterdir()
                   if entry.name not in LOCAL_SKIP and not entry.name.startswith(("store.db", "integrity-check.json")))
    result: dict = {}
    bundles.capped(left, "tracked_total", result, "tracked")
    bundles.capped(local, "local_total", result, "local")
    return result


def _build_commits(root: Path, git, journal: dict, pre, facts: dict, files) -> dict:
    marker = server_marker.Marker(journal["url"], journal["host_id"])
    sign = untrack.signing_enabled(git)
    commits = {name: untrack.build_branch_commit(git, name, old, marker, journal["store_id"], sign=sign)
               for name, old in journal["branches"].items()}
    facts["commits"] = commits
    facts["not_migrated"] = _not_migrated(root, git, journal["branches"][pre.current], files)
    _stage(root, journal, "commits_built", commits=commits)
    _checkpoint("commits_built")
    return commits


def _write_bundle(root: Path, journal: dict, out: Path, facts: dict, files, temp: Path, commits: dict) -> dict:
    manifest = {"created": _now(), "source_build": _build(), "store_id": journal["store_id"],
                "schema_version": facts["schema"], "high_seq": journal["high"], "exported_through": journal["high"],
                "marker": {"url": journal["url"], "host_id": journal["host_id"]},
                "current_branch": journal["current_branch"], "commits": commits, "counts": facts["counts"],
                "team_mode": facts["team_mode"], "not_migrated": facts["not_migrated"]}
    bundles.capped(facts["claims"], "claims_total", manifest, "claims")
    partial = out.with_name(f"{out.name}.{os.getpid()}-{time.monotonic_ns()}.partial")       # this invocation's own
    written = bundles.write_bundle(partial, manifest, temp, files)                            # exclusive create
    try:
        bundles.open_bundle(partial).close()
        try:
            os.link(partial, out)                                                             # never replaces a late --out
        except FileExistsError:
            raise MigrationRefused(f"{out} appeared while the pack ran; choose a new --out path") from None
        except OSError:
            if out.exists():
                raise MigrationRefused(f"{out} appeared while the pack ran; choose a new --out path") from None
            os.replace(partial, out)
    finally:
        partial.unlink(missing_ok=True)
    temp.unlink(missing_ok=True)
    journal["facts"] = {key: facts[key] for key in ("schema", "counts", "claims", "team_mode", "not_migrated")}
    return written


def _build() -> dict:
    from taskmaster.coordinator.protocol import build_identity
    return build_identity()


def _move_refs(root: Path, git, journal: dict, pre) -> dict:
    """Step 13 (design §7.2): the `--branch` refs first, the current branch last, then the idempotent sync."""
    _stage(root, journal, "refs")
    _checkpoint("refs_started")
    current = journal["current_branch"]
    order = [name for name in journal["branches"] if name != current] + [current]
    for name in order:
        old, new = journal["branches"][name], journal["commits"][name]
        tip = untrack.tip(git, name)
        if tip == new:
            continue
        if tip != old:
            raise MigrationFailed(f"{name} moved since the pack; resolve it by hand, or `--undo --not-pushed`")
        try:
            untrack.move_ref(git, name, new, old)
        except untrack.LostRace as exc:
            raise MigrationFailed(str(exc)) from None
        _checkpoint(f"ref_moved:{name}")
    untrack.sync_index(git)
    _checkpoint("index_synced")
    untrack.sync_gitignore(git)
    _checkpoint("gitignore_synced")
    report = _pack_report(root, git, journal)
    _stage(root, journal, "done", report=report)
    return report


def _tracking_elsewhere(git, journal: dict) -> dict:
    handled = set(journal["branches"])
    local, remote = [], []
    for name in untrack.local_branches(git):
        tip = untrack.tip(git, name)
        if name not in handled and tip and untrack.tracked_listing(git, tip):
            local.append(name)
    for ref in untrack.remote_branches(git):
        code, tip = git.text("rev-parse", "--verify", "-q", f"{ref}^{{commit}}")
        if code == 0 and untrack.tracked_listing(git, tip):
            remote.append(ref)
    return {"local": local, "remote": remote}


def _pack_report(root: Path, git, journal: dict) -> dict:
    facts = journal["facts"]
    upstream_warning = _upstream_check(git, journal["current_branch"], journal["branches"][journal["current_branch"]],
                                       strict=False)
    claims = facts["claims"]
    latest = max((claim["expires_at"] for claim in claims if claim.get("expires_at")), default=None)
    out = Path(journal["out"])
    return {
        "state": "packed", "bundle": str(out), "bundle_sha256": journal["bundle_sha256"],
        "store_id": journal["store_id"], "schema_version": facts["schema"], "high_seq": journal["high"],
        "counts": facts["counts"], "team_mode": facts["team_mode"],
        "branches": {name: {"old": journal["branches"][name], "new": journal["commits"][name]}
                     for name in journal["branches"]},
        "still_tracking": _tracking_elsewhere(git, journal), "not_migrated": facts["not_migrated"],
        "claims": claims[:bundles.LIST_CAP], "claims_latest_expiry": latest, "warnings": [w for w in [upstream_warning] if w],
        "push": "git push <remote> " + " ".join(journal["branches"]),
        "host": f"scp {out.name} <host>:  then, in the host clone after `git pull --ff-only`:  "
                f"taskmaster serve init --root <host clone> --from {out.name}",
        "single_use": SINGLE_USE, "laptop_warning": LAPTOP_WARNING, "rollout_rule": rollout_rule(),
        "push_rejected_rule": PUSH_REJECTED_RULE,
    }


# -- undo --------------------------------------------------------------------------------------------

def undo(root, *, not_pushed: bool, record=None) -> dict:
    """`pack --undo --not-pushed` (design §6.5): refs back to their old tips, index and `.gitignore` as before, marker
    and latch removed, files under `.taskmaster/` untouched."""
    root = _project(root)
    if not not_pushed:
        raise MigrationRefused(f"--undo needs --not-pushed: your statement that no pack commit has left this machine. "
                               f"{UNDO_WARNING}")
    journal = load_journal(root, PACK_JOURNAL)
    if not _is_active(journal) and not (journal and journal.get("stage") == "done"):
        raise MigrationRefused("there is no pack to undo")
    git = untrack.git_for(root, record=record)
    commits = journal.get("commits") or {}
    for new in commits.values():
        _, contained = git.text("for-each-ref", "--format=%(refname)", "--contains", new, "refs/remotes/")
        if contained:
            raise MigrationRefused("already pushed (a remote-tracking ref contains the pack commit): revert the commit "
                                   "with `git revert` and run `taskmaster serve marker clear-latch` in every clone instead")
    current = untrack.current_branch(git)
    if current != journal["current_branch"]:
        raise MigrationRefused(f"HEAD is on {current}; check out {journal['current_branch']} first")
    moved = []
    for name, old in journal["branches"].items():
        tip = untrack.tip(git, name)
        new = commits.get(name)
        if tip == new and new is not None and new != old:
            moved.append(name)
        elif tip != old:
            raise MigrationRefused(f"{name} has commits on top of the pack commit (or moved); resolve that by hand")
    lease = _acquire_lease(root)
    try:
        for name in moved:
            untrack.move_ref(git, name, journal["branches"][name], commits[name])
        owned_ignore = _gitignore_owned_for_undo(git, root, journal)
        untrack.sync_index(git)
        if owned_ignore and journal.get("gitignore_before_sha256") is None:
            (root / ".gitignore").unlink(missing_ok=True)            # the pack created it; the restored HEAD has none
        elif owned_ignore:
            untrack.sync_gitignore(git)
        _clean_temp_copies(root)
        if sha256_path(server_marker.marker_path(root)) == journal.get("marker_sha256"):
            server_marker.marker_path(root).unlink()
        server_marker.clear_latch(root)
        _stage(root, journal, "undone")
    finally:
        lease.release()
    return {"state": "undone", "bundle": journal.get("out"), "bundle_left_in_place": True}


def _gitignore_owned_for_undo(git, root: Path, journal: dict) -> bool:
    """Rewrite `.gitignore` only when it is the pack's content; if it already matches its pre-pack bytes, leave it."""
    working = sha256_path(root / ".gitignore")
    if working is None or working == journal.get("gitignore_before_sha256"):
        return False
    probe = dict(journal, stage="refs")
    return _gitignore_owned(git, root, probe)


# -- init --from (the host clone) --------------------------------------------------------------------

HOST_NEXT_STEPS = ("taskmaster serve token; taskmaster serve add-user <handle> for each teammate, and for the operator's own "
                   "laptop agent with --role operator; systemctl start taskmaster; taskmaster serve export status")


def _gitignore_warning(git) -> list[str]:
    code, _ = git.text("check-ignore", "-q", "--", ".taskmaster/backlog.yaml")
    return [] if code == 0 else [".taskmaster/backlog.yaml is not ignored in this clone: check .gitignore"]


def _host_has_content(root: Path) -> bool:
    """A store or backlog already on the host (H6): local/store.db*, or any file under .taskmaster/ outside local/serve/."""
    base = root / ".taskmaster"
    if not base.is_dir():
        return False
    for item in base.rglob("*"):
        rel = item.relative_to(base).as_posix()
        if not item.is_file() or rel == "local/serve" or rel.startswith("local/serve/"):
            continue
        return True
    return False


def _marker_at_head(git) -> server_marker.Marker | None:
    code, blob = git.text("rev-parse", "--verify", "-q", "HEAD:" + untrack.MARKER)
    if code != 0 or not untrack.HEX.fullmatch(blob):
        return None
    try:
        return server_marker.parse(untrack.blob(git, blob))
    except server_marker.ServerConfigError:
        return None


def preflight_init(root: Path, bundle_file: Path, *, export_branch: str, export_push: str | None, no_export: bool,
                   journal: dict | None, record=None):
    """Design §8.1, H1-H9. Returns `(git, bundle, settings)`; the caller closes the bundle. Reads only."""
    git = untrack.git_for(root, record=record)
    problem = _root_kind_refusal(git, root)
    if problem:
        raise MigrationRefused(problem)
    try:
        opened = bundles.open_bundle(bundle_file)
    except bundles.BundleRefused as exc:
        raise MigrationRefused(str(exc)) from None
    try:
        digest = sha256_path(bundle_file)
        if journal is not None and journal.get("bundle_sha256") != digest:
            raise MigrationRefused("this clone has a partial init for a different bundle; delete this host clone, clone "
                                   "again and run init from the start")
        manifest = opened.manifest
        want = server_marker.Marker(manifest["marker"]["url"], manifest["marker"]["host_id"])
        head = _marker_at_head(git)
        pull = "pull the commit `pack` made (push it from the laptop first), then run init again"
        if head is None or (head.url, head.host_id) != (want.url, want.host_id):
            raise MigrationRefused(f"HEAD has no matching {untrack.MARKER}: {pull}")
        working = server_marker.read_marker(root)
        if working is None or (working.url, working.host_id) != (head.url, head.host_id):
            raise MigrationRefused(f"the working-tree {untrack.MARKER} differs from HEAD's: {pull}")
        new = manifest["commits"][manifest["current_branch"]]
        if git.text("merge-base", "--is-ancestor", new, "HEAD")[0] != 0:
            raise MigrationRefused(f"the pack commit {new[:12]} is not in this clone's HEAD: {pull}")
        if untrack.index_tracks_taskmaster(git) or untrack.head_tracks_taskmaster(root):
            raise MigrationRefused("this clone's HEAD still tracks .taskmaster/: pull the pack commit "
                                   "(see docs/runbooks/team-server-migration.md#host-head)")
        if journal is None and _host_has_content(root):
            raise MigrationRefused("already initialized: this clone has a store or backlog")
        known = server_fence.read_host_id(root)
        if known is not None and known != want.host_id:
            raise MigrationRefused("this clone's host-id differs from the bundle's: it belongs to another server")
        settings = se.Settings(root, export_branch, se.DEFAULT_INTERVAL, export_push)
        if not no_export:
            try:
                se.check_settings(settings, record=record)
            except se.ExportRefused as exc:
                raise MigrationRefused(str(exc)) from None
            if journal is None and untrack.tip(git, export_branch) is not None:
                raise MigrationRefused(f"{export_branch} already exists; a restore uses `serve export restore`, "
                                       "never init")
        return git, opened, settings
    except BaseException:
        opened.close()
        raise


def _staging(root: Path) -> Path:
    return untrack.init_directory(root) / "staging"


def _stage_bundle(root: Path, opened) -> Path:
    staging = _staging(root)
    if staging.exists():
        shutil.rmtree(staging)                    # init's own directory
    opened.extract(staging)
    manifest = opened.manifest
    facts = store_facts(staging / "store.db", expect_store=manifest["store_id"], deep=True, root=root)
    if facts["high"] != manifest["high_seq"] or facts["watermark"] != manifest["exported_through"]:
        raise MigrationRefused("the bundle's store does not match its manifest (seq or export watermark)")
    carried = {e["path"]: e for e in manifest["tree"] if e["role"] == "projection"}
    with closing(cutover.connect_readonly(staging / "store.db")) as connection:
        rows = dict(connection.execute("SELECT file, content_hash FROM projection WHERE file NOT LIKE 'local/%'"))
    if set(rows) != set(carried):
        raise MigrationRefused("the bundle's projection files do not match the store's projection record")
    for rel, entry in carried.items():
        data = (staging / "tree" / Path(*rel.split("/"))).read_bytes()
        if not native_projection.matches_record(data, rows[rel]):
            raise MigrationRefused(f"the bundle's copy of {rel} does not match the store's record")
    return staging


def _place(staging: Path, root: Path, entry: dict) -> None:
    source = staging / "tree" / Path(*entry["path"].split("/"))
    target = root / ".taskmaster" / Path(*entry["path"].split("/"))
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)
    except FileExistsError:
        if target.read_bytes() != source.read_bytes():
            raise MigrationFailed(f".taskmaster/{entry['path']} exists with different bytes; nothing more is done") from None
        return
    try:
        os.utime(target, ns=(entry["mtime_ns"], entry["mtime_ns"]))
    except OSError:
        pass


def _refuse_links(root: Path, manifest: dict) -> None:
    """No destination ancestor under `.taskmaster/` may be a symlink or junction, or resolve outside the clone."""
    base = root / ".taskmaster"
    inside = Path(os.path.realpath(root))
    names = [e["path"] for e in manifest["tree"]] + ["local/store.db"]
    for rel in names:
        here = base
        for part in ("", *rel.split("/")[:-1]):
            here = here / part if part else here
            if os.path.lexists(here) and (_is_link(here) or not Path(os.path.realpath(here)).is_relative_to(inside)):
                raise MigrationRefused(f"{here} is a link or leaves the clone; init never writes through links")


def _publish(root: Path, staging: Path, manifest: dict) -> None:
    _refuse_links(root, manifest)
    for entry in manifest["tree"]:
        _place(staging, root, entry)
    try:
        server_fence.ensure_host_id(root, value=manifest["marker"]["host_id"])
    except ValueError as exc:
        raise MigrationRefused(str(exc)) from None
    target = root / ".taskmaster" / "local" / "store.db"
    try:
        os.link(staging / "store.db", target)                  # the store, last: its presence means "published"
    except FileExistsError:
        if sha256_path(target) != manifest["store"]["sha256"]:
            raise MigrationFailed("local/store.db exists and is not the bundle's store; nothing more is done") from None


def init(root, *, bundle, export_branch: str = se.DEFAULT_BRANCH, export_push: str | None = None,
         no_export: bool = False, flush_timeout: float = 120.0, record=None) -> dict:
    """Install a pack bundle on the host clone: stage, publish (store last), verify, export, latch. Design §8."""
    root = Path(root).resolve(strict=True) if Path(root).exists() else _missing(root)
    (root / ".taskmaster").mkdir(exist_ok=True)
    private_directory_at(root / ".taskmaster" / "local")                        # owner-only, like serve/
    bundle = Path(bundle)
    journal = load_journal(root, INIT_JOURNAL)
    git, opened, settings = preflight_init(root, bundle, export_branch=export_branch, export_push=export_push,
                                           no_export=no_export, journal=journal, record=record)
    try:
        if journal is not None and journal.get("stage") == "done":
            return journal["report"]
        lease = _acquire_lease(root)
        try:
            return _init_locked(root, lease, git, opened, settings, bundle, journal, no_export=no_export,
                                flush_timeout=flush_timeout)
        finally:
            lease.release()
    finally:
        opened.close()


def _missing(root):
    raise MigrationRefused(f"{root} does not exist")


def _init_stage(root: Path, journal: dict, stage: str, **more) -> None:
    journal.update(more, stage=stage)
    save_journal(root, INIT_JOURNAL, journal)


def _init_locked(root, lease, git, opened, settings, bundle, journal, *, no_export, flush_timeout) -> dict:
    manifest = opened.manifest
    if journal is None:
        journal = {"stage": "staging", "bundle_sha256": sha256_path(bundle), "store_id": manifest["store_id"],
                   "host_id": manifest["marker"]["host_id"], "started": _now(),
                   "pre_team_through_seq": manifest["high_seq"]}      # 027 D6: text at or below this seq is pre-team
        save_journal(root, INIT_JOURNAL, journal)
        _checkpoint("init_journal")
    stage = journal["stage"]
    store = root / ".taskmaster" / "local" / "store.db"
    if stage == "published" and store.exists():
        facts = store_facts(store, expect_store=manifest["store_id"])
        if facts["high"] != manifest["high_seq"]:
            raise MigrationFailed("the store was written after a partial init; nothing more is done")
    if stage in ("staging", "staged") or (stage == "published" and not store.exists()):
        try:
            staging = _stage_bundle(root, opened)
        except (MigrationRefused, bundles.BundleRefused) as exc:
            shutil.rmtree(_staging(root), ignore_errors=True)
            raise MigrationRefused(str(exc)) from None
        _init_stage(root, journal, "staged")
        _checkpoint("init_staged")
        _publish(root, staging, manifest)
        _init_stage(root, journal, "published")
        shutil.rmtree(staging, ignore_errors=True)
        _checkpoint("init_published")
        stage = "published"
    commit = journal.get("commit")
    marker = server_marker.Marker(manifest["marker"]["url"], manifest["marker"]["host_id"])
    with se.offline_owner(root, lease=lease) as owner:
        if owner.identity["store_id"] != manifest["store_id"]:
            raise MigrationFailed("the installed store's identity differs from the bundle's")
        if stage == "published":
            result = owner.flush(manifest["high_seq"], timeout=flush_timeout)
            if result.get("state") != "exported" or result.get("notices"):
                raise MigrationFailed("the installed store's export did not settle: "
                                      + "; ".join(result.get("notices") or ["timed out"]))
            _init_stage(root, journal, "verified")
            _checkpoint("init_verified")
        if not no_export:
            se.drain(owner, timeout=flush_timeout)
            outcome = se.export_once(owner, settings, push=settings.push is not None)
            if outcome.get("state") not in ("committed", "unchanged"):
                raise MigrationFailed(f"the first export did not commit: {outcome.get('reason') or outcome.get('state')}")
            record_state = outcome.get("record") or {}
            commit = record_state.get("commit") or commit
            journal["push_state"] = outcome.get("push_state")
            _init_stage(root, journal, "exported", commit=commit)
            _checkpoint("init_exported")
    server_marker.record_latch(root, marker)
    report = _init_report(root, git, manifest, journal, settings, no_export=no_export)
    _init_stage(root, journal, "done", report=report)
    return report


def _init_report(root: Path, git, manifest: dict, journal: dict, settings, *, no_export: bool) -> dict:
    claims = manifest.get("claims") or []
    latest = max((claim["expires_at"] for claim in claims if claim.get("expires_at")), default=None)
    return {
        "state": "installed", "store_id": manifest["store_id"], "schema_version": manifest["schema_version"],
        "high_seq": manifest["high_seq"], "counts": manifest["counts"], "team_mode": manifest["team_mode"],
        "source_build": manifest.get("source_build"),
        "export": None if no_export else {"branch": settings.branch, "commit": journal.get("commit"),
                                          "push": settings.push, "push_state": journal.get("push_state")},
        "claims": claims, "claims_latest_expiry": latest, "not_migrated": manifest.get("not_migrated", {}),
        "warnings": _gitignore_warning(git), "host_pull_rule": HOST_PULL_RULE, "next_steps": HOST_NEXT_STEPS,
        "single_use": SINGLE_USE, "rollout_rule": rollout_rule(),
    }


# -- text report -------------------------------------------------------------------------------------

RULE_KEYS = ("rollout_rule", "push_rejected_rule", "host_pull_rule", "single_use", "laptop_warning", "next_steps")


def render_report(report: dict) -> str:
    """The report as text: scalars as `key: value`, structures as indented JSON, the rules verbatim at the end."""
    lines = []
    for key, value in report.items():
        if key in RULE_KEYS:
            continue
        if isinstance(value, (dict, list)):
            lines.append(f"{key}: " + json.dumps(value, indent=1, sort_keys=True))
        else:
            lines.append(f"{key}: {value}")
    for key in RULE_KEYS:
        if report.get(key):
            lines += ["", report[key]]
    return "\n".join(lines)
