# User intent: files Taskmaster writes under .taskmaster/ must carry the line endings Git would
# check out for that path, so `git status` is not noisy with CRLF/LF-only "modifications" on Windows.
"""Ask Git which line ending a checkout would give a path under a backlog directory.

`line_ending(backlog_dir, path)` answers "lf", "crlf" or None (no opinion: not a Git repo, Git
missing or failing, or Git would write the index bytes unchanged). One batched `git check-attr`
plus one `git ls-files --eol` per backlog directory, cached; a changed `.gitattributes` or Git
config invalidates the cache. Every call goes through `run_bounded`: a plain subprocess timeout
can hang on Windows via a git.exe grandchild. Git runs outside the module lock, so a slow git
never blocks other writer threads.

Known limit: a tracked file's index EOL is read when the cache loads, so a file first cached while
untracked keeps that answer until the signature (config / .gitattributes mtimes) changes.
"""
from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from pathlib import Path

from taskmaster.bounded_run import run_bounded

GIT_TIMEOUT = 5.0
# After a failure (timeout, odd repo) do not respawn git on every write.
_RETRY_AFTER = 30.0
# Config / .gitattributes mtimes are re-read at most this often.
_RECHECK_AFTER = 1.0
_TRUE = {"true", "yes", "on", "1"}
_MISSING = object()


class _State:
    def __init__(self) -> None:
        self.signature: tuple | None = None
        self.failed_at: float | None = None
        self.checked = 0.0
        self.generation = 0
        self.prefix = ""
        self.toplevel: Path | None = None
        self.config_files: list[Path] = []
        self.attr_files: list[Path] = []
        self.info_attributes: Path | None = None
        self.autocrlf = "false"
        self.core_eol = "native"
        self.paths: dict[str, str | None] = {}  # repo-relative path -> answer
        self.index_eol: dict[str, str] = {}     # repo-relative tracked path -> i/<eol>

    def adopt(self, fresh: "_State") -> None:
        generation = self.generation + 1
        self.__dict__.update(fresh.__dict__)
        self.generation = generation


_STATES: dict[str, _State] = {}
_LOCK = threading.Lock()


def reset_for_tests() -> None:
    with _LOCK:
        _STATES.clear()


def _git(args: list[str], cwd: Path, *, input: bytes | None = None) -> bytes:
    done = run_bounded(["git", *args], timeout=GIT_TIMEOUT, input=input, cwd=str(cwd))
    if done.returncode != 0:
        raise OSError(f"git {args[0]} failed: {done.stderr[:200]!r}")
    return done.stdout


def _has_git_marker(directory: Path) -> bool:
    return any(os.path.lexists(parent / ".git") for parent in (directory, *directory.parents))


def _stamp(path: Path) -> int:
    try:
        return path.stat().st_mtime_ns
    except OSError:
        return 0


def _signature(state: _State, backlog_dir: Path) -> tuple:
    stamps = []
    if state.toplevel is not None:
        for parent in (backlog_dir, *backlog_dir.parents):
            stamps.append(_stamp(parent / ".gitattributes"))
            if parent == state.toplevel:
                break
    for path in (*state.config_files, *state.attr_files):
        stamps.append(_stamp(path))
    if state.info_attributes is not None:
        stamps.append(_stamp(state.info_attributes))
    return tuple(stamps)


def _rel(state: _State, path: Path) -> str | None:
    try:
        return path.resolve().relative_to(state.toplevel).as_posix()  # type: ignore[arg-type]
    except (ValueError, OSError):
        return None


def _parse_config(state: _State, raw: bytes) -> None:
    """Fill autocrlf / core.eol and the config files Git read, from `config --list --show-origin -z`."""
    items = raw.split(b"\0")
    files: list[Path] = []
    for i in range(0, len(items) - 1, 2):
        origin = items[i].decode("utf-8", "replace")
        entry = items[i + 1].decode("utf-8", "replace")
        if origin.startswith("file:"):
            path = Path(origin[5:])
            if path not in files:
                files.append(path)
        key, has_value, value = entry.partition("\n")
        key, value = key.lower(), value.strip().lower()
        if key == "core.autocrlf":
            # A bare key (no `=`) is true; an explicit empty value is false.
            if value == "input":
                state.autocrlf = "input"
            elif not has_value or value in _TRUE:
                state.autocrlf = "true"
            else:
                state.autocrlf = "false"
        elif key == "core.eol":
            state.core_eol = value
    state.config_files = files


def _load(state: _State, backlog_dir: Path) -> None:
    """Build per-repo state into a fresh `state`; raises on any git failure. Runs without the lock."""
    out = _git(["rev-parse", "--show-toplevel", "--git-common-dir"], backlog_dir)
    top, common = (line.strip() for line in out.decode("utf-8", "replace").splitlines()[:2])
    state.toplevel = Path(top).resolve()
    common_path = Path(common)
    common_path = common_path if common_path.is_absolute() else backlog_dir / common_path
    state.info_attributes = common_path.resolve() / "info" / "attributes"
    state.autocrlf, state.core_eol = "false", "native"
    _parse_config(state, _git(["config", "--list", "--show-origin", "-z"], backlog_dir))
    here = _rel(state, backlog_dir)
    state.prefix = "" if here in (None, ".", "") else here + "/"
    state.attr_files = []
    for root, dirs, files in os.walk(backlog_dir):
        dirs[:] = [d for d in dirs if d not in ("local", ".git")]
        if ".gitattributes" in files:
            state.attr_files.append(Path(root) / ".gitattributes")
    state.paths, state.index_eol = {}, {}
    tracked: list[str] = []
    for entry in _git(["ls-files", "-z", "--eol", "--", "."], backlog_dir).split(b"\0"):
        if not entry:
            continue
        meta, _, name = entry.decode("utf-8", "replace").partition("\t")
        rel = _rel(state, backlog_dir / name)
        if rel is None:
            continue
        tracked.append(rel)
        index_part = meta.split()[0] if meta.split() else ""
        state.index_eol[rel] = index_part[2:] if index_part.startswith("i/") else ""
    if tracked:
        state.paths.update(_query_attrs(state, tracked))
    state.signature = _signature(state, backlog_dir)


def _query_attrs(state: _State, rels: list[str]) -> dict[str, str | None]:
    """Answers for `rels` from one batched check-attr; reads config fields only, so no lock is needed."""
    payload = b"\0".join(rel.encode("utf-8") for rel in rels) + b"\0"
    out = _git(["check-attr", "-z", "--stdin", "text", "eol"], state.toplevel, input=payload)  # type: ignore[arg-type]
    fields = out.split(b"\0")
    attrs: dict[str, dict[str, str]] = {}
    for i in range(0, len(fields) - 2, 3):
        name, attr, value = (f.decode("utf-8", "replace") for f in fields[i:i + 3])
        attrs.setdefault(name, {})[attr] = value
    answers = {}
    for rel in rels:
        got = attrs.get(rel, {})
        answers[rel] = _decide(
            state, got.get("text", "unspecified"), got.get("eol", "unspecified"), state.index_eol.get(rel, ""))
    return answers


def _decide(state: _State, text: str, eol: str, index_eol: str) -> str | None:
    """Git's checkout rules for one path; None when Git writes the bytes unchanged."""
    if text == "unset":
        return None
    auto_like = text == "auto" or (text == "unspecified" and state.autocrlf in ("true", "input"))
    if auto_like and index_eol in ("-text", "mixed"):
        return None  # binary-looking or mixed blobs are never converted
    if text == "auto" and index_eol == "crlf":
        return "crlf"  # a CRLF blob under text=auto is never converted; it checks out as it is
    if eol in ("lf", "crlf"):
        return eol
    if text in ("set", "auto"):
        converting = True
    elif text == "unspecified":
        converting = state.autocrlf in ("true", "input")
    else:
        converting = False
    if not converting:
        # No conversion: Git checks out the index bytes, so a CRLF blob comes back CRLF.
        return "crlf" if index_eol == "crlf" else None
    if index_eol == "crlf":
        return "crlf"
    if state.autocrlf == "true":
        return "crlf"
    if state.autocrlf == "input":
        return "lf"
    if state.core_eol in ("lf", "crlf"):
        return state.core_eol
    return "crlf" if sys.platform == "win32" else "lf"


def _fast_rel(state: _State, raw_dir: str, path) -> str | None:
    """Repo-relative path by string arithmetic, for a path spelled under the same directory string."""
    text = os.fspath(path)
    prefix = raw_dir + os.sep
    if not text.startswith(prefix):
        return None
    sub = text[len(prefix):].replace(os.sep, "/")
    if sub.startswith("/") or ".." in sub.split("/"):
        return None
    return state.prefix + sub


def _snapshot(state: _State) -> _State:
    """A shallow copy for reading config fields outside the lock."""
    copy = _State()
    copy.__dict__.update(state.__dict__)
    return copy


def line_ending(backlog_dir: Path, path: Path) -> str | None:
    """"lf", "crlf" or None for `path` under `backlog_dir`; never raises."""
    try:
        raw = os.fspath(backlog_dir)
        now = time.perf_counter()
        with _LOCK:
            state = _STATES.get(raw)
        if state is None:
            if not _has_git_marker(Path(raw).resolve()):
                return None
            with _LOCK:
                state = _STATES.setdefault(raw, _State())
        with _LOCK:
            if state.failed_at is not None and now - state.failed_at < _RETRY_AFTER:
                return None
            reload_due = state.signature is None or now - state.checked >= _RECHECK_AFTER
            known_signature = state.signature
        try:
            if reload_due:
                resolved = Path(raw).resolve()
                with _LOCK:
                    stale = known_signature is None or known_signature != _signature(state, resolved)
                if stale:
                    fresh = _State()
                    _load(fresh, resolved)  # git calls: outside the lock
                    with _LOCK:
                        state.adopt(fresh)
                with _LOCK:
                    state.checked = now
                    state.failed_at = None
            with _LOCK:
                snapshot = _snapshot(state)
                rel = _fast_rel(state, raw, path)
            if rel is None:
                target = Path(path)
                rel = _rel(snapshot, target.parent.resolve() / target.name)
                if rel is None:
                    return None
            with _LOCK:
                answer = state.paths.get(rel, _MISSING)
            if answer is _MISSING:
                answer = _query_attrs(snapshot, [rel])[rel]  # git call: outside the lock
                with _LOCK:
                    if state.generation == snapshot.generation:
                        state.paths[rel] = answer
            return answer  # type: ignore[return-value]
        except (OSError, subprocess.SubprocessError, ValueError):
            with _LOCK:
                state.failed_at = now
                state.signature = None
            return None
    except Exception:
        return None


def crlf_for(backlog_dir: Path | None, path: Path) -> bool | None:
    """True/False when Git has an opinion on `path`, else None."""
    if backlog_dir is None:
        return None
    answer = line_ending(backlog_dir, path)
    return None if answer is None else answer == "crlf"


def tracked_local_files(backlog_dir: Path) -> list[str]:
    """Repo-relative tracked paths under `<backlog_dir>/local` (machine-local files); [] on any failure."""
    try:
        backlog_dir = Path(backlog_dir).resolve()
        if not _has_git_marker(backlog_dir):
            return []
        out = _git(["ls-files", "-z", "--full-name", "--", "local"], backlog_dir)
        return [n.decode("utf-8", "replace") for n in out.split(b"\0") if n]
    except Exception:
        return []
