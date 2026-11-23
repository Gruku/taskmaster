# User intent: the one small stdlib-only module both sides of local server mode share (the serve --local server and
# the stdio bridge), so the contract in docs/plans/2026-11-22-local-server-plan.md section 0 lives in one place.
"""Shared contract helpers for local server mode: discovery, result files, headers, status values, probes.

Imports only the standard library and taskmaster modules that import no third-party package; the bridge
runs without the server's environment, so tests/test_localserve.py pins that.
"""
from __future__ import annotations

import json
import os
import secrets
import time
from contextlib import closing, suppress
from pathlib import Path
from urllib.parse import quote, unquote

from taskmaster import server_fence, server_marker
from taskmaster.coordinator.ownership import private_directory, verify_private
from taskmaster.coordinator.protocol import (  # noqa: F401  (re-exported for both sides)
    compare_versions, declared_version, package_digest, same_build, valid_build)
from taskmaster.root import db_path

# -- contract constants (plan section 0) -----------------------------------------------------------

FORMAT = 1
PROFILE = "local"
SERVE_DIR = server_fence.SERVE_DIR            # .taskmaster/local/serve
DISCOVERY_NAME = "local.json"
SESSION_KEY_NAME = "session.key"

STATE_SERVING = "serving"
STATE_DRAINING = "draining"

STATUS_READY = "ready"
STATUS_LEASE_HELD = "lease_held"
STATUS_STORE_OWNED_BUSY = "store_owned_busy"
STATUS_REFUSED = "refused"
STATUS_ERROR = "error"
RESULT_STATUSES = (STATUS_READY, STATUS_LEASE_HELD, STATUS_STORE_OWNED_BUSY, STATUS_REFUSED, STATUS_ERROR)

HEADER_AUTHORIZATION = "Authorization"
HEADER_SESSION = "X-Taskmaster-Session"
HEADER_SESSION_NONCE = "X-Taskmaster-Session-Nonce"
HEADER_CWD = "X-Taskmaster-Cwd"

PATH_STATUS = "/local/v1/status"
PATH_RETIRE = "/local/v1/retire"

RESULT_WAIT_SECONDS = 15.0
RESULT_POLL_SECONDS = 0.05


# -- private atomic files --------------------------------------------------------------------------

def write_private_json(path, record: dict) -> Path:
    """Atomically write `record` as owner-only JSON: temp file in the same directory, fsync, replace."""
    path = Path(path)
    temp = path.with_name(f"{path.name}.{secrets.token_hex(6)}.tmp")
    descriptor = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(record, stream, sort_keys=True)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp, path)
    finally:
        with suppress(FileNotFoundError):
            temp.unlink()
    return path


def read_private_json(path):
    """The JSON object at `path`, or None when absent, partial or not an object.

    Raises PermissionError when the file or its directory is not owner-only, a link, or foreign
    (the coordinator's verify_private): a discovery file or token the bridge would trust must be ours.
    """
    path = Path(path)
    try:
        verify_private(path)
        verify_private(path.parent)
        raw = path.read_bytes()
    except FileNotFoundError:
        return None
    try:
        value = json.loads(raw.decode("utf-8"))
    except ValueError:
        return None
    return value if isinstance(value, dict) else None


# -- discovery -------------------------------------------------------------------------------------

def serve_dir(root) -> Path:
    return Path(root) / ".taskmaster" / "local" / SERVE_DIR


def discovery_path(root) -> Path:
    return serve_dir(root) / DISCOVERY_NAME


def session_key_path(root) -> Path:
    return serve_dir(root) / SESSION_KEY_NAME


def write_discovery(root, *, state: str, url: str, pid: int, plugin_dir, token_file, build: dict,
                    started: str, nonce: str) -> Path:
    """Publish local.json (server side). Creates the owner-only serve directory if needed."""
    if state not in (STATE_SERVING, STATE_DRAINING):
        raise ValueError(f"invalid discovery state: {state!r}")
    private_directory(Path(root), SERVE_DIR)
    record = {"format": FORMAT, "profile": PROFILE, "state": state, "url": url, "pid": pid,
              "root": str(root), "plugin_dir": str(plugin_dir), "token_file": str(token_file),
              "build": {"version": build["version"], "digest": build["digest"]},
              "started": started, "nonce": nonce}
    return write_private_json(discovery_path(root), record)


def read_discovery(root):
    """The discovery record when present, well-formed and not stale; else None.

    Stale means the host lease is free (the server died without cleaning up). A refused or
    unreadable record raises PermissionError, as read_private_json does.
    """
    record = read_private_json(discovery_path(root))
    if record is None or record.get("format") != FORMAT or record.get("profile") != PROFILE:
        return None
    if record.get("state") not in (STATE_SERVING, STATE_DRAINING):
        return None
    if not all(isinstance(record.get(key), str) for key in ("url", "token_file", "plugin_dir", "root")):
        return None
    if not isinstance(record.get("pid"), int) or not valid_build(record.get("build")):
        return None
    if not host_lease_held(root):
        return None
    return record


def remove_discovery(root) -> None:
    with suppress(FileNotFoundError):
        discovery_path(root).unlink()


def host_lease_held(root) -> bool:
    """Whether a process holds serve/host.lock now (a probe; see server_fence.host_lease_held)."""
    return server_fence.host_lease_held(root)


# -- result file (launch outcome channel) ----------------------------------------------------------

def write_result(path, status: str, reason: str = "", *, pid: int | None = None, url: str | None = None) -> Path:
    if status not in RESULT_STATUSES:
        raise ValueError(f"invalid result status: {status!r}")
    record = {"status": status, "reason": reason, "pid": os.getpid() if pid is None else pid}
    if url is not None:
        record["url"] = url
    return write_private_json(path, record)


def read_result(path, *, timeout: float = RESULT_WAIT_SECONDS, poll: float = RESULT_POLL_SECONDS):
    """Poll for the server's outcome until `timeout`. A missing, partial or invalid file keeps polling;
    on the deadline returns {"status": "error", "reason": "timeout..."} (the bridge falls back)."""
    path = Path(path)
    deadline = time.monotonic() + timeout
    while True:
        with suppress(OSError, PermissionError):
            record = read_private_json(path)
            if record is not None and record.get("status") in RESULT_STATUSES:
                return record
        if time.monotonic() >= deadline:
            return {"status": STATUS_ERROR, "reason": f"no launch result within {timeout:g}s", "pid": None}
        time.sleep(poll)


# -- header values ---------------------------------------------------------------------------------

def encode_cwd(cwd) -> str:
    return quote(os.fspath(cwd), safe="/:")


def decode_cwd(value: str) -> str:
    return unquote(value)


# -- marker / latch and legacy-store checks --------------------------------------------------------

def server_mode_reason(root):
    """Why local mode must not run here (a team-server marker or latch at the root or its repo roots), or None.

    Read-only: unlike server_fence.refusal it never records a latch, because a local server and its
    bridge must never write a marker or latch.
    """
    for candidate in server_fence.fence_roots(root):
        try:
            marker = server_marker.read_marker(candidate)
            if marker is None:
                marker = server_marker.read_latch(candidate)
        except server_marker.ServerConfigError as exc:
            return f"server marker or latch at {candidate} is unreadable: {exc}"
        if marker is not None:
            return f"project is in server mode ({marker.url})"
    return None


def is_native_store(root) -> bool:
    """True when `root` has a ready native store (schema 2, manifest ready); False for a missing or legacy one.

    Read-only, stdlib-only counterpart of native_routing.gate.native_database, same admission rule
    (native.db.assert_native); a damaged or unclassifiable database counts as not native.
    """
    import sqlite3
    from taskmaster.admission import UnsupportedStoreError
    from taskmaster.native.db import assert_native
    path = db_path(Path(root) / ".taskmaster")
    if not path.is_file():
        return False
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, isolation_level=None, timeout=5)) as connection:
            connection.execute("BEGIN")
            try:
                assert_native(connection)
            finally:
                connection.rollback()
    except (sqlite3.Error, UnsupportedStoreError):
        return False
    return True


def legacy_store(root) -> bool:
    """True when local mode must refuse the store: a project store that is not a ready native one."""
    return not is_native_store(root)
