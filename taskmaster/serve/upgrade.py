# User intent: let the operator replace a hand-run `taskmaster serve` with a newer build (`serve --force-upgrade`)
# without letting any other local process retire the daemon: the retire carries a proof only someone who can
# read the owner-only operator token can compute (team-server-009 section 8).
"""The retire proof and the new build's hand-over to a running older daemon.

`upgrade_proof` is shared by both sides. `hand_over` runs in the new build's `taskmaster serve --force-upgrade`
process: it reads the running owner's discovery record without opening the store, asks it to retire, and waits
until the host lease (and the coordinator's ownership lock) are free.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from pathlib import Path

from taskmaster import server_fence
from taskmaster.coordinator.client import Client
from taskmaster.coordinator.ownership import ownership_held
from taskmaster.coordinator.protocol import (HandshakeError, ServiceUnavailable, build_identity, compare_versions,
                                             describe, same_build, valid_build)

from .config import ServeRefused, ServeUnavailable

PROOF_RE = re.compile(r"[0-9a-f]{64}")
DEFAULT_TIMEOUT = 120.0
POLL_SECONDS = 0.1
BUSY_RETRY_SECONDS = 0.25
PREDATES_TEXT = ("the running daemon predates --force-upgrade; stop it (`systemctl stop taskmaster`) and start "
                 "this build")
NON_DAEMON_TEXT = ("the host lease is held by a non-daemon serve process (export or init); wait for it to "
                   "finish")


def proof_key(token: str) -> bytes:
    return hashlib.sha256(token.encode("utf-8")).digest()


def upgrade_proof(key: bytes, nonce: str, requester_build: dict) -> str:
    """HMAC-SHA256 over this daemon generation's nonce and the requester's build, keyed by the token digest."""
    canonical = json.dumps(requester_build, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    message = b"taskmaster-force-upgrade\0" + nonce.encode("utf-8") + b"\0" + canonical.encode("utf-8")
    return hmac.new(key, message, hashlib.sha256).hexdigest()


def read_token(root) -> str:
    """The operator token, read without creating it; ServeRefused when it is missing or unsafe."""
    from .auth import TOKEN_FILE, SERVE_DIR, read_operator_token
    path = Path(root) / ".taskmaster" / "local" / SERVE_DIR / TOKEN_FILE
    if not path.is_file():
        raise ServeRefused(f"{path} does not exist; the daemon that holds {root} has no operator token to prove "
                           "a hand-over with")
    return read_operator_token(path)


class _DiscoveryClient(Client):
    """A `Client` that never opens the store: its identity comes from the discovery record.

    It exists so the new build can ask the running owner to retire without `identify()` (G3 would refuse it
    on a marker root). Its only public method is `retire`.
    """

    def __init__(self, root, *, timeout=15):
        self.root = Path(root).resolve(strict=True)
        self.identity = {}
        self.build = build_identity()
        self.autostart, self.visibility, self.timeout = False, "native", timeout
        self._deadline = None
        record = Client._discovery(self)            # an empty identity matches any record
        self.identity = {key: record.get(key) for key in ("root", "store_id", "schema", "protocol",
                                                           "service_protocol")}
        self.record = Client._discovery(self)       # the same checks, now against the record's own identity

    def retire(self, proof: str) -> dict:
        return self._send(self.record, "retire", identity=dict(self.identity, schema=self.record.get("schema")),
                          force_upgrade=proof)


def _sleep_until(deadline: float, seconds: float) -> None:
    time.sleep(max(0.0, min(seconds, deadline - time.monotonic())))


def hand_over(root, timeout: float = DEFAULT_TIMEOUT, *, lease_is_ours: bool = False) -> None:
    """Retire the running owner of `root` and wait for it to leave. Raises ServeRefused (exit 2) or
    ServeUnavailable (exit 69); returns when the lease and the ownership lock are free (or, with
    `lease_is_ours`, when the ownership lock is free)."""
    root = Path(root)
    deadline = time.monotonic() + timeout
    if not lease_is_ours and not ownership_held(root):
        # No coordinator is listening: any discovery.json is stale and its port may be someone else's.
        raise ServeUnavailable(NON_DAEMON_TEXT)
    try:
        client = _DiscoveryClient(root)
    except FileNotFoundError:
        raise ServeUnavailable("the lock is held but no coordinator has published its address yet; retry") from None
    except (HandshakeError, ServiceUnavailable, OSError) as exc:
        raise ServeUnavailable(f"cannot read the running owner's address: {exc}") from None
    theirs = client.record.get("build")
    if not valid_build(theirs):
        raise ServeUnavailable("the running owner predates the build handshake and cannot be retired; stop it")
    if same_build(theirs, client.build) and not lease_is_ours:
        raise ServeRefused(f"a daemon of this build ({describe(client.build)}) already serves {root}")
    if compare_versions(client.build, theirs) == -1:
        raise ServeRefused(f"the running build {describe(theirs)} is newer than this one "
                           f"({describe(client.build)}); will not downgrade it. Stop it and start the older build "
                           "deliberately")
    try:
        proof = upgrade_proof(proof_key(read_token(root)), client.record["nonce"], client.build)
    except ServeRefused:
        if not lease_is_ours:
            raise
        proof = "0" * 64          # a session coordinator ignores it: its own retire policy decides
    while True:
        try:
            answer = client.retire(proof)
        except (ConnectionError, TimeoutError, ServiceUnavailable, OSError, HandshakeError):
            answer = {"state": "unreachable"}    # it may just have exited; the waits below decide
        except ValueError as exc:
            raise ServeRefused(f"the running owner refused: {exc}") from None
        state = answer.get("state")
        if state == "retiring" or state == "unreachable":
            break
        if state == "refused" and lease_is_ours and same_build(theirs, client.build):
            from taskmaster import server_marker
            if server_marker.server_mode(root) is None:
                raise ServeRefused(f"a session coordinator of this build holds {root}'s ownership lock and will not "
                                   f"retire; stop it with `taskmaster coordinator stop --root {root}`")
            break                  # a same-build stray: its dispatch met the fence and stops itself (M10)
        if state == "refused":
            reason = str(answer.get("reason", ""))
            if "never retired by a client" in reason:
                raise ServeUnavailable(PREDATES_TEXT)
            raise ServeRefused(f"the running owner refused to retire: {reason}")
        if time.monotonic() >= deadline:
            raise ServeUnavailable(f"build {describe(theirs)} is still busy after {timeout:g} s; retry, or stop it")
        _sleep_until(deadline, BUSY_RETRY_SECONDS)
    while time.monotonic() < deadline:
        free = not ownership_held(root) and (lease_is_ours or not server_fence.host_lease_held(root))
        if free:
            return
        time.sleep(POLL_SECONDS)
    raise ServeUnavailable(f"build {describe(theirs)} is still draining after {timeout:g} s; retry, or stop it")
