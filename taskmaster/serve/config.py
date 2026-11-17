# User intent: the daemon's bind policy and settings: loopback by default, plaintext off-host only by explicit, loudly warned opt-in.
"""Settings and bind policy for `taskmaster serve`."""
from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 7470
DEFAULT_PATH = "/mcp"
# Every URL prefix the app serves to teammates; the shipped Caddyfile's `@taskmaster` matcher must list exactly these
# (tests/test_deploy_files.py), or a production route 404s at the proxy (team-server-025).
READ_PREFIX = "/read/v1"
WRITE_PREFIX = "/write/v1"
PUBLIC_PREFIXES = (DEFAULT_PATH, READ_PREFIX, WRITE_PREFIX)
GRACEFUL_SECONDS = 10
DEFAULT_SESSION_IDLE_SECONDS = 1800.0     # design D10; the MCP SDK's recommended value
EXIT_OK, EXIT_REFUSED, EXIT_UNAVAILABLE, EXIT_COORDINATOR_LOST = 0, 2, 69, 75

# team-server-006 hardening (design D8). Defaults are flags; the rest are constants, named so 015 can challenge them.
DEFAULT_MAX_BODY_BYTES = 2 * 1024 * 1024
DEFAULT_HEADER_TIMEOUT = 10.0
DEFAULT_BODY_TIMEOUT = 30.0
# Lingering close (D3a follow-up): after an early answer that closes, drain and discard the rest of the body for at
# most this long / this many bytes, so the client reads the answer instead of a reset. 64 KiB covers a typical
# tool-call body; a client that sends more, or for longer, is cut off.
DEFAULT_LINGER_SECONDS = 2.0
DEFAULT_LINGER_BYTES = 64 * 1024
DEFAULT_MAX_CONNECTIONS = 128
DEFAULT_RATE_PER_MINUTE = 240
DEFAULT_RATE_BURST = 120
DEFAULT_MAX_INFLIGHT = 32            # per key; must stay below max_connections (design D7)
# Calls that copy the whole store and run a member's SQL (security review I-1): at most this many per key at once
# (refused beyond it) and this many on the whole server (queued for up to HEAVY_QUEUE_SECONDS, then refused), so one
# key can hold neither the daemon's memory nor all of its cores. The operator is not counted.
HEAVY_TOOLS = frozenset({"backlog_query"})
# Sized with the query worker's memory cap: query_worker.py has the budget under the unit's MemoryMax.
HEAVY_PER_KEY = 1
HEAVY_TOTAL = 2
HEAVY_QUEUE_SECONDS = 10.0
DEFAULT_VIEWER_PORT = 7471           # the CLI default; ServeConfig's default is None (no viewer), so tests opt in
KEEP_ALIVE_SECONDS = 5
MAX_HEADER_BYTES = 16 * 1024
TRUSTED_PROXIES = ("127.0.0.1", "::1")
FAILURE_BUDGET = 20                  # refusals per peer key, refilled per minute (design D6)
FAILURE_PEERS = 4096
RETRY_AFTER_MAX = 3600
TRACKED_FILES_TTL = 30.0             # seconds the git-tracked set for /file/ is cached (design D10.7)
VIEWER_SESSION_IDLE_SECONDS = 8 * 3600
VIEWER_SESSION_MAX_SECONDS = 24 * 3600
VIEWER_MAX_SESSIONS = 256
VIEWER_MAX_SESSIONS_PER_KEY = 8      # one key's logins evict only its own oldest sessions (006 review I4)
VIEWER_LOGIN_MAX_BYTES = 4096
LOGIN_ALARM_PER_MINUTE = 100       # failed viewer logins, all peers, before one WARNING (028 section 3.1)
VIEWER_MAX_CONNECTIONS = 32
VIEWER_ORIGIN_RE = re.compile(r"^(https?)://([A-Za-z0-9.-]+|\[[0-9A-Fa-f:]+\])(:\d{1,5})?$")
_LOOPBACK_ORIGIN_HOSTS = frozenset({"localhost", "127.0.0.1", "[::1]"})

_LOG_LEVELS = ("debug", "info", "warning")
_PATH_RE = re.compile(r"^/[A-Za-z0-9._~/-]*$")
_HOST_RE = re.compile(r"^[A-Za-z0-9.-]+(:\d{1,5})?$")


class ServeRefused(Exception):
    exit_code = EXIT_REFUSED


class BindRefused(ServeRefused):
    pass


class ServeUnavailable(ServeRefused):
    exit_code = EXIT_UNAVAILABLE


INSECURE_BIND_WARNING = (
    "serving plaintext HTTP on {host}: the bearer token and all backlog data cross the network "
    "unencrypted, so anyone on the path can read them and replay the token. Bind 127.0.0.1 behind "
    "a TLS reverse proxy (Caddy) or an encrypted overlay (Tailscale, WireGuard, SSH tunnel) instead.")


@dataclass(frozen=True)
class ServeConfig:
    root: Path
    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    path: str = DEFAULT_PATH
    allowed_hosts: tuple[str, ...] = ()
    insecure_bind: bool = False
    log_level: str = "info"
    session_idle_seconds: float = DEFAULT_SESSION_IDLE_SECONDS
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES
    header_timeout: float = DEFAULT_HEADER_TIMEOUT
    body_timeout: float = DEFAULT_BODY_TIMEOUT
    linger_seconds: float = DEFAULT_LINGER_SECONDS
    linger_bytes: int = DEFAULT_LINGER_BYTES
    max_connections: int = DEFAULT_MAX_CONNECTIONS
    rate_per_minute: int = DEFAULT_RATE_PER_MINUTE
    rate_burst: int = DEFAULT_RATE_BURST
    max_inflight: int = DEFAULT_MAX_INFLIGHT
    viewer_port: int | None = None
    viewer_origins: tuple[str, ...] = ()
    # team-server-013 state export. 0 = off (tests and embedders opt in); None = the CLI default: every
    # DEFAULT_EXPORT_INTERVAL seconds, except on a linked worktree or a root that is not a Git checkout.
    export_interval: int | None = 0
    export_branch: str = "taskmaster-state"
    export_push: str | None = None
    # team-server-009: replace a running older daemon of this root (host-local; needs the operator token).
    force_upgrade: bool = False
    upgrade_timeout: float = 120.0


DEFAULT_EXPORT_INTERVAL = 900


def allowed_viewer_origins(config: ServeConfig, port: int) -> tuple[str, ...]:
    """The configured public origins plus the two loopback ones for SSH-forward use (design D10.3)."""
    origins = (*config.viewer_origins, f"http://127.0.0.1:{port}", f"http://localhost:{port}")
    return tuple(dict.fromkeys(origins))


def bind_host(host: str) -> str:
    if host == "localhost":
        return "127.0.0.1"
    try:
        return ipaddress.ip_address(host).compressed
    except ValueError:
        raise BindRefused(f"--host must be an IP address or localhost, not {host!r}") from None


def check_bind(host: str, *, insecure_bind: bool) -> str | None:
    ip = ipaddress.ip_address(bind_host(host))
    if ip.is_loopback:
        return None
    if not insecure_bind:
        raise BindRefused(
            f"refusing to bind {host}: it is not a loopback address and "
            "taskmaster serve speaks plaintext HTTP. Bind 127.0.0.1 behind a TLS reverse proxy, or pass "
            "--insecure-bind to accept the risk.")
    return INSECURE_BIND_WARNING.format(host=host)


def validate(config: ServeConfig) -> ServeConfig:
    timeout = config.upgrade_timeout
    if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not 0 < timeout < float("inf"):
        raise ServeRefused(f"--upgrade-timeout must be a finite number of seconds above 0, not {timeout!r}")
    port = config.port
    if isinstance(port, bool) or not isinstance(port, int) or not 0 <= port <= 65535:
        raise ServeRefused(f"--port must be an integer from 0 to 65535, not {port!r}")
    path = config.path
    if not isinstance(path, str) or not _PATH_RE.match(path) or path == "/rpc":
        raise ServeRefused(f"--path must start with '/', use only [A-Za-z0-9._~/-] and not be /rpc: {path!r}")
    if path.rstrip("/") == "/write" or path.startswith("/write/"):
        # /write/ is the teammates' hook write endpoint (team-server-008 D5); MCP may not shadow it.
        raise ServeRefused(f"--path may not be under /write, which takes teammates' hook writes: {path!r}")
    if path.rstrip("/") == "/read" or path.startswith("/read/"):
        # /read/ is the teammates' read endpoint (team-server-007 D1); MCP may not shadow it.
        raise ServeRefused(f"--path may not be under /read, which serves teammates' reads: {path!r}")
    for host in config.allowed_hosts:
        if not isinstance(host, str) or not _HOST_RE.match(host):
            raise ServeRefused(f"--allowed-host must be host or host:port, not {host!r}")
    if config.log_level not in _LOG_LEVELS:
        raise ServeRefused(f"--log-level must be one of {', '.join(_LOG_LEVELS)}, not {config.log_level!r}")
    idle = config.session_idle_seconds
    if isinstance(idle, bool) or not isinstance(idle, (int, float)) or not 1 <= idle <= 86400:
        raise ServeRefused(f"--session-idle-seconds must be between 1 and 86400, not {idle!r}")
    _check_range("--max-body-bytes", config.max_body_bytes, 65536, 67108864, integer=True)
    _check_range("--header-timeout", config.header_timeout, 1, 120)
    _check_range("--body-timeout", config.body_timeout, 1, 600)
    _check_range("--linger-seconds", config.linger_seconds, 0, 10)
    _check_range("--linger-bytes", config.linger_bytes, 0, 16 * 1024 * 1024, integer=True)
    _check_range("--max-connections", config.max_connections, 8, 10000, integer=True)
    _check_range("--rate-limit", config.rate_per_minute, 1, 100000, integer=True)
    _check_range("--rate-burst", config.rate_burst, 1, 100000, integer=True)
    _check_range("--max-inflight", config.max_inflight, 1, 10000, integer=True)
    if config.max_inflight >= config.max_connections:
        raise ServeRefused(f"--max-inflight {config.max_inflight} must be below --max-connections "
                           f"{config.max_connections}")
    if config.viewer_port is not None:
        _check_range("--viewer-port", config.viewer_port, 0, 65535, integer=True)
    for origin in config.viewer_origins:
        match = VIEWER_ORIGIN_RE.match(origin) if isinstance(origin, str) else None
        if match is None:
            raise ServeRefused(f"--viewer-origin must be scheme://host[:port] with no path, not {origin!r}")
        if match.group(1) != "https" and match.group(2).lower() not in _LOOPBACK_ORIGIN_HOSTS:
            raise ServeRefused(f"--viewer-origin {origin} must be https (http only for localhost, 127.0.0.1, [::1])")
        # The viewer and the MCP host share nothing: a path prefix or a shared host would share the cookie site (028 D11).
        host = match.group(2).lower()
        if host not in _LOOPBACK_ORIGIN_HOSTS and any(allowed.lower().split(":")[0] == host for allowed in config.allowed_hosts):
            raise ServeRefused(f"--viewer-origin {origin} must be its own host (e.g. viewer.<domain>), not an --allowed-host")
    interval = config.export_interval
    if interval is not None and interval != 0:
        _check_range("--export-interval", interval, 60, 86400, integer=True)
    if interval == 0 and config.export_push is not None:
        raise ServeRefused("--export-push needs the state export on (--export-interval is 0)")
    if not isinstance(config.export_branch, str) or (config.export_push is not None
                                                     and not isinstance(config.export_push, str)):
        raise ServeRefused("--export-branch and --export-push must be names")
    return config


def _check_range(flag: str, value, low, high, *, integer=False) -> None:
    kinds = (int,) if integer else (int, float)
    if isinstance(value, bool) or not isinstance(value, kinds) or not low <= value <= high:
        raise ServeRefused(f"{flag} must be {'an integer ' if integer else ''}from {low} to {high}, not {value!r}")
