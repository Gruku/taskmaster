# User intent: nobody reaches the served tools without a token; one operator token now, and a seam so per-person keys (team-server-002) plug in without touching the transport.
"""Operator token storage, the authenticator seam and the FastMCP token verifier."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
import re
import secrets
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from fastmcp.server.auth import AccessToken, TokenVerifier

from taskmaster.coordinator import ownership

from .config import ServeRefused
from .principals import KEY_RE, PrincipalRegistry, RegistryUnavailable

SERVE_DIR = "serve"                        # .taskmaster/local/serve/
TOKEN_FILE = "operator.token"
TOKEN_PREFIX = "tm_op_"
TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{32,256}")
LOG = logging.getLogger("taskmaster.serve")


OPERATOR_KEY_ID = "operator"
_DUMMY_DIGEST = hashlib.sha256(b"taskmaster: no such key").digest()


@dataclass(frozen=True)
class Principal:
    handle: str
    role: str                              # "operator" | "member"
    key_id: str                            # 16 lowercase hex, or "operator" for the bootstrap token


OPERATOR = Principal("operator", "operator", OPERATOR_KEY_ID)


def _safe(value) -> str:
    from .app import log_safe              # app imports auth, so this import happens at call time
    return log_safe(value)


class Authenticator(Protocol):
    def authenticate(self, token: str) -> Principal | None: ...


def serve_directory(root: Path) -> Path:
    try:
        return ownership.private_directory(root, SERVE_DIR)
    except PermissionError as exc:
        raise ServeRefused(f"{root}/.taskmaster/local/serve must be owned by and private to this user: {exc}") from exc


def token_path(root: Path) -> Path:
    return serve_directory(root) / TOKEN_FILE


def read_operator_token(path: Path) -> str:
    try:
        ownership.verify_private(path)
    except PermissionError as exc:
        raise ServeRefused(f"{path} must be owner-only (chmod 600 on Linux): {exc}") from exc
    value = path.read_text(encoding="utf-8").strip()
    if not TOKEN_RE.fullmatch(value):
        raise ServeRefused(
            f"{path} does not hold a valid operator token; run `taskmaster serve token --root <root> --rotate`")
    return value


def _new_token() -> str:
    return TOKEN_PREFIX + secrets.token_urlsafe(32)


def ensure_operator_token(root: Path) -> tuple[str, bool]:
    path = token_path(root)
    if path.exists():
        return read_operator_token(path), False
    token = _new_token()
    try:
        _write_token(path, token, exclusive=True)
    except FileExistsError:
        return read_operator_token(path), False
    return token, True


def rotate_operator_token(root: Path) -> str:
    token = _new_token()
    _write_token(token_path(root), token, exclusive=False)
    return token


def _write_token(path: Path, token: str, *, exclusive: bool) -> None:
    temp = path.with_name(f"{TOKEN_FILE}.{secrets.token_hex(8)}.tmp")
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0)
    fd = os.open(temp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write((token + "\n").encode("utf-8"))
            handle.flush()
            os.fsync(handle.fileno())
        if exclusive:
            os.link(temp, path)             # raises FileExistsError when a concurrent creator won
        else:
            os.replace(temp, path)
    finally:
        try:
            os.unlink(temp)
        except FileNotFoundError:
            pass


class OperatorTokenAuthenticator:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.Lock()
        self._key = None
        self._digest: bytes | None = None

    def authenticate(self, token: str) -> Principal | None:
        # A transient OSError (EMFILE under a connection flood, a Windows sharing violation) refuses
        # only this request and leaves the cache alone, so the next request retries; caching it would
        # lock the operator out until the file next changed. Only content/permission refusals
        # (ServeRefused) are cached.
        with self._lock:
            try:
                info = self.path.stat()
            except OSError as exc:
                LOG.error("operator token unreadable, refusing this request: %s", exc)
                return None
            key = (info.st_mtime_ns, info.st_size, info.st_ino)
            if key != self._key:
                try:
                    digest = hashlib.sha256(read_operator_token(self.path).encode()).digest()
                except ServeRefused as exc:
                    self._key, self._digest = key, None
                    LOG.error("operator token unusable, refusing every request: %s", exc)
                except OSError as exc:
                    LOG.error("operator token unreadable, refusing this request: %s", exc)
                    return None
                else:
                    self._key, self._digest = key, digest
            digest = self._digest
        if digest is None or not token:
            return None
        if hmac.compare_digest(hashlib.sha256(token.encode()).digest(), digest):
            return OPERATOR
        return None


class KeyAuthenticator:
    """Member keys by id with one constant-time compare; anything else is the operator bootstrap token."""

    def __init__(self, operator: OperatorTokenAuthenticator, registry: PrincipalRegistry):
        self.operator = operator
        self.registry = registry
        self._seen: set[str] = set()
        self._seen_lock = threading.Lock()

    def authenticate(self, token: str) -> Principal | None:
        match = KEY_RE.fullmatch(token or "")
        if match is None:
            return self.operator.authenticate(token)
        key_id = match.group(1)
        unavailable = False
        try:
            record = self.registry.lookup(key_id)
        except RegistryUnavailable:
            record, unavailable = None, True
        active = record is not None and record.active
        digest = record.digest if active else _DUMMY_DIGEST
        ok = hmac.compare_digest(hashlib.sha256(token.encode()).digest(), digest)
        if ok and active:
            with self._seen_lock:
                first = key_id not in self._seen
                self._seen.add(key_id)
            if first:
                LOG.info("key %s (%s) authenticated", _safe(key_id), _safe(record.handle))
            return Principal(record.handle, record.role, record.key_id)
        reason = ("key list unavailable" if unavailable else "unknown" if record is None
                  else "revoked" if not record.active else "wrong secret")
        LOG.warning("refused key %s: %s", _safe(key_id), reason)
        return None


class ServeTokenVerifier(TokenVerifier):
    def __init__(self, authenticator: Authenticator):
        super().__init__()
        self.authenticator = authenticator

    async def verify_token(self, token: str) -> AccessToken | None:
        principal = self.authenticator.authenticate(token)
        if principal is None:
            return None
        return AccessToken(
            token=f"key:{principal.key_id}", client_id=principal.handle, subject=principal.key_id,
            scopes=[], expires_at=None,
            claims={"taskmaster_principal": principal.handle, "taskmaster_role": principal.role,
                    "taskmaster_key_id": principal.key_id})


def principal_from_access_token(access_token) -> Principal | None:
    claims = getattr(access_token, "claims", None) or {}
    handle, role, key_id = claims.get("taskmaster_principal"), claims.get("taskmaster_role"), claims.get("taskmaster_key_id")
    if handle and role and key_id:
        return Principal(handle, role, key_id)
    if handle == OPERATOR.handle and role == OPERATOR.role and not key_id:
        return OPERATOR
    return None


def current_principal() -> Principal | None:
    from fastmcp.server.dependencies import get_access_token
    return principal_from_access_token(get_access_token())


def current_key_id() -> str | None:
    """The authenticated key of the HTTP request being served; None outside a request."""
    from fastmcp.server.dependencies import get_http_request
    try:
        request = get_http_request()
    except RuntimeError:
        return None
    principal = current_principal()
    if principal is None:
        raise PermissionError("authenticated principal missing")
    if principal.key_id == OPERATOR_KEY_ID and not is_host_local(request.scope):
        # RequirePrincipalMiddleware already refuses this (002 D7); re-checked where the allowlist
        # reads the principal, so "operator" always means the host (team-server-005 D8).
        raise PermissionError("the operator bootstrap token works only on the host")
    return principal.key_id


VIEWER_KEY_ID: ContextVar[str | None] = ContextVar("taskmaster_viewer_key_id", default=None)
_KEY_ID_OR_OPERATOR = re.compile(r"operator|[0-9a-f]{16}")


def request_key_id() -> str | None:
    """The only callable ever installed as client.ORIGIN_PROVIDER (by the daemon)."""
    return current_key_id() or VIEWER_KEY_ID.get()


@contextmanager
def viewer_principal(key_id: str):
    """For the viewer's handlers: stamp the writes of this request, in this context only."""
    if not isinstance(key_id, str) or not _KEY_ID_OR_OPERATOR.fullmatch(key_id):
        raise ValueError("invalid key id")
    token = VIEWER_KEY_ID.set(key_id)
    try:
        yield
    finally:
        VIEWER_KEY_ID.reset(token)


FORWARDING_HEADERS = (b"x-forwarded-for", b"forwarded", b"x-real-ip")
HOST_LOCAL_NAMES = frozenset({"127.0.0.1", "localhost", "::1"})


def _host_name(value: bytes) -> str | None:
    text = value.decode("latin-1").strip().lower()
    if text.startswith("["):
        end = text.find("]")
        rest = text[end + 1:] if end > 0 else None
        if rest is None or (rest and not re.fullmatch(r":\d{1,5}", rest)):
            return None
        return text[1:end]
    if text.count(":") > 1:
        return None
    return text.split(":", 1)[0]


def is_host_local(scope) -> bool:
    """The one host-local check: a loopback peer, no forwarding header, and a loopback Host."""
    try:
        client = scope.get("client")
        if not client or not ipaddress.ip_address(client[0]).is_loopback:
            return False
        names, hosts = [], []
        for name, value in scope.get("headers") or []:
            lowered = name.lower()
            names.append(lowered)
            if lowered == b"host":
                hosts.append(value)
        if any(name in FORWARDING_HEADERS for name in names):
            return False
        return len(hosts) == 1 and _host_name(hosts[0]) in HOST_LOCAL_NAMES
    except Exception:  # noqa: BLE001 - malformed input is simply not host-local
        return False
