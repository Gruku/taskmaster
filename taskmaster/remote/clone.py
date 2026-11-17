# User intent: name one checkout of a team project so the server can keep a person's branch, worktree and merge
# records per clone instead of overwriting one shared field. The id is not a secret and authenticates nothing; the
# handle always comes from the key (team-server-011 D1).
"""Clone identity: `c-<16 hex>` in `<root>/.git/taskmaster-clone.json`, shared by every worktree of the clone."""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path

from taskmaster.coordinator import ownership

NAME = "taskmaster-clone.json"
CLONE_RE = re.compile(r"c-[0-9a-f]{16}")
MAX_BYTES = 1024


def clone_path(root) -> Path:
    return Path(root) / ".git" / NAME


def _read(path: Path) -> str | None:
    """The id in a regular, owner-only, small file; None for anything else (never follows a link)."""
    try:
        if path.is_symlink() or getattr(path, "is_junction", lambda: False)() or not path.is_file():
            return None
        ownership.verify_private(path)
        with open(path, "rb") as handle:
            raw = handle.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            return None
        found = json.loads(raw.decode("utf-8")).get("clone")
    except (OSError, ValueError, AttributeError, PermissionError):
        return None
    return found if isinstance(found, str) and CLONE_RE.fullmatch(found) else None


def _publish(path: Path, text: str) -> bool:
    """Write `path` exclusively through an owner-only temp file; False when another process published first."""
    temp = path.with_name(f"{path.name}.{secrets.token_hex(6)}.tmp")
    try:
        descriptor = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(text.encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        ownership._restrict(temp, "the clone id file")
        try:
            os.link(temp, path)
        except FileExistsError:
            return False
        return True
    finally:
        try:
            temp.unlink()
        except FileNotFoundError:
            pass


def clone_id(root, *, create: bool = True) -> str | None:
    """This clone's id, created on first use. None when `<root>/.git` is not a directory (a submodule's `.git` is a
    file), when the file is a link or not owner-only, or when it can't be read or written: callers then mean "the
    person's default clone"."""
    git = Path(root) / ".git"
    try:
        if git.is_symlink() or not git.is_dir():
            return None
    except OSError:
        return None
    path = clone_path(root)
    if os.path.lexists(path):
        return _read(path)
    if not create:
        return None
    text = json.dumps({"clone": f"c-{secrets.token_hex(8)}",
                       "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}) + "\n"
    try:
        _publish(path, text)
    except OSError:
        return None
    return _read(path)
