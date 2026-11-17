# User intent: the one way a teammate's hook reaches the team server: locate it from the clone's marker, find the
# key, open the cache, and keep the hook's own logs and dedupe state in the user's cache dir, never in a
# `.taskmaster/` the clone must not have (team-server-007 D8-D13).
"""`connect(root)` for server-mode hooks, and where their state lives."""
from __future__ import annotations

import time
from dataclasses import dataclass
from pathlib import Path

from . import dirs
from .cache import Cache
from .client import Client, ReadUnavailable
from .keys import NoKey, load_key
from .locate import ServerRef, locate

LOG_MAX_BYTES = 1024 * 1024
LOG_KEEP_BYTES = 512 * 1024


@dataclass
class Session:
    server: ServerRef
    client: Client
    state: Path | None          # <cache>/remote/<server_id>/hooks/, or None when it can't be made private


def state_dir(server: ServerRef) -> Path | None:
    try:
        return dirs.private_chain(dirs.cache_base(), "remote", server.server_id, "hooks")
    except OSError:
        return None


def stamp_queue(server: ServerRef):
    """The per-server merge-stamp queue in the user's cache dir (owner-only), or None when it can't be made private.

    Per server, not per key, so a rotated key replays what the old one queued; entries hold no secret."""
    from taskmaster.native_routing.merge_stamps import QueueDir
    try:
        path = dirs.private_chain(dirs.cache_base(), "remote", server.server_id, "stamps")
    except OSError:
        return None
    state = state_dir(server)
    return QueueDir(path, (state or path) / "hook.log")


def server_of(root) -> ServerRef:
    from taskmaster.server_marker import ServerConfigError
    try:
        server = locate(root)
    except ServerConfigError as exc:
        raise ReadUnavailable("config", str(exc)) from None
    if server is None:
        raise ReadUnavailable("config", "not a server-mode project")
    return server


def connect(root, *, log=None) -> Session:
    """A client for the server `root`'s marker names. ReadUnavailable(config | no-key) when there is none."""
    server = server_of(root)
    try:
        key = load_key(server)
    except NoKey as exc:
        raise ReadUnavailable("no-key", str(exc)) from None
    cache = Cache(server.server_id, key.key_id, log=log)
    return Session(server, Client(server, key, cache, log=log), state_dir(server))


def append_log(state: Path | None, hook: str, reason: str) -> None:
    """One line in the server's hook log. Never raises."""
    if state is None:
        return
    try:
        path = state / "hook.log"
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"{time.time()} {hook}: {reason}\n")
        if path.stat().st_size > LOG_MAX_BYTES:
            path.write_bytes(path.read_bytes()[-LOG_KEEP_BYTES:])
    except Exception:  # noqa: BLE001
        pass
