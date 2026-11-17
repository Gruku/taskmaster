# User intent: stop a code branch tracking `.taskmaster/` without touching anyone's files or running any hook, as a plain
# plumbing commit with compare-and-swap ref moves (team-server-014); and tell the host clone when a checkout has brought
# the tracked `.taskmaster/` back, because Git then silently overwrites its live projection files.
"""The untrack transform (design §7) and the host HEAD guard (§10.1).

Every Git call goes through `UntrackGit`, whose argv allowlist (§7.4) excludes push, fetch, pull, rm, checkout, switch,
commit, merge, clean, stash, gc and every `reset` but the one exact form. Objects are built first (`build_branch_commit`);
refs move afterwards, forward only, by `update-ref <ref> <new> <old>`.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from taskmaster import server_marker
from taskmaster.coordinator.ownership import private_directory, private_directory_at

from .gitrun import AllowlistedGit

HEX = re.compile(r"[0-9a-f]{40}")
HEAD_REF = "refs/heads/"
TASKMASTER = ".taskmaster"
GITIGNORE = ".gitignore"
MARKER = server_marker.MARKER_NAME
IGNORING = frozenset({".taskmaster", ".taskmaster/", "/.taskmaster", "/.taskmaster/"})
RESET_PATHS = [TASKMASTER, GITIGNORE, MARKER]
MOVE_MESSAGE = "taskmaster serve init pack"
TREE_MODE = "040000"
COMMIT_MESSAGE = """chore: serve this project's backlog from a Taskmaster server

Untracks .taskmaster/ (the backlog now lives on the team server and is backed up on the
taskmaster-state branch there) and adds .taskmaster-server.json, which fences every clone.

Taskmaster-Migration: 1
Taskmaster-Store: {store_id}
"""


class UntrackRefused(Exception):
    """A branch or repository can't be untracked as asked: nothing was written."""


class UntrackError(Exception):
    """A Git step failed; objects may exist, no ref moved by this step."""


class SigningFailed(UntrackError):
    """`commit.gpgSign` is on and the commit could not be signed."""


class LostRace(UntrackError):
    """A branch moved between the build and the compare-and-swap; nothing after it was moved."""


# -- the runner ------------------------------------------------------------------------------------------

class UntrackGit(AllowlistedGit):
    """The §7.4 allowlist. `plan(branch)` names a branch `update-ref` may move; nothing else is movable."""

    refused_error, failed_error = UntrackRefused, UntrackError
    ENV_KEYS: frozenset = frozenset()          # signing variables are inherited by git (only GIT_* is scrubbed)

    def __init__(self, root: Path, directory: Path, *, record=None, planned=()):
        super().__init__(root, directory, record=record)
        self.planned = set(planned)

    def plan(self, branch: str) -> None:
        self.planned.add(branch)

    @staticmethod
    def _head_ref(value: str) -> bool:
        return (value.startswith(HEAD_REF) and len(value) > len(HEAD_REF) and ".." not in value
                and not any(ch.isspace() or ord(ch) < 32 for ch in value))

    @staticmethod
    def _remote_ref(value: str) -> bool:
        return (value.startswith("refs/remotes/") and len(value) > len("refs/remotes/") and ".." not in value
                and not any(ch.isspace() or ord(ch) < 32 for ch in value))

    def refusal(self, args, env) -> str | None:
        if env:
            return f"environment {sorted(env)}"
        if not args:
            return "empty argv"
        command, rest = args[0], list(args[1:])
        if command == "rev-parse":
            flags = {"--show-toplevel", "--absolute-git-dir", "--git-common-dir", "--show-superproject-working-tree",
                     "--show-object-format", "--show-ref-format"}
            ok = (len(rest) == 1 and rest[0] in flags) or rest == ["--symbolic-full-name", "@{u}"] or (
                len(rest) == 3 and rest[:2] == ["--verify", "-q"] and (
                    rest[2] in ("HEAD:.taskmaster-server.json", "HEAD:.taskmaster", "HEAD^{commit}", "@{u}^{commit}")
                    or (rest[2].endswith("^{commit}") and (
                        self._head_ref(rest[2][:-len("^{commit}")])
                        or self._remote_ref(rest[2][:-len("^{commit}")])))))
        elif command == "symbolic-ref":
            ok = rest == ["-q", "HEAD"] or (len(rest) == 2 and rest[0] == "-q" and self._head_ref(rest[1]))
        elif command == "for-each-ref":
            ok = rest in (["--format=%(refname)", "refs/heads/"], ["--format=%(refname)", "refs/remotes/"]) or (
                len(rest) == 4 and rest[0] == "--format=%(refname)" and rest[1] == "--contains"
                and HEX.fullmatch(rest[2]) is not None and rest[3] == "refs/remotes/")
        elif command == "worktree":
            ok = rest == ["list", "--porcelain"]
        elif command == "status":
            ok = rest == ["--porcelain=v1", "-z", "--untracked-files=no", "--", GITIGNORE]
        elif command == "ls-tree":
            ok = (len(rest) == 2 and rest[0] == "-z" and HEX.fullmatch(rest[1]) is not None) or (
                len(rest) == 6 and rest[:3] == ["-r", "--name-only", "-z"] and HEX.fullmatch(rest[3]) is not None
                and rest[4:] == ["--", TASKMASTER])
        elif command == "ls-files":
            ok = rest == ["-z", "--", TASKMASTER]
        elif command == "cat-file":
            ok = len(rest) == 2 and rest[0] == "blob" and HEX.fullmatch(rest[1]) is not None
        elif command == "hash-object":
            ok = rest == ["-w", "--no-filters", "--stdin"]
        elif command == "mktree":
            ok = rest == ["-z"]
        elif command == "commit-tree":
            ok = (len(rest) == 6 and HEX.fullmatch(rest[0]) is not None and rest[1] == "-p"
                  and HEX.fullmatch(rest[2]) is not None and rest[3] in ("-S", "--no-gpg-sign") and rest[4:] == ["-F", "-"])
        elif command == "update-ref":
            ok = (len(rest) == 5 and rest[:2] == ["-m", MOVE_MESSAGE] and rest[2].startswith(HEAD_REF)
                  and rest[2][len(HEAD_REF):] in self.planned and all(HEX.fullmatch(x) for x in rest[3:]))
        elif command == "reset":
            ok = rest == ["-q", "HEAD", "--", *RESET_PATHS]
        elif command == "checkout-index":
            ok = rest == ["-f", "--", GITIGNORE]
        elif command == "var":
            ok = rest == ["GIT_COMMITTER_IDENT"]
        elif command == "config":
            ok = rest == ["--get", "--type=bool", "commit.gpgSign"]
        elif command == "merge-base":
            ok = (len(rest) == 3 and rest[0] == "--is-ancestor" and HEX.fullmatch(rest[1]) is not None
                  and (HEX.fullmatch(rest[2]) is not None or rest[2] == "HEAD"))
        elif command == "check-ignore":
            ok = rest == ["-q", "--", ".taskmaster/backlog.yaml"]
        else:
            ok = False
        return None if ok else f"git {command} {' '.join(rest)[:120]}"


def init_directory(root: Path) -> Path:
    """`.taskmaster/local/serve/init/`, owner-only: the stage journals, temp copies and the hook-free directory."""
    return private_directory_at(private_directory(Path(root), "serve") / "init")


def git_for(root: Path, *, record=None, planned=()) -> UntrackGit:
    directory = init_directory(root)
    private_directory_at(directory / "no-hooks")
    return UntrackGit(root, directory, record=record, planned=planned)


# -- reads -----------------------------------------------------------------------------------------------

def tip(git: UntrackGit, branch: str) -> str | None:
    code, out = git.text("rev-parse", "--verify", "-q", f"{HEAD_REF}{branch}^{{commit}}")
    return out if code == 0 and HEX.fullmatch(out) else None


def current_branch(git: UntrackGit) -> str | None:
    """The checked-out branch's short name, None for a detached or unborn HEAD."""
    code, out = git.text("symbolic-ref", "-q", "HEAD")
    return out[len(HEAD_REF):] if code == 0 and out.startswith(HEAD_REF) else None


def is_symbolic(git: UntrackGit, branch: str) -> bool:
    code, _ = git.text("symbolic-ref", "-q", f"{HEAD_REF}{branch}")
    return code == 0


def local_branches(git: UntrackGit) -> list[str]:
    _, out = git.text("for-each-ref", "--format=%(refname)", HEAD_REF)
    return [line[len(HEAD_REF):] for line in out.splitlines() if line.startswith(HEAD_REF)]


def remote_branches(git: UntrackGit) -> list[str]:
    _, out = git.text("for-each-ref", "--format=%(refname)", "refs/remotes/")
    return [line for line in out.splitlines() if line and not line.endswith("/HEAD")]


@dataclass(frozen=True)
class Entry:
    mode: str
    kind: str
    sha: str
    name: str

    def line(self) -> bytes:
        return f"{self.mode} {self.kind} {self.sha}\t{self.name}".encode("utf-8", "surrogateescape") + b"\0"


def root_entries(git: UntrackGit, commit: str) -> list[Entry]:
    code, out, err = git.run("ls-tree", "-z", commit)
    if code:
        raise UntrackError(f"cannot read {commit[:12]}: {err.decode('utf-8', 'replace').strip()[:200]}")
    entries = []
    for raw in out.split(b"\0"):
        if not raw:
            continue
        head, _, name = raw.partition(b"\t")
        mode, kind, sha = head.decode("ascii").split(" ")
        entries.append(Entry(mode, kind, sha, name.decode("utf-8", "surrogateescape")))
    return entries


def tracked_listing(git: UntrackGit, commit: str) -> list[str]:
    _, out, _ = git.run("ls-tree", "-r", "--name-only", "-z", commit, "--", TASKMASTER)
    return [item.decode("utf-8", "surrogateescape") for item in out.split(b"\0") if item]


def index_tracks_taskmaster(git: UntrackGit) -> bool:
    _, out, _ = git.run("ls-files", "-z", "--", TASKMASTER)
    return bool(out.strip(b"\0"))


def signing_enabled(git: UntrackGit) -> bool:
    code, out = git.text("config", "--get", "--type=bool", "commit.gpgSign")
    return code == 0 and out == "true"


def blob(git: UntrackGit, sha: str) -> bytes:
    code, out, err = git.run("cat-file", "blob", sha)
    if code:
        raise UntrackError(f"cannot read blob {sha[:12]}: {err.decode('utf-8', 'replace').strip()[:200]}")
    return out


def write_blob(git: UntrackGit, data: bytes) -> str:
    code, out, err = git.run("hash-object", "-w", "--no-filters", "--stdin", stdin=data)
    sha = out.decode("ascii", "replace").strip()
    if code or not HEX.fullmatch(sha):
        raise UntrackError(f"cannot store an object: {err.decode('utf-8', 'replace').strip()[:200]}")
    return sha


# -- the transform ---------------------------------------------------------------------------------------

def gitignore_content(existing: bytes | None) -> bytes | None:
    """The `.gitignore` bytes with `/.taskmaster/` added in the file's own line ending; None when it already ignores it."""
    if existing is None:
        return b"/.taskmaster/\n"
    if any(line.strip() in IGNORING for line in existing.decode("utf-8", "replace").splitlines()):
        return None
    eol = b"\r\n" if b"\r\n" in existing else b"\n"
    if existing and not existing.endswith(b"\n"):
        existing += eol
    return existing + b"/.taskmaster/" + eol


def branch_refusal(git: UntrackGit, branch: str, commit: str) -> str | None:
    """Why `commit`'s root can't take the untrack (P11 and the marker rule of P10), or None."""
    entries = {entry.name: entry for entry in root_entries(git, commit)}
    if MARKER in entries:
        return f"{branch} already has {MARKER}"
    found = entries.get(TASKMASTER)
    if found is not None and found.mode != TREE_MODE:
        return f"{branch} has .taskmaster as something other than a directory"
    ignore = entries.get(GITIGNORE)
    if ignore is not None and ignore.kind != "blob":
        return f"{branch} has .gitignore as something other than a file"
    if ignore is not None and ignore.mode == "120000":
        return f"{branch} has .gitignore as a symlink"
    return None


def build_branch_commit(git: UntrackGit, branch: str, commit: str, marker: server_marker.Marker, store_id: str,
                        *, sign: bool) -> str:
    """The untrack commit for `commit` (the tip of `branch`): objects only, no ref moves. Its single parent is `commit`."""
    problem = branch_refusal(git, branch, commit)
    if problem:
        raise UntrackRefused(problem)
    entries = [entry for entry in root_entries(git, commit) if entry.name != TASKMASTER]
    ignore = next((entry for entry in entries if entry.name == GITIGNORE), None)
    changed = gitignore_content(None if ignore is None else blob(git, ignore.sha))
    if changed is not None:
        mode = "100644" if ignore is None else ignore.mode
        entries = [entry for entry in entries if entry.name != GITIGNORE]
        entries.append(Entry(mode, "blob", write_blob(git, changed), GITIGNORE))
    entries.append(Entry("100644", "blob", write_blob(git, server_marker.render(marker).encode("utf-8")), MARKER))
    code, out, err = git.run("mktree", "-z", stdin=b"".join(entry.line() for entry in entries))
    tree = out.decode("ascii", "replace").strip()
    if code or not HEX.fullmatch(tree):
        raise UntrackError(f"cannot build the tree: {err.decode('utf-8', 'replace').strip()[:200]}")
    code, out, err = git.run("commit-tree", tree, "-p", commit, "-S" if sign else "--no-gpg-sign", "-F", "-",
                             stdin=COMMIT_MESSAGE.format(store_id=store_id).encode("utf-8"))
    new = out.decode("ascii", "replace").strip()
    if code or not HEX.fullmatch(new):
        text = err.decode("utf-8", "replace").strip()[:300]
        if sign:
            raise SigningFailed(f"signing failed ({text}): fix signing or set commit.gpgSign=false for this "
                                "repository, then pack again")
        raise UntrackError(f"cannot create the commit: {text}")
    return new


def move_ref(git: UntrackGit, branch: str, new: str, old: str) -> None:
    """`update-ref refs/heads/<branch> <new> <old>`: LostRace when the branch is no longer at `old`."""
    git.plan(branch)
    code, _, err = git.run("update-ref", "-m", MOVE_MESSAGE, f"{HEAD_REF}{branch}", new, old)
    if code:
        raise LostRace(f"{branch} moved since the pack read it: {err.decode('utf-8', 'replace').strip()[:200]}")


def _step(git: UntrackGit, *args) -> None:
    code, _, err = git.run(*args)
    if code:
        raise UntrackError(f"git {args[0]} failed: {err.decode('utf-8', 'replace').strip()[:200]}")


def sync_index(git: UntrackGit) -> None:
    """Index entries under `.taskmaster/` go; the marker and `.gitignore` entries match HEAD. Other staged changes stay."""
    _step(git, "reset", "-q", "HEAD", "--", *RESET_PATHS)


def sync_gitignore(git: UntrackGit) -> None:
    """Write the working `.gitignore` from the index, with the repository's own line-ending conversion."""
    _step(git, "checkout-index", "-f", "--", GITIGNORE)


def sync_current(git: UntrackGit) -> None:
    """Make the index and the working `.gitignore` agree with the new HEAD. Idempotent; touches no other file."""
    sync_index(git)
    sync_gitignore(git)


# -- the host HEAD guard (D13) ---------------------------------------------------------------------------

def head_tracks_taskmaster(root: Path, *, closed: bool = False) -> str | None:
    """HEAD's commit id when HEAD tracks `.taskmaster/`, else None. When Git can't be asked: None, or with
    `closed=True` (the daemon's start guard) "unknown", so an unanswered question refuses rather than passes."""
    try:
        git = git_for(Path(root))
        code, _ = git.text("rev-parse", "--verify", "-q", "HEAD:.taskmaster")
        if code != 0:
            return None
        code, head = git.text("rev-parse", "--verify", "-q", "HEAD^{commit}")
        return head if code == 0 and HEX.fullmatch(head) else "HEAD"
    except (UntrackRefused, UntrackError, OSError, PermissionError):
        return "unknown (Git did not answer)" if closed else None


# -- which branches may be untracked (P10, P11) ----------------------------------------------------------

def worktree_branches(git: UntrackGit) -> list[str]:
    """Branch names checked out in any worktree of this repository (the main one included)."""
    _, out = git.text("worktree", "list", "--porcelain")
    return [line[len("branch " + HEAD_REF):] for line in out.splitlines() if line.startswith("branch " + HEAD_REF)]


def branch_problem(git: UntrackGit, root: Path, branch: str, *, current: str | None, owned: frozenset = frozenset()) -> str | None:
    """Why `--branch branch` can't be untracked (design P10, P11), or None. `owned` holds tips that are the
    journal's recorded new commits: those are already untracked and are accepted as they stand."""
    import os
    found = tip(git, branch)
    if found is None:
        return f"{branch} does not exist"
    if is_symbolic(git, branch):
        return f"{branch} is a symbolic ref"
    if branch == current:
        return f"{branch} is the checked-out branch, which is always untracked"
    fold = os.path.normcase if os.name == "nt" else (lambda value: value)
    if fold(branch) in {fold(name) for name in worktree_branches(git)}:
        return f"{branch} is checked out in a worktree"
    if found in owned:
        return None
    from . import state_export
    if state_export.is_export_branch(root, f"{HEAD_REF}{branch}"):
        return f"{branch} is a state export branch"
    return branch_refusal(git, branch, found)
