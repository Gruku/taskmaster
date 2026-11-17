# User intent: the team server's backup and history (team-server-013): commit the store's verified `.taskmaster/` export
# and stored documents to their own branch on a schedule and push it fast-forward to the operator's backup remote,
# never touching code branches, the working tree, the index or secrets. Design: docs/specs/2026-11-01-team-server-013-state-export-design.md
"""State export: plumbing commits to `taskmaster-state` (temporary index, commit-tree, CAS update-ref).

Every Git call goes through `Git.run`, which refuses any argv outside the §4.4 allowlist before a process starts.
The guards, the verified read and the ref update all run under the coordinator's `publication` lock, so no
managed-Git operation can interleave (I1); push and maintenance run outside it.
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import logging
import os
import random
import re
import stat
import threading
import time
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from taskmaster.coordinator import git as managed
from taskmaster.coordinator import scopes

from .gitrun import AllowlistedGit

LOG = logging.getLogger("taskmaster.serve.export")

DEFAULT_BRANCH = "taskmaster-state"
DEFAULT_INTERVAL = 900
MIN_INTERVAL, MAX_INTERVAL = 60, 86400
FIRST_DELAY = 60
GIT_TIMEOUT, PUSH_TIMEOUT, MAINTENANCE_TIMEOUT = 60, 120, 600
MAINTENANCE_EVERY = 86400
HELD_ALARM = 86400
BUSY_STREAK, BUSY_ACQUIRE = 3, 60.0
WARN_EVERY = 3600.0
KEPT = 20
EXPORT_DIR, STATE_FILE, HOOKS_DIR = "export", "export.json", "no-hooks"
DOC_DIR = "external-documents"
MANIFEST = f"{DOC_DIR}/index.json"
ZERO = "0" * 40
FORMAT_VERSION = "1"
TRAILER_EXPORT, TRAILER_STORE, TRAILER_SEQ = "Taskmaster-Export", "Taskmaster-Store", "Taskmaster-Seq"
TRAILER_BUILD, TRAILER_HELD, TRAILER_ADOPTED = "Taskmaster-Build", "Taskmaster-Held", "Taskmaster-Adopted-From"
AUTHOR = {"GIT_AUTHOR_NAME": "Taskmaster export", "GIT_AUTHOR_EMAIL": "taskmaster-export@localhost.invalid",
          "GIT_COMMITTER_NAME": "Taskmaster export", "GIT_COMMITTER_EMAIL": "taskmaster-export@localhost.invalid"}
SSH_ENV = ("GIT_SSH_COMMAND", "GIT_SSH", "GIT_SSH_VARIANT")
DEFAULT_BRANCH_NAMES = ("main", "master")
# Top-level names an export commit may hold (guard 5's shape test), besides the current generation's own.
CLONE_DIR = "clone-fields"
BRANCHES = f"{CLONE_DIR}/branches.json"
STATIC_TOP = frozenset({"backlog.yaml", "project.yaml", DOC_DIR, CLONE_DIR, "tasks", "epics", "phases", "bugs", "issues",
                        "handovers", "decisions", "ideas", "notes", "areas", "trackers", "integrations", "users"})
WALK_FORMAT = ("--format=%H%x00%P%x00%T%x00%(trailers:key=Taskmaster-Export,valueonly,separator=%x2C)"
               "%x00%(trailers:key=Taskmaster-Store,valueonly,separator=%x2C)")
REJECTED = ("[rejected]", "non-fast-forward", "fetch first")

HEX = re.compile(r"[0-9a-f]{40}")
RANGE = re.compile(r"[0-9a-f]{40}(?:\.\.[0-9a-f]{40})?")
# G4: tree paths that are never committed. G4/RR-I2: document source paths that are never exported.
_TREE_DENY = re.compile(r"operator\.token|principals\..*|discovery\.json|export\.json|serve\.log|store\.db.*"
                        r"|.*\.token|.*\.key", re.IGNORECASE)
_SOURCE_DENY = re.compile(_TREE_DENY.pattern + r"|.*\.(?:pem|p12|pfx|jks|keystore|kdbx|env)"
                          r"|id_(?:rsa|dsa|ecdsa|ed25519).*|credentials(?:\..*)?|secrets\..*", re.IGNORECASE)
_ARMOUR = re.compile(r"-----BEGIN [A-Z0-9 ]*PRIVATE KEY-----")
_URL = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+")
_SCP = re.compile(r"[^\s@:'\"/]+@[^\s:'\"]+:[^\s'\"]+")
_DRIVE = re.compile(r"^[A-Za-z]:")


DOC_RETAKES = 2


class ExportRefused(Exception):
    """Configuration or a guard refuses: nothing was written."""


class ExportError(Exception):
    """A Git step failed; objects may have been written, the ref was not moved."""


@dataclass(frozen=True)
class Settings:
    root: Path
    branch: str = DEFAULT_BRANCH
    interval: int = DEFAULT_INTERVAL
    push: str | None = None

    @property
    def ref(self) -> str:
        return f"refs/heads/{self.branch}"


# ── Pure policy (G4, RR-I2, G7) ──────────────────────────────────────────────


def tree_path_refusal(rel) -> str | None:
    """Why a tree path is never committed, or None (G4)."""
    if not isinstance(rel, str) or not rel or any(c in rel for c in "\n\r\0\\") or rel.startswith('"'):
        return "unsafe characters"
    parts = rel.split("/")
    if any(part in ("", ".", "..") for part in parts):
        return "unsafe path"
    if parts[0].lower() == "local":
        return "local state"
    for part in parts:
        if part.startswith("."):
            return "dot component"
        if part.lower() == "git~1":
            return "git alias"
    if _TREE_DENY.fullmatch(parts[-1]):
        return "secret name"
    return None


def source_path_refusal(path) -> str | None:
    """Why a document imported from `path` is never exported, or None (RR-I2)."""
    if not isinstance(path, str) or not path.strip() or "\0" in path:
        return "no usable path"
    value = path.replace("\\", "/")
    while value.startswith("./"):
        value = value[2:]
    if value.startswith("/") or _DRIVE.match(value):
        return "outside the project"
    parts = [part for part in value.split("/") if part not in ("", ".")]
    if not parts or ".." in parts:
        return "outside the project"
    for part in parts:
        if part.startswith("."):
            return "dot path"
        if part.lower() in ("local", "git~1"):
            return "local state"
    if _SOURCE_DENY.fullmatch(parts[-1]):
        return "secret name"
    return None


def resolved_refusal(root: Path, path) -> str | None:
    """The same rule on the path the link resolves to (an operator path import follows symlinks)."""
    first = source_path_refusal(path)
    if first is not None:
        return first
    base = os.path.realpath(root)
    target = os.path.realpath(os.path.join(base, path.replace("\\", "/")))
    try:
        rel = os.path.relpath(target, base)
    except ValueError:                                   # another drive on Windows
        return "resolves outside the project"
    if rel == os.curdir or rel.split(os.sep)[0] == os.pardir:
        return "resolves outside the project"
    refusal = source_path_refusal(rel.replace(os.sep, "/"))
    return None if refusal is None else f"resolves to a {refusal}"


def body_refusal(body) -> str | None:
    return "private key in the body" if isinstance(body, str) and _ARMOUR.search(body) else None


_SECRETS: dict[str, tuple[str, ...]] = {}


def learn_remote_secrets(remote, url) -> None:
    """Remember the userinfo/password literals of a remote's URL so redact() can strip them from bare SSH output."""
    from urllib.parse import urlsplit
    url = (url or "").strip()
    found: list[str] = []
    if "://" in url:
        try:
            userinfo = url.split("://", 1)[1].split("/", 1)[0].rpartition("@")[0]
            if userinfo:
                found.append(userinfo + "@")
            found += [x for x in (urlsplit(url).password,) if x]
        except ValueError:
            pass
    elif "@" in url.split(":", 1)[0]:
        found.append(url.split(":", 1)[0].rpartition("@")[0] + "@")
    _SECRETS[remote] = tuple(sorted({x for x in found if len(x) > 1}, key=len, reverse=True))


def redact(text, remote) -> str:
    """No URL and no credentials in anything the exporter logs, stores or raises (G7)."""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    marker = f"<remote:{remote or '?'}>"
    text = text or ""
    for secret in _SECRETS.get(remote, ()):      # the configured URL's userinfo, wherever SSH echoes it bare
        text = text.replace(secret, marker)
    text = _URL.sub(marker, text)
    text = _SCP.sub(marker, text)
    return " ".join(text.split())[:500]


def blob_id(content: bytes) -> str:
    return hashlib.sha1(b"blob %d\0" % len(content) + content).hexdigest()


# ── Files under local/serve ─────────────────────────────────────────────────


def serve_dir_path(root: Path) -> Path:
    return Path(root) / ".taskmaster" / "local" / "serve"


def state_path(root: Path) -> Path:
    return serve_dir_path(root) / STATE_FILE


def export_directory(root: Path) -> Path:
    """`local/serve/export/`, private like its parent (the Windows ACL is inherited from local/serve)."""
    from .auth import serve_directory
    base = serve_directory(root)
    path = base / EXPORT_DIR
    path.mkdir(mode=0o700, exist_ok=True)
    if path.is_symlink() or getattr(path, "is_junction", lambda: False)() or path.resolve().parent != base.resolve():
        raise ExportRefused("local/serve/export must be a plain directory inside local/serve")
    if os.name != "nt":
        if path.stat().st_uid != os.getuid():
            raise ExportRefused("local/serve/export must be owned by the current user")
        path.chmod(0o700)
    hooks = path / HOOKS_DIR
    hooks.mkdir(mode=0o700, exist_ok=True)
    return path


def load_state(root: Path) -> dict:
    try:
        value = json.loads(state_path(root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


def save_state(root: Path, state: dict) -> None:
    from .principals import replace_private
    data = (json.dumps(state, indent=1, sort_keys=True) + "\n").encode("utf-8")
    replace_private(state_path(root), data)


def sweep(directory: Path) -> None:
    """Remove the exporter's own stray temp files: exact name patterns, regular files only, no recursion."""
    with contextlib.suppress(OSError):
        for entry in os.scandir(directory):
            if not re.fullmatch(r"(?:index|doc)-\d+-\d+\.tmp", entry.name):
                continue
            try:
                info = os.lstat(entry.path)
            except OSError:
                continue
            reparse = getattr(info, "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if stat.S_ISREG(info.st_mode) and not reparse:
                with contextlib.suppress(OSError):
                    os.unlink(entry.path)


# ── Git runner with the argv allowlist (§4.4) ───────────────────────────────


class Git(AllowlistedGit):
    """Every exporter Git call. `record`, when a list, receives (argv, env) for the allowlist spy test."""

    refused_error, failed_error = ExportRefused, ExportError
    ENV_KEYS = frozenset({"GIT_INDEX_FILE", "GCM_INTERACTIVE", *AUTHOR, *SSH_ENV})

    def __init__(self, root: Path, settings: Settings, directory: Path, *, record=None):
        super().__init__(root, directory, record=record)
        self.settings = settings
        self.ref, self.remote = settings.ref, settings.push
        self.config_keys = {"core.sshCommand", "init.defaultBranch"}
        if self.remote:
            self.config_keys.add(f"remote.{self.remote}.mirror")

    @property
    def tracking_ref(self) -> str:
        return f"refs/remotes/{self.remote}/{self.ref.removeprefix('refs/heads/')}"

    def _rev(self, value) -> bool:
        return (value == self.ref or bool(HEX.fullmatch(value)) or value == f"{self.ref}^{{commit}}"
                or (value.endswith("^{tree}") and HEX.fullmatch(value[:-7]) is not None))

    def _own_index(self, value) -> bool:
        path = Path(value)
        return path.parent == self.directory and re.fullmatch(r"index-\d+-\d+\.tmp", path.name) is not None

    def refusal(self, args, env) -> str | None:
        if set(env) - self.ENV_KEYS:
            return f"environment {sorted(set(env) - self.ENV_KEYS)}"
        index = env.get("GIT_INDEX_FILE")
        if index is not None and not self._own_index(index):
            return "GIT_INDEX_FILE outside the export directory"
        if not args:
            return "empty argv"
        command, rest = args[0], list(args[1:])
        if command == "rev-parse":
            flags = {"--verify", "-q", "--show-toplevel", "--absolute-git-dir", "--git-common-dir",
                     "--show-object-format", "--show-ref-format"}
            revs = [item for item in rest if item not in flags]
            ok = len(revs) <= 1 and all(self._rev(item) for item in revs)
        elif command == "symbolic-ref":
            ok = rest == ["-q", self.ref] or (bool(self.remote) and rest in (
                ["-q", f"refs/remotes/{self.remote}/HEAD"], ["-q", self.tracking_ref]))
        elif command == "worktree":
            ok = rest == ["list", "--porcelain"]
        elif command == "rev-list":
            ok = len(rest) == 3 and rest[:2] == ["--first-parent", WALK_FORMAT] and RANGE.fullmatch(rest[2]) is not None
        elif command == "merge-base":
            ok = len(rest) == 3 and rest[0] == "--is-ancestor" and all(HEX.fullmatch(x) for x in rest[1:])
        elif command == "cat-file":
            ok = rest == ["--batch"]
        elif command == "ls-tree":
            ok = bool(rest) and rest[:-1] in ([], ["-r", "-z", "--full-tree"]) and HEX.fullmatch(rest[-1]) is not None
        elif command == "for-each-ref":
            ok = rest == ["--format=%(refname)", "refs/heads/"]
        elif command == "check-ref-format":
            ok = rest == [self.ref]
        elif command == "remote":
            ok = bool(self.remote) and rest == ["get-url", "--push", self.remote]
        elif command == "config":
            ok = (len(rest) == 2 and rest[0] == "--get" and rest[1] in self.config_keys) or (
                bool(self.remote) and rest == ["--get-all", f"remote.{self.remote}.fetch"])
        elif command == "hash-object":
            ok = rest == ["-w", "--no-filters", "--stdin-paths"]
        elif command == "update-index":
            ok = rest == ["-z", "--add", "--index-info"] and index is not None
        elif command == "write-tree":
            ok = rest == [] and index is not None
        elif command == "commit-tree":
            ok = (len(rest) >= 4 and rest[0] == "--no-gpg-sign" and HEX.fullmatch(rest[1]) is not None
                  and (rest[2:] == ["-F", "-"] or (len(rest) == 6 and rest[2] == "-p" and HEX.fullmatch(rest[3]) is not None
                                                   and rest[4:] == ["-F", "-"])))
        elif command == "update-ref":
            ok = (len(rest) == 6 and rest[:2] == ["--no-deref", "-m"] and rest[3] == self.ref
                  and all(HEX.fullmatch(x) for x in rest[4:]))
        elif command == "push":
            ok = bool(self.remote) and rest == ["--no-verify", "--porcelain", self.remote, f"{self.ref}:{self.ref}"]
        elif command == "maintenance":
            ok = rest == ["run", "--task=loose-objects", "--quiet"]
        else:
            ok = False
        return None if ok else f"git {command} {' '.join(rest)[:120]}"

    PERMITTED_CONFIG = frozenset({"push.followTags=false", "push.recurseSubmodules=no",
                                  "core.sshCommand=ssh -o BatchMode=yes"})


# ── Repository reads ────────────────────────────────────────────────────────


def _tip(git: Git) -> str | None:
    code, out = git.text("rev-parse", "--verify", "-q", git.ref)
    return out if code == 0 and HEX.fullmatch(out) else None


def _tree_map(git: Git, commit: str | None) -> dict[str, str]:
    if commit is None:
        return {}
    code, out, err = git.run("ls-tree", "-r", "-z", "--full-tree", commit)
    if code:
        raise ExportError(f"cannot list the export tree: {redact(err, git.remote)}")
    found = {}
    for entry in out.split(b"\0"):
        if not entry:
            continue
        meta, _, path = entry.partition(b"\t")
        mode, kind, sha = meta.decode().split()
        if kind == "blob":
            found[path.decode("utf-8", "surrogateescape")] = sha
    return found


def _parse_tree(raw: bytes) -> list[str]:
    names, at = [], 0
    while at < len(raw):
        space = raw.index(b" ", at)
        nul = raw.index(b"\0", space)
        names.append(raw[space + 1:nul].decode("utf-8", "surrogateescape"))
        at = nul + 21
    return names


def _top_names(git: Git, trees) -> dict[str, list[str]]:
    trees = list(dict.fromkeys(trees))
    if not trees:
        return {}
    code, out, err = git.run("cat-file", "--batch", stdin=("\n".join(trees) + "\n").encode())
    if code:
        raise ExportError(f"cannot read export trees: {redact(err, git.remote)}")
    found, at = {}, 0
    for tree in trees:
        header_end = out.index(b"\n", at)
        header = out[at:header_end].decode().split()
        if len(header) != 3:                       # "<sha> missing"
            found[tree], at = [], header_end + 1
            continue
        sha, kind, size = header
        body = out[header_end + 1:header_end + 1 + int(size)]
        found[tree] = _parse_tree(body) if kind == "tree" else []
        at = header_end + 1 + int(size) + 1
    return found


@dataclass
class Commit:
    sha: str
    parents: list
    tree: str
    export: str
    store: str


def _walk(git: Git, spec: str) -> list[Commit]:
    code, out, err = git.run("rev-list", "--first-parent", WALK_FORMAT, spec, timeout=300)
    if code:
        raise ExportError(f"cannot walk the export history: {redact(err, git.remote)}")
    commits = []
    for line in out.decode("utf-8", "replace").splitlines():
        if line.startswith("commit ") or "\0" not in line:
            continue
        sha, parents, tree, export, store = (line.split("\0") + [""] * 5)[:5]
        commits.append(Commit(sha, parents.split(), tree, export.strip(), store.strip()))
    return commits


def _shaped(commit: Commit, tops: list[str], names) -> bool:
    return (len(commit.parents) <= 1 and commit.export == FORMAT_VERSION and ".taskmaster" not in tops
            and all(name in names for name in tops))


def chain_is_export(git: Git, tip: str, names) -> tuple[bool, list[Commit]]:
    commits = _walk(git, tip)
    tops = _top_names(git, [c.tree for c in commits])
    return all(_shaped(c, tops.get(c.tree, []), names) for c in commits), commits


def is_export_branch(root: Path, ref: str, *, names=None) -> bool:
    """For 014's untrack walk: True when `ref`'s first-parent history is entirely export-shaped (guard 5)."""
    branch = ref.removeprefix("refs/heads/")
    git = Git(root, Settings(Path(root), branch=branch), export_directory(root))
    tip = _tip(git)
    if tip is None:
        return False
    return chain_is_export(git, tip, STATIC_TOP | set(names or ()))[0]


# ── Start-time checks ───────────────────────────────────────────────────────


def is_linked_worktree(root: Path) -> bool:
    repo = managed.repository(root)
    return os.path.normcase(str(Path(repo["git_dir"]).resolve())) != os.path.normcase(str(Path(repo["common_dir"])))


def default_branch_names(git: Git) -> set[str]:
    names = set(DEFAULT_BRANCH_NAMES)
    code, value = git.text("config", "--get", "init.defaultBranch")
    if code == 0 and value:
        names.add(value)
    if git.remote:
        code, value = git.text("symbolic-ref", "-q", f"refs/remotes/{git.remote}/HEAD")
        if code == 0 and value.startswith(f"refs/remotes/{git.remote}/"):
            names.add(value.removeprefix(f"refs/remotes/{git.remote}/"))
    return names


def branch_refusal(git: Git, branch: str) -> str | None:
    """Why `branch` may not be the export branch by name (gate I1).

    Loose refs on NTFS and APFS are case-insensitive while packed refs are not: `refs/heads/Master` with no
    such ref would be created as a loose file that `master` then resolves to. So any existing branch, loose or
    packed, that matches case-insensitively but not exactly is refused, and default names are compared folded."""
    folded = branch.casefold()
    if folded in {name.casefold() for name in default_branch_names(git)}:
        return f"{branch} is a default branch; choose a dedicated branch"
    code, out = git.text("for-each-ref", "--format=%(refname)", "refs/heads/")
    if code:
        return "cannot list the local branches"
    for ref in out.splitlines():
        name = ref.removeprefix("refs/heads/")
        if name != branch and name.casefold() == folded:
            return (f"{branch} differs only in case from the existing branch {name}; on a case-insensitive "
                    "filesystem writing it would move that branch")
    return None


def check_settings(settings: Settings, *, record=None) -> Git:
    """Validate the operator's export settings against the host clone; ExportRefused names the problem."""
    from taskmaster.project import valid_branch_name, valid_remote_name
    root = Path(settings.root)
    if not isinstance(settings.interval, int) or isinstance(settings.interval, bool) or \
            not (settings.interval == 0 or MIN_INTERVAL <= settings.interval <= MAX_INTERVAL):
        raise ExportRefused(f"--export-interval must be 0 or from {MIN_INTERVAL} to {MAX_INTERVAL}")
    if not valid_branch_name(settings.branch) or settings.branch.lower() in ("head",) or "@" in settings.branch:
        raise ExportRefused(f"--export-branch {settings.branch!r} is not a valid branch name")
    if settings.push is not None and not valid_remote_name(settings.push):
        raise ExportRefused(f"--export-push {settings.push!r} is not a valid remote name")
    try:
        managed.repository(root)
    except managed.GitRefused as exc:
        raise ExportRefused(f"{root} is not the top level of a Git checkout ({exc})") from None
    git = Git(root, settings, export_directory(root), record=record)
    if git.run("check-ref-format", settings.ref)[0] != 0:
        raise ExportRefused(f"--export-branch {settings.branch!r} is not a valid branch name")
    _, objects = git.text("rev-parse", "--show-object-format")
    if objects != "sha1":
        raise ExportRefused(f"the repository uses the {objects} object format; the export supports sha1 only")
    _, refs = git.text("rev-parse", "--show-ref-format")
    if refs == "reftable":
        raise ExportRefused("the repository uses the reftable ref format; the export supports files refs only")
    if settings.push is not None:
        if git.run("remote", "get-url", "--push", settings.push)[0] != 0:    # stdout (the URL) is discarded (G7)
            raise ExportRefused(f"--export-push names no configured remote: {settings.push}")
        code, mirror = git.text("config", "--get", f"remote.{settings.push}.mirror")
        if code == 0 and mirror.lower() in ("true", "yes", "on", "1"):
            raise ExportRefused(f"remote {settings.push} is a mirror remote; the export never mirror-pushes")
    refusal = branch_refusal(git, settings.branch)
    if refusal is not None:
        raise ExportRefused(f"--export-branch {refusal}")
    return git


# ── One cycle ───────────────────────────────────────────────────────────────


@dataclass
class Marks:
    high: int
    through: int
    watermark: int
    owes: bool
    docs_seq: int


class Exporter:
    """One host's export: `cycle()` runs §4.2 once and records the outcome in export.json."""

    def __init__(self, owner, settings: Settings, *, wall=time.time, record=None, halted=None, rng=None):
        self.owner, self.settings, self.wall = owner, settings, wall
        self.root = Path(settings.root)
        self.directory = export_directory(self.root)
        self.git = Git(self.root, settings, self.directory, record=record)
        self.halted = halted or (lambda: False)
        self.rng = rng or random.Random()
        self.store_id = owner.identity["store_id"]
        self.ssh_env = {key: os.environ[key] for key in SSH_ENV if os.environ.get(key)}

    # -- reads --

    def _stopping(self) -> bool:
        return self.halted() or self.owner.stopping.is_set()

    def marks(self) -> Marks:
        from taskmaster.native import projection as outbox
        from taskmaster.native_routing import progress
        with closing(self.owner._connect(readonly=True)) as connection:
            connection.execute("BEGIN")
            try:
                high = connection.execute("SELECT COALESCE(MAX(seq),0) FROM domain_events").fetchone()[0]
                docs = connection.execute("SELECT COALESCE(MAX(imported_seq),0) FROM external_documents").fetchone()[0]
                return Marks(high=int(high), through=outbox.exported_through(connection),
                             watermark=outbox.watermark(connection),
                             owes=bool(progress.owes_through(connection, int(high))), docs_seq=int(docs))
            finally:
                connection.rollback()

    def owed(self, target: int) -> tuple[bool, str | None]:
        """Whether the store still owes export through `target`, and which check says so."""
        from taskmaster.native import projection as outbox
        from taskmaster.native_routing import progress
        with closing(self.owner._connect(readonly=True)) as connection:
            connection.execute("BEGIN")
            try:
                if outbox.watermark(connection) < target:
                    return True, "export watermark"
                if progress.owes_through(connection, target):
                    return True, "PROGRESS debt"
                return False, None
            finally:
                connection.rollback()

    def store_tops(self) -> set[str]:
        with closing(self.owner._connect(readonly=True)) as connection:
            rows = connection.execute("SELECT file FROM projection WHERE file NOT LIKE 'local/%'").fetchall()
        return STATIC_TOP | {row[0].split("/", 1)[0] for row in rows}

    def documents(self, parent: dict[str, str]):
        """Pre-lock snapshot of external_documents (§4.5): ({tree path: blob}, dropped, docs_seq, written)."""
        from taskmaster.native import documents as native_documents
        from taskmaster.native.queries import Repository
        from taskmaster.taskmaster_v3 import TASK_DOC_SECTIONS
        from taskmaster.native.contracts import _identifier
        entries, manifest, dropped, payload = {}, [], [], {}
        with closing(self.owner._connect(readonly=True)) as connection, Repository(connection).snapshot() as snap:
            docs_seq = int(connection.execute("SELECT COALESCE(MAX(imported_seq),0) FROM external_documents").fetchone()[0])
            rows = connection.execute(
                "SELECT c.kind,c.public_id,d.section,d.path,d.body,d.content_hash,d.imported_seq FROM external_documents d "
                "JOIN entity_core c ON c.entity_key=d.entity_key WHERE c.deleted=0 "
                "ORDER BY c.kind,c.public_id,d.section").fetchall()
            for kind, ident, section, path, body, content_hash, seq in rows:
                label = f"{kind}/{ident}/{section}"
                try:
                    _identifier(ident, "id")
                    if kind != "task" or section not in TASK_DOC_SECTIONS:
                        raise ValueError
                except (ValueError, TypeError):
                    dropped.append({"document": label, "rule": "invalid identity"})
                    continue
                try:
                    declared = ((snap.get(kind, ident, fields=["docs"]) or {}).get("fields", {}).get("docs") or {}).get(section)
                except Exception:  # noqa: BLE001 - an unreadable task drops only its documents
                    declared = None
                rule = (resolved_refusal(self.root, path)
                        or (resolved_refusal(self.root, declared) if declared and declared != path else None)
                        or body_refusal(body))
                data = body.encode("utf-8") if isinstance(body, str) else None
                if rule is None and (data is None or hashlib.sha256(data).hexdigest() != content_hash):
                    rule = "hash mismatch"
                if rule is not None:
                    dropped.append({"document": label, "rule": rule})
                    continue
                facts = native_documents.import_origin(connection, seq)
                source = facts.get("source") if facts.get("source") in ("upload", "file") else "file"
                handle = facts.get("imported_by") if source == facts.get("source") else ""
                tree_path = f"{DOC_DIR}/{kind}/{ident}/{section}.md"
                if tree_path_refusal(tree_path) is not None:
                    dropped.append({"document": label, "rule": "invalid identity"})
                    continue
                payload[tree_path] = data
                manifest.append({"kind": kind, "id": ident, "section": section, "path": path,
                                 "sha256": content_hash, "source": source, "imported_by": handle or None,
                                 # A restored copy keeps its first import's seq (012/013 contract, gate M5).
                                 "imported_seq": facts["original_seq"] if type(facts.get("original_seq")) is int
                                 and facts["original_seq"] > 0 else int(seq)})
        if manifest:
            payload[MANIFEST] = (json.dumps(manifest, indent=1, sort_keys=True) + "\n").encode("utf-8")
        branches, refused = self.branch_rows()
        dropped.extend(refused)
        if branches:
            payload[BRANCHES] = (json.dumps(branches, indent=1, sort_keys=True) + "\n").encode("utf-8")
        for tree_path, data in payload.items():
            entries[tree_path] = blob_id(data)
        known = set(parent.values())
        self._write_blobs({path: payload[path] for path, sha in entries.items() if sha not in known}, entries)
        return entries, dropped, docs_seq

    def current_branch_mark(self) -> str:
        from taskmaster.native import clone_fields
        with closing(self.owner._connect(readonly=True)) as connection:
            return clone_fields.branch_mark(connection)

    def branch_rows(self):
        """`([row, ...], dropped)`: every branch row of a live task whose parts pass the rules that wrote them (D10)."""
        from taskmaster.native import clone_fields
        from taskmaster.native.contracts import _identifier
        from taskmaster.native_routing.hook_reads import ref_problem
        from taskmaster.team_identity import HANDLE_RE
        with closing(self.owner._connect(readonly=True)) as connection:
            rows = clone_fields.branch_rows(connection)
            self._branch_mark = clone_fields.branch_mark(connection)
        kept, dropped = [], []
        for row in rows:
            rule = tree_path_refusal(BRANCHES)
            try:
                _identifier(row["id"], "id")
                if not HANDLE_RE.fullmatch(row["handle"]):
                    rule = rule or "invalid handle"
                elif not clone_fields.valid_clone(row["clone"]):
                    rule = rule or "invalid clone"
                else:
                    rule = rule or ref_problem(row["branch"]) or body_refusal(row["branch"])
            except (ValueError, TypeError):
                rule = rule or "invalid identity"
            if rule is not None:
                dropped.append({"document": f"{CLONE_DIR}/{row['handle']}/{row['id']}", "rule": rule})
            else:
                kept.append(row)
        return kept, dropped

    def _write_blobs(self, payload: dict[str, bytes], expected: dict[str, str]) -> None:
        """One batched `hash-object --stdin-paths` over private temp files; ids checked against Python's."""
        if not payload:
            return
        temps = []
        try:
            for path, data in payload.items():
                temp = self.git.temp_name("doc")
                fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
                with os.fdopen(fd, "wb") as stream:
                    stream.write(data)
                temps.append((path, temp))
            code, out, err = self.git.run("hash-object", "-w", "--no-filters", "--stdin-paths",
                                          stdin="".join(temp.as_posix() + "\n" for _, temp in temps).encode())
            ids = out.decode().split()
            if code or len(ids) != len(temps) or any(ids[i] != expected[path] for i, (path, _) in enumerate(temps)):
                raise ExportError(f"document blobs did not hash as expected {redact(err, self.git.remote)}".strip())
        finally:
            for _, temp in temps:
                with contextlib.suppress(OSError):
                    os.unlink(temp)

    # -- guards (§4.3), called with publication held --

    def guards(self, state: dict, adopt: bool) -> tuple[str | None, str | None]:
        git = self.git
        if git.run("symbolic-ref", "-q", git.ref)[0] == 0:
            raise ExportRefused(f"{git.ref} is a symbolic ref; the export never writes through one")
        _, listing = git.text("worktree", "list", "--porcelain")
        for line in listing.splitlines():            # folded everywhere: NTFS and APFS refs ignore case
            if line.startswith("branch ") and line[7:].strip().casefold() == git.ref.casefold():
                raise ExportRefused(f"{self.settings.branch} is checked out in a worktree; check out another branch there")
        refusal = branch_refusal(git, self.settings.branch)
        if refusal is not None:
            raise ExportRefused(f"{refusal}; the export never writes it")
        tip = _tip(git)
        if tip is None:
            return None, None
        record = state.get("commit") if state.get("store_id") == self.store_id else None
        names = self.store_tops()
        if record and tip == record:
            return tip, None
        if record:
            if git.run("merge-base", "--is-ancestor", record, tip)[0] != 0:
                raise ExportRefused(f"{self.settings.branch} no longer descends from this store's last export")
            commits = _walk(git, f"{record}..{tip}")
            tops = _top_names(git, [c.tree for c in commits])
            if not all(_shaped(c, tops.get(c.tree, []), names) for c in commits):
                raise ExportRefused(f"{self.settings.branch} holds commits that are not exports since the last one")
            foreign = next((c.store for c in commits if c.store != self.store_id), None)
            if foreign is not None:
                raise ExportRefused(f"another store ({foreign}) is exporting to {self.settings.branch}; stop it "
                                    "or choose another --export-branch")
            return tip, None
        shaped, commits = chain_is_export(git, tip, names)
        if not shaped:
            raise ExportRefused(f"{self.settings.branch} is not an export branch (its history is not export commits)")
        if commits and commits[0].store != self.store_id:
            if not adopt:
                raise ExportRefused(f"another store ({commits[0].store}) is exporting to {self.settings.branch}; if this "
                                    "is a restore, adopt it once with `taskmaster serve export run --adopt`")
            return tip, commits[0].store
        return tip, None

    # -- the locked read and build (§4.2) --

    def _locked(self, state: dict, docs, adopt: bool, now: float) -> dict:
        git, owner = self.git, self.owner
        doc_entries, dropped_docs, docs_seq = docs
        tip, adopted = self.guards(state, adopt)
        marks = self.marks()
        for _ in range(DOC_RETAKES):    # the snapshot predates the lock; judge it against the locked marks
            if docs_seq == marks.docs_seq:
                break
            doc_entries, dropped_docs, docs_seq = self.documents(_tree_map(git, tip))
            marks = self.marks()
        if docs_seq != marks.docs_seq:
            return {"state": "deferred", "reason": "documents changed during the export"}
        # Gate M2: "in sync" is judged at the read, under the lock, so a catch-up after the read never vouches for
        # a commit that does not hold those writes.
        sync = self.in_sync(state, marks)
        generation, files, mismatched = managed.generation(owner, marks.through, exact=True)
        if self._stopping():
            return {"state": "stopped"}
        parent = _tree_map(git, tip)
        names = set(files)
        held_now = {m if m in names else m.split(": ", 1)[0] for m in mismatched}
        entries, dropped, write = {}, [], []
        for rel in files:
            refusal = tree_path_refusal(rel)
            if refusal is not None:
                dropped.append(f"{rel} ({refusal})")
                continue
            if rel in held_now or rel not in generation["blobs"]:
                if rel in parent:
                    entries[rel] = parent[rel]
                continue
            if parent.get(rel) == generation["exact"][rel]:
                entries[rel] = parent[rel]
            else:
                write.append(rel)
        if write:
            code, out, err = git.run("hash-object", "-w", "--no-filters", "--stdin-paths",
                                     stdin="".join(f".taskmaster/{rel}\n" for rel in write).encode())
            ids = out.decode().split()
            if code or len(ids) != len(write):
                raise ExportError(f"cannot hash the projection files: {redact(err, git.remote)}")
            for rel, sha in zip(write, ids):
                # The verified read's exact bytes: anything else changed after it (R2 pins this coupling).
                if sha != generation["exact"][rel]:
                    raise ExportError(f"{rel} changed during the export; retrying next cycle")
                entries[rel] = sha
        for path, sha in doc_entries.items():
            if tree_path_refusal(path) is None:
                entries[path] = sha
        if self._stopping():
            return {"state": "stopped"}
        index = git.temp_name("index")
        try:
            listing = "".join(f"100644 blob {entries[path]}\t{path}\0" for path in sorted(entries))
            env = {"GIT_INDEX_FILE": str(index)}
            code, _, err = git.run("update-index", "-z", "--add", "--index-info", stdin=listing.encode("utf-8"), env=env)
            if code:
                raise ExportError(f"cannot stage the export: {redact(err, git.remote)}")
            code, tree = git.text("write-tree", env=env)
            if code or not HEX.fullmatch(tree):
                raise ExportError("cannot write the export tree")
        finally:
            with contextlib.suppress(OSError):
                os.unlink(index)
        held = {rel: (state.get("held") or {}).get(rel, now) for rel in sorted(held_now)}
        result = {"state": "unchanged", "commit": tip, "through": marks.through, "docs_seq": docs_seq, "sync": sync,
                  "held": held, "dropped": dropped, "dropped_documents": dropped_docs, "adopted": adopted}
        parent_tree = git.text("rev-parse", "--verify", "-q", f"{tip}^{{tree}}")[1] if tip else None
        if tree == parent_tree:
            return result
        message = self._message(marks.through, entries, parent, held, dropped, dropped_docs, adopted, len(doc_entries))
        args = ["commit-tree", "--no-gpg-sign", tree] + (["-p", tip] if tip else []) + ["-F", "-"]
        code, commit = git.text(*args, stdin=message.encode("utf-8"), env=AUTHOR)
        if code or not HEX.fullmatch(commit):
            raise ExportError("cannot create the export commit")
        code, _, err = git.run("update-ref", "--no-deref", "-m", "taskmaster export", git.ref, commit, tip or ZERO)
        if code:
            text = redact(err, git.remote)
            if ".lock" in text:
                raise ExportError(f"ref lock present ({text}); remove it if no Git process is running")
            raise ExportError(f"{self.settings.branch} moved during the export; retrying next cycle ({text})")
        result.update(state="committed", commit=commit)
        return result

    def _message(self, through, entries, parent, held, dropped, dropped_docs, adopted, documents) -> str:
        added = sum(1 for p in entries if p not in parent)
        modified = sum(1 for p in entries if p in parent and parent[p] != entries[p])
        removed = sum(1 for p in parent if p not in entries)
        files = sum(1 for p in entries if not p.startswith(DOC_DIR + "/"))
        documents = sum(1 for p in entries if p.startswith(DOC_DIR + "/") and p != MANIFEST)
        lines = [f"taskmaster export: seq {through}, {files} files, {documents} documents", "",
                 f"+{added} ~{modified} -{removed}"]
        lines += [f"held: {rel}" for rel in list(held)[:KEPT]]
        lines += [f"dropped: {item}" for item in dropped[:KEPT]]
        lines += [f"dropped document: {item['document']} ({item['rule']})" for item in dropped_docs[:KEPT]]
        trailers = [f"{TRAILER_EXPORT}: {FORMAT_VERSION}", f"{TRAILER_STORE}: {self.store_id}",
                    f"{TRAILER_SEQ}: {through}"]
        build = getattr(self.owner, "build", None)
        if build is not None:
            from taskmaster.coordinator.protocol import describe
            trailers.append(f"{TRAILER_BUILD}: {describe(build)}")
        if held:
            trailers.append(f"{TRAILER_HELD}: {len(held)}")
        if adopted:
            trailers.append(f"{TRAILER_ADOPTED}: {adopted}")
        return "\n".join(lines) + "\n\n" + "\n".join(trailers) + "\n"

    # -- push and maintenance (outside the lock) --

    def fetch_mapping_refusal(self) -> str | None:
        """After a push Git rewrites local tracking refs through the remote's fetch refspecs, outside the publication
        lock. Refuse unless every mapping lands under refs/remotes/<name>/ and the export branch's tracking ref is no symref."""
        git = self.git
        prefix = f"refs/remotes/{git.remote}/"
        code, out = git.text("config", "--get-all", f"remote.{git.remote}.fetch")
        for spec in out.splitlines() if code == 0 else ():
            spec = spec.strip().lstrip("+")
            if not spec or spec.startswith("^"):
                continue
            dst = spec.partition(":")[2]
            if dst and not dst.startswith(prefix):
                return (f"remote.{git.remote}.fetch maps to a ref outside {prefix}, so a push would rewrite a local "
                        "branch. Fix the remote's fetch refspec or point --export-push at a different remote.")
        if git.run("symbolic-ref", "-q", git.tracking_ref)[0] == 0:
            return (f"{git.tracking_ref} is a symbolic ref, so a push would write through it. "
                    "Delete it or point --export-push at a different remote.")
        return None

    def push(self, state: dict, now: float) -> None:
        git = self.git
        if not git.remote:
            return
        tip = state.get("commit")
        if not tip or tip == state.get("pushed") or now < float(state.get("push_not_before") or 0):
            return
        learn_remote_secrets(git.remote, git.text("remote", "get-url", "--push", git.remote)[1])
        blocked = self.fetch_mapping_refusal()
        if blocked:
            failures = int(state.get("push_failures") or 0) + 1
            state.update(push_state="push_blocked", push_error=blocked, push_failures=failures,
                         push_not_before=now + min(3600.0, 60.0 * 2 ** (failures - 1)))
            return
        config = ["-c", "push.followTags=false", "-c", "push.recurseSubmodules=no"]
        if not self.ssh_env and git.run("config", "--get", "core.sshCommand")[0] != 0:
            config += ["-c", "core.sshCommand=ssh -o BatchMode=yes"]
        code, out, err = git.run("push", "--no-verify", "--porcelain", git.remote, f"{git.ref}:{git.ref}",
                                 env={"GCM_INTERACTIVE": "never", **self.ssh_env}, timeout=PUSH_TIMEOUT, config=config)
        # Porcelain status lines: " " fast-forward, "*" new ref, "=" up to date, "+" forced, "-" deleted, "!" rejected.
        lines = [line for line in out.decode("utf-8", "replace").splitlines()
                 if line[:1] in " =*+-!" and line.count("\t") >= 2]
        flags = [line[0] for line in lines]
        if code == 0 and flags and all(flag in " =*" for flag in flags):
            state.update(pushed=tip, push_failures=0, push_not_before=0, push_state="ok")
            return
        failures = int(state.get("push_failures") or 0) + 1
        wait = min(3600.0, 60.0 * 2 ** (failures - 1)) * self.rng.uniform(0.9, 1.1)
        rejected = "!" in flags or any(word in err.decode("utf-8", "replace") for word in REJECTED)
        if rejected:
            state["push_state"] = "push_blocked"
            state["push_error"] = (f"the remote's {self.settings.branch} has history this branch lacks. Inspect it "
                                   f"(git log {git.remote}/{self.settings.branch} after a manual fetch) and fetch it into "
                                   "the local branch only if it is this server's own history; otherwise point "
                                   "--export-push at a different backup repo. The exporter never force-pushes.")
        else:
            state["push_state"] = "push_failed"
            summary = " ".join(redact(line, git.remote) for line in lines)
            state["push_error"] = redact(err, git.remote) or summary or "push failed"
        state.update(push_failures=failures, push_not_before=now + wait)

    def maintain(self, state: dict, now: float) -> None:
        if now - float(state.get("last_maintenance") or 0) < MAINTENANCE_EVERY:
            return
        try:
            code, _, err = self.git.run("maintenance", "run", "--task=loose-objects", "--quiet",
                                        timeout=MAINTENANCE_TIMEOUT)
        except ExportError as exc:
            LOG.warning("export maintenance: %s", exc)
            return
        if code:
            LOG.warning("export maintenance failed: %s", redact(err, self.git.remote))
            return
        state["last_maintenance"] = now

    # -- the cycle --

    def cycle(self, *, adopt=False, push=True, acquire_timeout=managed.PUBLICATION_TIMEOUT) -> dict:
        now = self.wall()
        state = load_state(self.root)
        if state.get("store_id") not in (None, self.store_id) and not adopt:
            state = {k: v for k, v in state.items() if k in ("started",)}   # another store's record binds nothing here
        state.update(branch=self.settings.branch, remote=self.settings.push, interval=self.settings.interval,
                     last_attempt=now)
        state.setdefault("started", now)
        tracked = host_tracks_taskmaster(self.root)
        if tracked:
            state["host_tracks_taskmaster"] = tracked            # D13: Git may have overwritten live files (team-server-014)
        else:
            state.pop("host_tracks_taskmaster", None)
        outcome = {"state": None}
        try:
            sweep(self.directory)
            outcome = self._cycle(state, adopt, acquire_timeout, now)
            if outcome["state"] in ("committed", "unchanged"):
                self._settle(state, outcome, now)
                if push:
                    self.push(state, now)
                if outcome["state"] == "committed":
                    self.maintain(state, now)
        except ExportRefused as exc:
            outcome = {"state": "commit_failed", "reason": str(exc)}
        except ExportError as exc:
            outcome = {"state": "commit_failed", "reason": str(exc)}
        except managed.GitRefused as exc:
            outcome = {"state": "commit_failed", "reason": redact(str(exc), self.settings.push)}
        if outcome["state"] == "commit_failed":
            state.update(state="commit_failed", failures=int(state.get("failures") or 0) + 1,
                         last_error=redact(outcome["reason"], self.settings.push))
        elif outcome["state"] == "deferred":
            state.update(state="deferred", deferred_reason=outcome["reason"])
            state["deferred_since"] = state.get("deferred_since") or now
        outcome["push_state"] = state.get("push_state")
        try:
            save_state(self.root, state)
        except OSError as exc:
            LOG.warning("could not record the export state: %s", type(exc).__name__)
        outcome["record"] = state
        return outcome

    def _cycle(self, state, adopt, acquire_timeout, now) -> dict:
        owner = self.owner
        if self._stopping():
            return {"state": "stopped"}
        refusal = owner.publication_refusal()
        if refusal:
            return {"state": "deferred", "reason": "managed Git pin"}
        if owner.git_active is not None:
            return {"state": "deferred", "reason": "managed Git operation"}
        marks = self.marks()
        tip0 = _tip(self.git)
        if (state.get("store_id") == self.store_id and state.get("commit") and tip0 == state.get("commit")
                and marks.through == state.get("through") and marks.docs_seq == state.get("docs_seq")
                and state.get("branch_mark") == self.current_branch_mark()
                and not state.get("held") and not adopt):
            return {"state": "unchanged", "commit": tip0, "through": marks.through, "docs_seq": marks.docs_seq,
                    "held": {}, "dropped": [], "dropped_documents": state.get("dropped_documents") or [],
                    "adopted": None, "skipped": True, "sync": self.in_sync(state, marks)}
        docs = self.documents(_tree_map(self.git, tip0))
        if not owner.publication.acquire(timeout=acquire_timeout):
            return {"state": "deferred", "reason": "publisher busy"}
        try:
            if self._stopping():
                return {"state": "stopped"}
            if owner.publication_refusal():
                return {"state": "deferred", "reason": "managed Git pin"}
            if owner.git_active is not None:
                return {"state": "deferred", "reason": "managed Git operation"}
            return self._locked(state, docs, adopt, now)
        finally:
            owner.publication.release()

    def _settle(self, state: dict, outcome: dict, now: float) -> None:
        state.update(store_id=self.store_id, commit=outcome["commit"], through=outcome["through"],
                     docs_seq=outcome["docs_seq"], failures=0, last_error=None)
        if not outcome.get("skipped"):
            if getattr(self, "_branch_mark", None) is not None:
                state["branch_mark"] = self._branch_mark
            state["held"] = dict(list(outcome["held"].items())[:KEPT])
            state["held_count"] = len(outcome["held"])
            state["dropped_documents"] = outcome["dropped_documents"][:KEPT]
            state["dropped_documents_count"] = len(outcome["dropped_documents"])
        if outcome.get("adopted"):
            state["adopted"] = {"from_store": outcome["adopted"], "commit": outcome["commit"], "at": now}
        owed, why, high = outcome["sync"]
        if owed:
            state.update(state="behind", behind_reason=why, caught_up=False)
            if state.get("behind_owed") is None:
                state["behind_owed"] = high
            state["deferred_since"] = state.get("deferred_since") or now
        else:
            state.update(state=outcome["state"], caught_up=True, last_ok=now, behind_owed=None, behind_reason=None,
                         deferred_since=None, deferred_reason=None)

    def in_sync(self, state: dict, marks: Marks) -> tuple[bool, str | None, int]:
        """(owed, which check, high) at this read. RR-I1 and the busy-server note: "in sync" needs the store to owe
        nothing through the target recorded at the first `behind`, so a server that keeps up never alarms."""
        target = state.get("behind_owed")
        target = marks.high if target is None else int(target)
        owed, why = self.owed(target)
        return owed, why, marks.high


def host_tracks_taskmaster(root: Path) -> str | None:
    """HEAD's commit id when this server-mode clone's HEAD tracks `.taskmaster/` (D13), else None. Solo roots: None."""
    from taskmaster import server_marker
    from .untrack import head_tracks_taskmaster
    try:
        if server_marker.server_mode(root) is None:
            return None
    except server_marker.ServerConfigError:
        return None
    return head_tracks_taskmaster(root)


def export_once(owner, settings: Settings, *, adopt: bool = False, push: bool = True) -> dict:
    """One export cycle (the seam 014's `serve init --from` calls before untracking `.taskmaster/`)."""
    return Exporter(owner, settings).cycle(adopt=adopt, push=push)


# ── Scheduler ───────────────────────────────────────────────────────────────


def next_delay(outcome: dict, record: dict, interval: int, now: float, rng=random) -> float:
    state = outcome.get("state")
    if state == "deferred":
        delay = 60.0
    elif state == "commit_failed":
        failures = max(1, int(record.get("failures") or 1))
        delay = min(float(interval), 60.0 * 2 ** (failures - 1)) * rng.uniform(0.9, 1.1)
    else:
        delay = float(interval)
    push_due = float(record.get("push_not_before") or 0)
    if record.get("remote") and record.get("commit") and record.get("commit") != record.get("pushed") and push_due > now:
        delay = min(delay, max(1.0, push_due - now))
    return max(1.0, delay)


class StateExporter:
    """The daemon's exporter thread: a cycle every `interval`, with §7.1's backoff; never stops the daemon."""

    def __init__(self, owner, settings: Settings, *, first_delay=None, wall=time.time, rng=None, record=None):
        self.settings, self.wall = settings, wall
        self.first_delay = min(FIRST_DELAY, settings.interval) if first_delay is None else first_delay
        self.stop_event = threading.Event()
        self.rng = rng or random.Random()
        self.exporter = Exporter(owner, settings, wall=wall, record=record, halted=self.stop_event.is_set, rng=self.rng)
        self.thread = None
        self.busy = 0
        self.warned: dict[str, float] = {}
        self.tracked_warned = -WARN_EVERY            # D13's alarm has its own clock: `warned` clears when the export recovers
        self.failing = False

    def start(self) -> "StateExporter":
        self.thread = threading.Thread(target=self._run, name="taskmaster-state-export", daemon=True)
        self.thread.start()
        return self

    def _run(self):
        delay = self.first_delay
        while not self.stop_event.wait(delay):
            try:
                delay = self.step()
            except Exception:                    # noqa: BLE001 - the exporter must never take the daemon down
                LOG.exception("state export cycle crashed; retrying")
                delay = 60.0

    def step(self) -> float:
        timeout = BUSY_ACQUIRE if self.busy >= BUSY_STREAK else managed.PUBLICATION_TIMEOUT
        outcome = self.exporter.cycle(acquire_timeout=timeout)
        self.busy = self.busy + 1 if outcome.get("reason") == "publisher busy" else 0
        record = outcome.get("record") or {}
        self._log(outcome, record)
        return next_delay(outcome, record, self.settings.interval, self.wall(), self.rng)

    def _warn(self, key: str, text: str, *args) -> None:
        now = self.wall()
        if now - self.warned.get(key, -WARN_EVERY) >= WARN_EVERY:
            self.warned[key] = now
            LOG.warning(text, *args)

    def _log(self, outcome: dict, record: dict) -> None:
        state = record.get("state")
        stale = max(2 * self.settings.interval, 3600)
        problem = None
        if record.get("host_tracks_taskmaster"):
            self.failing = True
            now = self.wall()
            if now - self.tracked_warned >= WARN_EVERY:
                self.tracked_warned = now
                LOG.error("the host clone's HEAD (%s) tracks .taskmaster/: Git may have overwritten live projection "
                          "files; see docs/runbooks/team-server-migration.md#host-head", record["host_tracks_taskmaster"])
        if state == "commit_failed":
            problem = ("commit_failed", "state export failed: %s", record.get("last_error"))
        elif record.get("push_state") in ("push_failed", "push_blocked"):
            problem = (record["push_state"], "state export push failed: %s", record.get("push_error"))
        elif state in ("deferred", "behind") and record.get("deferred_since") and \
                self.wall() - float(record["deferred_since"]) > stale:
            reason = record.get("deferred_reason") if state == "deferred" else record.get("behind_reason")
            problem = (state, f"state export has not been in sync for over {stale} s ({state}: %s)", reason)
        if problem is not None:
            self.failing = True
            self._warn(problem[0], problem[1], problem[2])
            return
        if outcome.get("state") == "committed":
            LOG.info("state export: committed %s (seq %s, %s held, pushed %s)", outcome.get("commit"),
                     outcome.get("through"), len(outcome.get("held") or {}),
                     "yes" if record.get("pushed") == record.get("commit") else "no")
        if self.failing and record.get("caught_up") and not record.get("host_tracks_taskmaster"):
            self.failing = False
            self.warned.clear()
            LOG.info("state export recovered")

    def request_stop(self) -> None:
        self.stop_event.set()

    def stop(self, timeout: float = 10.0) -> None:
        self.stop_event.set()
        if self.thread is not None:
            self.thread.join(timeout)


# ── Offline commands: `serve export run` and `restore-documents` (§5.1, §4.5) ──


@contextlib.contextmanager
def offline_owner(root: Path, *, lease=None):
    """The host store's coordinator, in this process, while no daemon runs: ServeUnavailable (exit 69) when
    something already owns the store. The same ownership entry the daemon uses.

    `lease` is a host lease the caller already holds (014's pack and init keep one across several steps): it is
    used as is and is not released on exit. Without it the owner claims and releases its own."""
    from taskmaster import server_fence
    from taskmaster.coordinator import client
    from taskmaster.coordinator.ownership import OwnershipUnavailable
    from .config import ServeUnavailable
    from .daemon import OWNED_TEXT, ServeCoordinator, load_server_module
    from .principals import PrincipalRegistry, principals_path
    load_server_module(root)
    previous, client.AUTOSTART_ALLOWED = client.AUTOSTART_ALLOWED, False
    owner, own_lease = None, None
    try:
        if lease is None:
            try:
                own_lease = server_fence.claim_host(root)   # the fence exemption, held while this process owns the store
            except server_fence.HostLeaseHeld as exc:
                raise ServeUnavailable(str(exc)) from None
        try:
            owner = ServeCoordinator(root, registry=PrincipalRegistry(principals_path(root))).start()
        except OwnershipUnavailable:
            raise ServeUnavailable(OWNED_TEXT.format(root=root)) from None
        yield owner
    finally:
        if owner is not None:
            owner.stop()
            owner.close()
        if own_lease is not None:
            own_lease.release()
        client.AUTOSTART_ALLOWED = previous


def drain(owner, timeout: float = 120.0) -> None:
    """Let the coordinator's startup export catch up before a one-shot export reads the files."""
    with closing(owner._connect(readonly=True)) as connection:
        high = int(connection.execute("SELECT COALESCE(MAX(seq),0) FROM domain_events").fetchone()[0])
    owner.flush(high, timeout=timeout)


def _cat(git: Git, specs: list[str]) -> list[bytes | None]:
    code, out, err = git.run("cat-file", "--batch", stdin="".join(spec + "\n" for spec in specs).encode("utf-8"))
    if code:
        raise ExportError(f"cannot read the backup: {redact(err, git.remote)}")
    found, at = [], 0
    for _ in specs:
        end = out.index(b"\n", at)
        header = out[at:end].split()
        if len(header) != 3 or header[1] != b"blob":
            found.append(None)
            at = end + 1
            continue
        size = int(header[2])
        found.append(out[end + 1:end + 1 + size])
        at = end + 1 + size + 1
    return found


def _backup_commit(root: Path, source: str, branch: str):
    """`(git, commit)` of the backup a restore reads, after the branch and source checks both restores share."""
    from taskmaster.project import valid_branch_name
    root = Path(root)
    if not valid_branch_name(branch) or "@" in branch or branch.lower() == "head":
        raise ExportRefused(f"--export-branch {branch!r} is not a valid branch name")
    git = Git(root, Settings(root, branch=branch), export_directory(root))
    if git.run("check-ref-format", git.ref)[0] != 0:
        raise ExportRefused(f"--export-branch {branch!r} is not a valid branch name")
    refusal = branch_refusal(git, branch)
    if refusal is not None:
        raise ExportRefused(f"--export-branch {refusal}")
    if HEX.fullmatch(source or ""):
        commit = source if git.text("rev-parse", "--verify", "-q", source)[0] == 0 else None
    elif source in (branch, git.ref):
        commit = _tip(git)
    else:
        raise ExportRefused("--from takes a full commit id or the export branch name")
    if commit is None:
        raise ExportRefused(f"{source} is not a commit in this repository")
    return git, commit


def restore_documents(owner, root: Path, source: str, *, branch: str = DEFAULT_BRANCH) -> dict:
    """Re-import a backup's stored documents through `document.import(restore=True)` as the host operator
    (the 012/013 shared restore contract): the uploader and source survive; the source filter applies again."""
    from taskmaster.coordinator.actor_resolver import LOCAL
    from taskmaster.team_identity import HANDLE_RE
    root = Path(root)
    git, commit = _backup_commit(root, source, branch)
    raw = _cat(git, [f"{commit}:{MANIFEST}"])[0]
    if raw is None:
        return {"commit": commit, "restored": 0, "unchanged": 0, "refused": [], "failed": [],
                "note": "the backup holds no stored documents"}
    try:
        manifest = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise ExportError("the backup's document manifest is not valid JSON") from None
    if not isinstance(manifest, list):
        raise ExportError("the backup's document manifest is not a list")
    keys = {"kind", "id", "section", "path", "sha256", "source", "imported_by", "imported_seq"}
    entries = [entry for entry in manifest if isinstance(entry, dict) and keys <= set(entry)]
    bodies = _cat(git, [f"{commit}:{DOC_DIR}/{e['kind']}/{e['id']}/{e['section']}.md" for e in entries])
    report = {"commit": commit, "restored": 0, "unchanged": 0, "refused": [], "failed": []}
    run = os.urandom(4).hex()          # each run is its own request; the import itself is idempotent
    for entry, data in zip(entries, bodies):
        label = f"{entry['kind']}/{entry['id']}/{entry['section']}"
        rule = (tree_path_refusal(f"{DOC_DIR}/{entry['kind']}/{entry['id']}/{entry['section']}.md")
                or resolved_refusal(root, entry["path"]))
        if rule is None and (data is None or hashlib.sha256(data).hexdigest() != entry["sha256"]):
            rule = "blob does not match its sha256"
        body = data.decode("utf-8", "replace") if data is not None else None
        if rule is None:
            rule = body_refusal(body)
        if rule is not None:
            report["refused"].append({"document": label, "rule": rule})
            continue
        restored_source = entry["source"] if entry["source"] in ("upload", "file") else "file"   # fail closed
        handle = entry["imported_by"] if restored_source == entry["source"] else ""
        if not isinstance(handle, str) or not HANDLE_RE.fullmatch(handle):
            handle = ""
        arguments = {"kind": entry["kind"], "id": entry["id"], "section": entry["section"], "path": entry["path"],
                     "body": body, "restore": True, "source": restored_source, "imported_by": handle}
        if type(entry["imported_seq"]) is int and entry["imported_seq"] > 0:
            arguments["original_seq"] = entry["imported_seq"]
        envelope = {"protocol": 2, "store_id": owner.identity["store_id"], "caller_scope": scopes.EXPORT_RESTORE,
                    "request_id": f"restore:{run}:{commit[:12]}:{label}"[:256], "operation": "document.import",
                    "arguments": arguments, "expected_revisions": []}
        try:
            receipt = owner.submit(envelope, origin=LOCAL).result(120)
        except Exception as exc:                 # noqa: BLE001 - one bad entry never stops the rest
            report["failed"].append({"document": label, "error": str(exc)[:300]})
            continue
        report["restored" if receipt.get("affected") else "unchanged"] += 1
    return report


def restore_branches(owner, root: Path, source: str, *, branch: str = DEFAULT_BRANCH) -> dict:
    """Re-submit a backup's members' branch rows as `clone.restore` commands as the host operator (D10), so the
    merge gate finds those branches again. A row whose task no longer exists is refused and reported."""
    from taskmaster.coordinator.actor_resolver import LOCAL
    root = Path(root)
    git, commit = _backup_commit(root, source, branch)
    report = {"commit": commit, "branches_restored": 0, "branches_refused": [], "branches_failed": []}
    raw = _cat(git, [f"{commit}:{BRANCHES}"])[0]
    if raw is None:
        return report
    try:
        rows = json.loads(raw.decode("utf-8"))
    except ValueError:
        raise ExportError("the backup's branch file is not valid JSON") from None
    if not isinstance(rows, list):
        raise ExportError("the backup's branch file is not a list")
    run = os.urandom(4).hex()
    for row in rows:
        if not isinstance(row, dict) or not {"handle", "clone", "kind", "id", "branch"} <= set(row):
            report["branches_refused"].append({"row": str(row)[:120], "rule": "malformed row"})
            continue
        label = f"{row['handle']}/{row['clone'] or '-'}/{row['id']}"
        arguments = {"handle": row["handle"], "clone": row["clone"], "kind": row["kind"], "id": row["id"],
                     "field": "branch", "value": row["branch"]}
        envelope = {"protocol": 2, "store_id": owner.identity["store_id"], "caller_scope": scopes.EXPORT_RESTORE,
                    "request_id": f"restore-branch:{run}:{commit[:12]}:{label}"[:256], "operation": "clone.restore",
                    "arguments": arguments, "expected_revisions": []}
        try:
            owner.submit(envelope, origin=LOCAL).result(120)
        except Exception as exc:                 # noqa: BLE001 - one bad row never stops the rest
            text = str(exc)[:300]
            if "not found" in text or "no live task" in text or "clone.restore" in text:
                report["branches_refused"].append({"row": label, "rule": text})
            else:
                report["branches_failed"].append({"row": label, "error": text})
            continue
        report["branches_restored"] += 1
    return report


# ── Status (§7.4) ───────────────────────────────────────────────────────────


def status(root: Path, *, now=None) -> tuple[dict, int]:
    """(report, exit code): 0 healthy, 1 failing or stale, 2 not configured. Never opens the store."""
    now = time.time() if now is None else now
    record = load_state(root)
    if not record.get("branch"):
        return {"configured": False}, 2
    interval = int(record.get("interval") or DEFAULT_INTERVAL)
    stale = max(2 * interval, 3600)
    reasons = []
    if record.get("host_tracks_taskmaster"):
        reasons.append(f"host HEAD {record['host_tracks_taskmaster']} tracks .taskmaster/ (docs/runbooks/"
                       "team-server-migration.md#host-head)")
    if int(record.get("failures") or 0) > 0:
        reasons.append(f"commit failing: {record.get('last_error')}")
    if record.get("push_state") in ("push_failed", "push_blocked") or int(record.get("push_failures") or 0) > 0:
        reasons.append(f"{record.get('push_state') or 'push_failed'}: {record.get('push_error')}")
    since = record.get("deferred_since")
    if since is not None and now - float(since) > stale:
        label = record.get("deferred_reason") if record.get("state") == "deferred" else record.get("behind_reason")
        reasons.append(f"{record.get('state')} for {int(now - float(since))} s ({label})")
    last_ok = record.get("last_ok")
    reference = float(last_ok) if last_ok is not None else float(record.get("started") or now)
    if now - reference > stale:
        reasons.append(f"not in sync for {int(now - reference)} s")
    held = record.get("held") or {}
    old = [rel for rel, at in held.items() if now - float(at) > HELD_ALARM]
    if old:
        reasons.append(f"{len(old)} path(s) held for over 24 h")
    tip = None
    with contextlib.suppress(Exception):
        code, out = managed.probe(root, "rev-parse", "--verify", "-q", f"refs/heads/{record['branch']}", ok=(0, 1))
        tip = out.decode().strip() if code == 0 else None
    report = {key: record.get(key) for key in (
        "branch", "remote", "interval", "state", "commit", "pushed", "through", "last_ok", "deferred_since",
        "deferred_reason", "behind_reason", "caught_up", "failures", "push_failures", "push_state", "push_error",
        "last_error", "adopted", "held_count", "dropped_documents_count", "dropped_documents",
        "host_tracks_taskmaster")}
    report.update(tip=tip, held_oldest=min(held.values()) if held else None, problems=reasons)
    return report, 1 if reasons else 0
