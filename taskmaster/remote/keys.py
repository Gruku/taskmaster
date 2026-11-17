# User intent: find this teammate's key for exactly the server their clone names, and never hand it to any other
# origin, even when a branch rewrites the tracked marker (team-server-007 D9, review I1).
"""Key lookup: an owner-only key file per canonical origin, or an env key bound to one origin."""
from __future__ import annotations

import hashlib
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path

from taskmaster.coordinator import ownership

from . import dirs
from .locate import ServerRef, canonical_origin, server_ref

# The member key format of team-server-002 (serve/principals.py KEY_RE); a test pins the two together.
KEY_RE = re.compile(r"tm_u_([0-9a-f]{16})_([A-Za-z0-9_-]{43})")
OPERATOR_PREFIX = "tm_op_"
ENV_KEY, ENV_URL = "TASKMASTER_SERVER_KEY", "TASKMASTER_SERVER_URL"


class NoKey(Exception):
    """No usable key for this server; the message names the fix. Nothing was sent."""


@dataclass(frozen=True)
class Key:
    value: str
    key_id: str
    marker: str          # identifies the key's source, for the refused entry (never the key itself)
    source: str          # "env" or "file"

    def __repr__(self) -> str:      # never print the secret
        return f"Key(key_id={self.key_id!r}, source={self.source!r})"


def key_path(server: ServerRef, env=None) -> Path:
    return dirs.config_base(env) / "keys" / f"{server.server_id}.key"


def _check(value: str, where: str) -> str:
    value = value.strip()
    if value.startswith(OPERATOR_PREFIX):
        raise NoKey(f"{where} holds the operator bootstrap token, which works only on the host; "
                    "ask the operator for your own key (taskmaster serve add-user)")
    match = KEY_RE.fullmatch(value)
    if match is None:
        raise NoKey(f"{where} does not hold a taskmaster key (tm_u_...)")
    return match.group(1)


def load_key(server: ServerRef, env=None) -> Key:
    env = os.environ if env is None else env
    value = env.get(ENV_KEY)
    if value:
        bound = env.get(ENV_URL)
        try:
            bound_origin = canonical_origin(bound) if bound else None
        except ValueError:
            bound_origin = None
        if bound_origin != server.origin:
            raise NoKey(f"{ENV_KEY} is bound to {bound_origin or '(no ' + ENV_URL + ')'}, but this clone's "
                        f"marker names {server.origin}; nothing was sent")
        key_id = _check(value, ENV_KEY)
        return Key(value.strip(), key_id, "env:" + hashlib.sha256(value.strip().encode()).hexdigest()[:16], "env")
    path = key_path(server, env)
    if not os.path.lexists(path):
        raise NoKey(f"no key for {server.origin}; run: taskmaster remote set-key --url {server.url}")
    try:
        ownership.verify_private(path)
        ownership.verify_private(path.parent)
    except (PermissionError, OSError) as exc:
        raise NoKey(f"{path} must be owner-only and not a link ({exc}); run taskmaster remote set-key again") from None
    try:
        info = path.stat()
        value = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise NoKey(f"{path} cannot be read ({exc.strerror or exc})") from None
    key_id = _check(value, str(path))
    return Key(value.strip(), key_id, f"file:{info.st_mtime_ns}:{info.st_size}", "file")


def write_key(url: str, value: str, env=None) -> Path:
    """Validate `value` and store it owner-only for the server `url` names. Returns the file's path."""
    server = server_ref(url)
    _check(value, "the key")
    folder = dirs.private_chain(dirs.config_base(env), "keys")
    path = folder / f"{server.server_id}.key"
    temp = folder / f".{server.server_id}.{secrets.token_hex(6)}.tmp"
    fd = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0), 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write((value.strip() + "\n").encode("utf-8"))
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()
    return path
