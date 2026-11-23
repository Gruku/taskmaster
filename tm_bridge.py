# User intent: every Claude Code session on a taskmaster project talks to ONE shared hidden per-project server through this
# tiny stdlib-only stdio bridge (RAM: ~15 MB per session instead of ~120 MB), seamlessly, never opening a console window.
# /// script
# requires-python = ">=3.11"
# dependencies = ["anyio", "fastmcp>=3.4,<4", "h11>=0.16", "httpx", "mcp>=1.28,<2", "pydantic>=2", "pyyaml", "starlette>=1.0.1,<2", "uvicorn>=0.35,<1"]
# ///
"""The plugin's "tm" MCP entry: a stdio <-> streamable-HTTP relay to the project's local taskmaster server.

Spec: docs/specs/2026-11-22-local-server-design.md section 4; contract: docs/plans/2026-11-22-local-server-plan.md section 0.
Standard library plus taskmaster.localserve / taskmaster.root only (the bridge runs without the server's environment; the
script header above exists only so `uv run --script tm_bridge.py --print-python` can name the server's interpreter).
When the local server cannot be had it relays to today's `uv run backlog_server.py` instead (the fallback).
"""
from __future__ import annotations

import sys
if "ssl" not in sys.modules:                  # loopback HTTP only: do not pay ~3 MB for the TLS stack http.client would import
    sys.modules["ssl"] = None
import hashlib
import http.client
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlsplit

PLUGIN_DIR = Path(__file__).resolve().parent
SERVER_NAME = "tm"
CONNECT_TIMEOUT = 5.0
MAX_BODY_BYTES = 64 * 1024 * 1024    # one response document; a server that sends more is broken or hostile
MAX_LINE_BYTES = 16 * 1024 * 1024    # one SSE line
LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1")
KNOWN_PROTOCOLS = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")   # the MCP versions the server's SDK negotiates
SHORT_TIMEOUT = 30.0           # initialize, lists, ping and every other non-tool request; tools/call has none
STATUS_TIMEOUT = 3.0
RECONNECT_BUDGET = 45.0        # how long one request waits for the server to come back before it fails
DRAIN_WAIT = 30.0              # a retiring server finishes in-flight calls first
LEASE_HELD_TRIES = 3
SUCCESSOR_WAIT = 30.0              # a bridge that did not retire the server waits this long for its successor before launching its own
MAX_RETIRES = 2                    # upgrade day: how often one bridge replaces an older build that a slower bridge relaunched
RETRY_ATTEMPTS = 4
LAUNCH_BOUND = 180.0               # cold uv environment (first session after an install/upgrade) needs ~30 s: wait this long for a launch
LIST_HOLD = 12.0                   # with no cached tool list, tools/list waits this long, then answers empty and sends list_changed later
HOLD_BOUND = 180.0                 # a request that arrives before the upstream session is ready waits this long instead of failing
_ENV_LOCK = threading.Lock()       # one uv environment build at a time in this process: the stdio fallback never races a launch's build
DRAINING_TEXT = "is draining"       # the 503 body of serve/local.py's drain refusal
FALLBACK_FAILURES = 3
FALLBACK_WINDOW = 60.0
GET_BACKOFF = (0.2, 0.5, 1.0, 2.0, 3.0, 5.0)
RECONNECT_BACKOFF_START = 0.2      # first wait between eager reconnect attempts; doubles each try
RECONNECT_BACKOFF_CAP = 2.0
GET_NON200_STEP = 2.0              # a non-200 answer to the stream GET waits step * consecutive failures, capped
GET_NON200_CAP = 30.0
WIN = os.name == "nt"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def _ls():
    """taskmaster.localserve, imported late so the --print-python and --launcher modes stay cheap."""
    if str(PLUGIN_DIR) not in sys.path:
        sys.path.insert(0, str(PLUGIN_DIR))
    from taskmaster import localserve
    return localserve


class Unsent(Exception):
    """The request provably never reached the server (connect failed, or a 404 answered before dispatch)."""


class Ambiguous(Exception):
    """The request may have been received: the connection failed after it was sent."""


class ServerUnavailable(Exception):
    """No usable local server this time; the text says why."""


class Log:
    """Once-only notes to stderr (Claude Code shows them in its MCP log) plus an optional debug file."""

    def __init__(self, path=None, stream=None):
        self.path, self.stream, self.seen, self.lock = path, stream if stream is not None else sys.stderr, set(), threading.Lock()

    def once(self, text):
        with self.lock:
            if text in self.seen:
                return
            self.seen.add(text)
        self.note(text, always=True)

    def note(self, text, always=False):
        line = f"tm-bridge: {text}"
        if always:
            try:
                print(line, file=self.stream, flush=True)
            except (OSError, ValueError):
                pass
        if self.path:
            try:
                with open(self.path, "a", encoding="utf-8") as out:
                    out.write(time.strftime("%H:%M:%S ") + line + "\n")
            except OSError:
                pass


# -- opt-outs and the decision to use the local server --------------------------------------------------------------

def fallback_reason(root, env=None):
    """Why this session must use today's stdio server, or None when the local server applies. The rule lives in
    taskmaster.localserve, where the stdio server reads it too to say why it serves this session (local-server-012)."""
    return _ls().fallback_reason(root, env)


# -- discovery, status, skew, retire --------------------------------------------------------------------------------

class Endpoint:
    def __init__(self, record, token):
        self.record, self.token = record, token
        parts = urlsplit(record["url"])
        self.host, self.port = parts.hostname, parts.port or 80
        self.path = parts.path or "/mcp"
        self.build = record.get("build")
        if self.host not in LOOPBACK_HOSTS:                # the operator token goes only to this machine (bridge review M7)
            raise ValueError(f"the local server record names a non-loopback host: {self.host!r}")


def read_token(record) -> str:
    """The operator token named by the discovery record; must be an owner-only file we own."""
    from taskmaster.coordinator.ownership import verify_private
    path = Path(record["token_file"])
    verify_private(path)
    token = path.read_text(encoding="utf-8").strip()
    if not token:
        raise ServerUnavailable("operator token file is empty")
    return token


def _local_request(endpoint, verb, path, body=None, timeout=STATUS_TIMEOUT):
    conn = http.client.HTTPConnection(endpoint.host, endpoint.port, timeout=timeout)
    try:
        headers = {"Authorization": "Bearer " + endpoint.token, "Accept": "application/json"}
        data = None
        if body is not None:
            data = json.dumps(body).encode()
            headers["Content-Type"] = "application/json"
        conn.request(verb, path, data, headers)
        resp = conn.getresponse()
        raw = resp.read()
        try:
            parsed = json.loads(raw.decode("utf-8")) if raw.strip() else None
        except ValueError:
            parsed = None
        return resp.status, parsed
    finally:
        conn.close()


def probe_status(endpoint):
    """The server's /local/v1/status answer, or None when it does not answer (down, hung or not ours)."""
    ls = _ls()
    try:
        status, body = _local_request(endpoint, "GET", ls.PATH_STATUS)
    except (OSError, http.client.HTTPException):
        return None
    return body if status == 200 and isinstance(body, dict) else None


def skew_action(server_build, my_build) -> str:
    """The spec section 4 step 3 matrix: 'use', 'retire' (older server) or 'retire_if_alone' (same release, other code)."""
    ls = _ls()
    if not ls.valid_build(server_build):
        return "retire_if_alone"
    if ls.same_build(server_build, my_build):
        return "use"
    order = ls.compare_versions(my_build, server_build)
    if order == -1:
        return "use"                   # the server is newer: this is the old session; MCP is the contract
    if order == 1:
        return "retire"
    return "retire_if_alone"


def request_retire(endpoint, *, only_if_alone, requester):
    ls = _ls()
    try:
        status, body = _local_request(endpoint, "POST", ls.PATH_RETIRE,
                                      {"only_if_no_other_bridges": bool(only_if_alone), "requester": requester})
    except (OSError, http.client.HTTPException):
        return False, "retire request failed"
    reason = body.get("reason") if isinstance(body, dict) else None
    return status == 202, reason or f"HTTP {status}"


def wait_lease_free(root, seconds) -> bool:
    ls = _ls()
    deadline = time.monotonic() + seconds
    while ls.host_lease_held(root):
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
    return True


# -- launch ---------------------------------------------------------------------------------------------------------

def scrubbed_env(root, base=None):
    """The server's environment: no Claude Code session variables (they would pin the server to its first session), and
    the leftovers sweep told to keep it (other sessions use it)."""
    env = {k: v for k, v in (os.environ if base is None else base).items() if not k.upper().startswith("CLAUDE")}
    env["LEFTOVERS_KEEP"] = "1"
    env["LEFTOVERS_NAME"] = "tm-local:" + (Path(root).name or "root")
    return env


def no_feedback_startupinfo():
    """Windows: STARTF_FORCEOFFFEEDBACK (0x80), so starting a child does not show the app-starting cursor. The server launch
    chain (uv, launcher, server) is the only part of a session that outlasts the first second without a window."""
    if not WIN:
        return {}
    info = subprocess.STARTUPINFO()
    info.dwFlags |= 0x80
    return {"startupinfo": info}


def hidden_flags(breakaway=True):
    if not WIN:
        return {"start_new_session": True}
    flags = NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP
    return {"creationflags": flags | (0x01000000 if breakaway else 0), **no_feedback_startupinfo()}   # CREATE_BREAKAWAY_FROM_JOB


def spawn_detached(argv, *, env, cwd, stderr=None, wait_first=False):
    """Popen with no window and no stdin/stdout; tries to leave the parent's job object, else spawns without breakaway."""
    kw = dict(env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
              stderr=subprocess.DEVNULL if stderr is None else stderr)
    try:
        return subprocess.Popen(argv, **hidden_flags(True), **kw)
    except OSError:
        if not WIN:
            raise
        return subprocess.Popen(argv, **hidden_flags(False), **kw)


def launcher_main(argv) -> int:
    """The short-lived middle process of the double spawn: start the server, exit at once, so the server's parent is not
    the bridge (a parent-PID tree kill of the bridge does not reach it)."""
    log_file = None
    pid_file = None
    if argv[:1] == ["--pid-file"]:
        pid_file, argv = argv[1], argv[2:]
    try:
        root = argv[argv.index("--root") + 1]
        # Not serve.log: the server's own rotating handler owns that file, and a second writer duplicated its lines and
        # broke the rollover on Windows. This file holds only what the server prints before its logging is up.
        start_log = Path(root) / ".taskmaster" / "local" / "serve" / "serve-start.log"
        log_file = open(start_log, "wb")
    except (ValueError, IndexError, OSError):
        log_file = None
    try:
        child = spawn_detached(argv, env=dict(os.environ), cwd=str(PLUGIN_DIR), stderr=log_file)
    except OSError as exc:
        print(f"tm-bridge launcher: {exc}", file=sys.stderr)
        return 1
    if pid_file:                    # lets the bridge tell "still starting" from "died without a result" while it waits
        try:
            Path(pid_file).write_text(str(child.pid), encoding="ascii")
        except OSError:
            pass
    return 0


def _cache_file() -> Path:
    override = os.environ.get("TASKMASTER_BRIDGE_CACHE")
    if override:
        return Path(override)
    base = os.environ.get("LOCALAPPDATA") if WIN else os.environ.get("XDG_CACHE_HOME")
    return Path(base or Path.home() / ".cache") / "taskmaster" / "bridge-python.json"


def _dependency_hash() -> str:
    match = re.search(r"(?m)^# dependencies = .*$", Path(__file__).read_text(encoding="utf-8"))
    return hashlib.sha256((match.group(0) if match else "").encode()).hexdigest()[:16]


def server_python(plugin_dir=PLUGIN_DIR, *, refresh=False, log=None):
    """The interpreter of the server's uv environment, resolved once through uv and cached (keyed by plugin dir and the
    dependency line). A pruned uv env (the cached file is gone) re-resolves. None when uv cannot name one."""
    key = hashlib.sha256((str(Path(plugin_dir).resolve()) + "|" + _dependency_hash()).encode()).hexdigest()[:24]
    cache = _cache_file()
    try:
        cached = json.loads(cache.read_text(encoding="utf-8"))
        if not isinstance(cached, dict):
            cached = {}
    except (OSError, ValueError):
        cached = {}
    if not refresh and cached.get(key) and Path(cached[key]).is_file():
        return cached[key]
    uv = shutil.which("uv")
    if not uv:
        return None
    try:
        with _ENV_LOCK:             # a cold environment build: nothing else of ours starts another one meanwhile
            out = subprocess.run([uv, "run", "--quiet", "--script", str(Path(plugin_dir) / "tm_bridge.py"), "--print-python"],
                                 capture_output=True, text=True, timeout=LAUNCH_BOUND, creationflags=NO_WINDOW,
                                 stdin=subprocess.DEVNULL, **no_feedback_startupinfo())
    except (OSError, subprocess.SubprocessError) as exc:
        if log:
            log.note(f"uv could not name the server interpreter: {exc}")
        return None
    lines = [line.strip() for line in out.stdout.splitlines() if line.strip()]
    if out.returncode != 0 or not lines or not Path(lines[-1]).is_file():
        if log:
            log.note(f"uv could not name the server interpreter (exit {out.returncode}): {out.stderr.strip()[-300:]}")
        return None
    cached[key] = lines[-1]
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(cached), encoding="utf-8")
    except OSError:
        pass
    return lines[-1]


def _sweep_stale_launch_files(sdir, max_age=3600.0):
    """A result written after its bridge gave up is never read; remove launch files nobody can still be waiting for (bridge review M6)."""
    cutoff = time.time() - max_age
    for path in list(Path(sdir).glob("launch-*.json")) + list(Path(sdir).glob("launch-*.pid")):
        try:
            if path.stat().st_mtime < cutoff:
                path.unlink()
        except OSError:
            pass


def launch_server(root, plugin_dir=PLUGIN_DIR, *, log=None, idle_exit=None, server_argv=None, wait=None):
    """Double-spawn the local server and read its outcome from the result file (plan section 0). Returns the result dict;
    status 'ready' is the only success. server_argv overrides the command (tests); otherwise the cached uv-env python
    runs taskmaster_cli.py serve --local."""
    ls = _ls()
    from taskmaster.coordinator.ownership import private_directory
    sdir = private_directory(Path(root), ls.SERVE_DIR)
    _sweep_stale_launch_files(sdir)
    token = secrets.token_hex(6)
    result_file = sdir / f"launch-{token}.json"
    pid_file = sdir / f"launch-{token}.pid"
    wait = LAUNCH_BOUND if wait is None else wait
    idle = idle_exit if idle_exit is not None else os.environ.get("TASKMASTER_LOCAL_IDLE_EXIT", "600")
    if server_argv is None:
        server_argv = _test_argv("TASKMASTER_BRIDGE_TEST_SERVER_ARGV")
    if server_argv is None:
        python = server_python(plugin_dir, log=log)
        if python is None:
            return {"status": ls.STATUS_ERROR, "reason": "no interpreter for the server environment (is uv installed?)"}
        server_argv = [python, str(Path(plugin_dir) / "taskmaster_cli.py"), "serve", "--local", "--root", str(root),
                       "--result-file", str(result_file), "--idle-exit", str(idle), "--plugin-dir", str(plugin_dir)]
    else:
        server_argv = [part.replace("{result_file}", str(result_file)).replace("{root}", str(root)) for part in server_argv]
    launcher = [sys.executable, str(Path(__file__).resolve()), "--launcher", "--pid-file", str(pid_file), "--", *server_argv]
    try:
        child = spawn_detached(launcher, env=scrubbed_env(root), cwd=str(plugin_dir))
    except OSError as exc:
        return {"status": ls.STATUS_ERROR, "reason": f"could not start the launcher: {exc}"}
    result = _await_result(ls, result_file, pid_file, child, wait)
    for leftover in (result_file, pid_file):
        try:
            leftover.unlink()
        except OSError:
            pass
    return result


def _pid_alive(pid) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    if not WIN:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        return True
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)             # PROCESS_QUERY_LIMITED_INFORMATION
    if not handle:
        return ctypes.get_last_error() == 5                      # access denied: it exists
    try:
        code = wintypes.DWORD()
        return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259     # STILL_ACTIVE
    finally:
        kernel.CloseHandle(handle)


def _await_result(ls, result_file, pid_file, launcher, wait):
    """The server's result, waiting up to `wait` seconds while something that could still produce it is alive (the server
    process the launcher started, or the launcher itself before it has written the server's pid). Gives up early only when
    that process is gone without a result, so a slow environment build is waited out but a crashed server is not."""
    deadline = time.monotonic() + wait
    launcher_gone_at = None
    while True:
        result = ls.read_result(result_file, timeout=0.0)
        if result.get("status") in ls.RESULT_STATUSES and "no launch result" not in str(result.get("reason", "")):
            return result
        now = time.monotonic()
        if now >= deadline:
            return {"status": ls.STATUS_ERROR, "reason": f"no launch result within {wait:g}s", "pid": None}
        try:
            server_pid = int(pid_file.read_text(encoding="ascii").strip())
        except (OSError, ValueError):
            server_pid = None
        if server_pid is not None:
            if not _pid_alive(server_pid):
                late = ls.read_result(result_file, timeout=0.5)        # it may have written the result as it exited
                if late.get("status") in ls.RESULT_STATUSES and "no launch result" not in str(late.get("reason", "")):
                    return late
                return {"status": ls.STATUS_ERROR, "reason": "no launch result: the server process exited before reporting", "pid": server_pid}
        elif launcher.poll() is not None:
            launcher_gone_at = launcher_gone_at or now
            if now - launcher_gone_at > 2.0:                           # the launcher is gone and never named a server
                return {"status": ls.STATUS_ERROR, "reason": "no launch result: the launcher exited without starting the server", "pid": None}
        time.sleep(0.1)


def _await_successor(ls, root, seconds):
    """After a drain this bridge did not request: the discovery record a successor publishes, or None when none appears in time."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            record = ls.read_discovery(root)
        except PermissionError:
            return None
        if record is not None:
            return record
        time.sleep(0.1)
    return None


def ensure_server(root, my_build, requester, *, plugin_dir=PLUGIN_DIR, log=None, launch=None):
    """Find the project's local server, replacing or launching it as the skew matrix and the lease say; returns an Endpoint
    or raises ServerUnavailable(reason) (the caller falls back or keeps retrying)."""
    ls = _ls()
    launch = launch or launch_server
    lease_held_seen = 0
    retired_once = False
    retires = 0
    for _ in range(12):
        try:
            record = ls.read_discovery(root)
        except PermissionError as exc:
            raise ServerUnavailable(f"discovery file or token is not private to this user: {exc}") from exc
        if record is not None and record["state"] == ls.STATE_DRAINING:
            if not wait_lease_free(root, DRAIN_WAIT):
                raise ServerUnavailable("the local server is draining and did not exit in time")
            record = None
            if not retired_once:        # someone else retired it: their build launches the successor, not this (maybe older) one
                record = _await_successor(ls, root, SUCCESSOR_WAIT)
                if record is not None:
                    continue
        if record is not None:
            try:
                endpoint = Endpoint(record, read_token(record))
            except PermissionError as exc:
                raise ServerUnavailable(f"operator token is not private to this user: {exc}") from exc
            except (OSError, KeyError) as exc:
                raise ServerUnavailable(f"operator token unreadable: {exc}") from exc
            status = probe_status(endpoint)
            if status is None:
                time.sleep(0.3)         # the lease is held but nothing answers: starting, or hung; re-read a few times
                lease_held_seen += 1
                if lease_held_seen > LEASE_HELD_TRIES + 2:
                    raise ServerUnavailable("the local server holds its lease but does not answer")
                continue
            build = status.get("build") if isinstance(status.get("build"), dict) else record["build"]
            action = skew_action(build, my_build)
            if action == "use" or (retired_once and (action != "retire" or retires >= MAX_RETIRES)):
                if action == "retire" and log:
                    log.once("an older taskmaster build is serving this project after the upgrade; this session uses it as is")
                return endpoint
            retires += 1
            ok, reason = request_retire(endpoint, only_if_alone=action == "retire_if_alone", requester=requester)
            retired_once = True
            if not ok:
                if log:
                    log.once("another taskmaster build runs this project's server and could not be replaced now "
                             f"({reason}); this session uses it as is. Hooks and CLI of the other install are refused "
                             "until the server restarts.")
                return endpoint
            if not wait_lease_free(root, DRAIN_WAIT):
                if log:
                    log.once("the previous local server is still draining; this session keeps using it")
                return endpoint
            continue
        result = launch(root, plugin_dir, log=log)
        status = result.get("status")
        if log:
            log.note(f"launch result: {result}")
        if status == ls.STATUS_READY:
            continue                    # re-read discovery: it names the endpoint and the token
        if status == ls.STATUS_LEASE_HELD:
            lease_held_seen += 1
            if lease_held_seen >= LEASE_HELD_TRIES:
                raise ServerUnavailable("another server keeps winning the host lease but publishes nothing")
            time.sleep(0.2)
            continue
        raise ServerUnavailable(f"{status}: {result.get('reason') or 'no reason given'}")
    raise ServerUnavailable("could not settle on a local server")


# -- the relay ------------------------------------------------------------------------------------------------------

def may_have_committed_text(tool, sent_known=False, place="local"):
    # The first line matches the mods' "not connected" matcher (mods/taskmaster-mods/hooks/tm.ts NOT_CONNECTED), so a restart
    # blip shows as "connecting" there instead of "offline".
    head = f'no connected MCP tool "{tool}" on a server named "{SERVER_NAME}"'
    if sent_known:
        return head + f": the {place} taskmaster server could not be reached; this call was not sent"
    return head + (f": the connection to the {place} taskmaster server was lost while this call ran; the call may have committed, "
                   "check its effect before repeating it")


class Bridge:
    def __init__(self, root, my_build, discriminator, *, stdout=None, ensure=None, cwd=None, log=None, plugin_dir=PLUGIN_DIR,
                 budget=RECONNECT_BUDGET, fallback_command=None):
        self.root, self.build, self.discriminator = Path(root), my_build, discriminator
        self.nonce = secrets.token_hex(8)
        self.cwd = cwd if cwd is not None else os.getcwd()
        self.log = log or Log()
        self.plugin_dir = plugin_dir
        self.budget = budget
        self.out = stdout if stdout is not None else sys.stdout.buffer
        self.out_lock = threading.Lock()
        self._ensure = ensure or (lambda: ensure_server(self.root, self.build, self.discriminator, plugin_dir=self.plugin_dir, log=self.log))
        self.endpoint = None
        self.sid = None
        self.protocol = None
        self.epoch = 0
        self.init_msg = None
        self.inited_msg = None
        self.server_build = None
        self.replays = 0
        self.closed = threading.Event()
        self.stream_started = False
        self.stream_wake = threading.Event()
        self._rlock = threading.Lock()
        self._slock = threading.Lock()
        self.failures = []                 # times a (re)launch failed; 3 within FALLBACK_WINDOW switch to the stdio child
        self.fallback = None               # the stdio child once the local server is given up on
        self.fallback_command = fallback_command
        self._fb_lock = threading.Lock()
        self.place = "local"               # how errors name the server (a team clone says "team")
        self.lazy = False                  # start_lazy(): answer the handshake before the upstream session exists
        self.upstream_ready = threading.Event()
        self.boot_error = None
        self.served_init = None            # the initialize result the client was given before upstream was ready
        self.served_tools = None           # the tools list it was given from the cache, if any
        self.inited_upstream = False       # the boot already sent notifications/initialized upstream
        self._lazy_lock = threading.Lock()
        self.last_init_result = None
        self._held_calls = set()           # ids of tools/call requests held for the upstream session, not yet sent
        self._cancelled_held = set()       # held ids the client cancelled: dropped, never sent, never answered
        self.hold_bound = HOLD_BOUND
        self.list_hold = LIST_HOLD
        self.terminal = False              # set by a subclass when retrying a reconnect cannot help (a refused key)

    # -- output
    def emit(self, message):
        line = json.dumps(message, separators=(",", ":")) + "\n"
        with self.out_lock:
            self.out.write(line.encode("utf-8"))
            self.out.flush()

    def emit_error(self, mid, text, code=-32000):
        self.emit({"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": text}})

    # -- HTTP
    def headers(self, sid, accept, *, body=True):
        from taskmaster import localserve as ls
        h = {"Authorization": "Bearer " + self.endpoint.token, "Accept": accept,
             ls.HEADER_SESSION: self.discriminator, ls.HEADER_SESSION_NONCE: self.nonce, ls.HEADER_CWD: ls.encode_cwd(self.cwd)}
        if body:
            h["Content-Type"] = "application/json"
        if sid:
            h["Mcp-Session-Id"] = sid
        if self.protocol and sid:
            h["MCP-Protocol-Version"] = self.protocol
        return h

    def open(self, verb, body, read_timeout, *, sid, accept="application/json, text/event-stream"):
        endpoint = self.endpoint
        if endpoint is None:
            raise Unsent(OSError("no server endpoint yet"))
        conn = http.client.HTTPConnection(endpoint.host, endpoint.port, timeout=CONNECT_TIMEOUT)
        try:
            conn.connect()
        except OSError as exc:
            conn.close()
            raise Unsent(exc) from exc
        conn.sock.settimeout(read_timeout)
        try:
            conn.request(verb, endpoint.path, body, self.headers(sid, accept, body=body is not None))
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise Ambiguous(exc) from exc
        return conn, resp

    @staticmethod
    def events(resp):
        """Messages of a response body: one JSON document, or an SSE stream parsed as it arrives."""
        ctype = resp.getheader("Content-Type") or ""
        if "text/event-stream" in ctype:
            data = []
            while True:
                raw = resp.readline(MAX_LINE_BYTES + 1)
                if len(raw) > MAX_LINE_BYTES:
                    raise OSError("an event line from the server is too long")
                if not raw:
                    if data:
                        yield from Bridge._decode("\n".join(data))
                    return
                line = raw.decode("utf-8").rstrip("\r\n")
                if line == "":
                    if data:
                        yield from Bridge._decode("\n".join(data))
                        data = []
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip(" "))
        else:
            raw_body = resp.read(MAX_BODY_BYTES + 1)
            if len(raw_body) > MAX_BODY_BYTES:
                raise OSError("a response from the server is too large")
            body = raw_body.decode("utf-8")
            if body.strip():
                yield from Bridge._decode(body)

    @staticmethod
    def _decode(text):
        try:
            value = json.loads(text)
        except ValueError:
            return
        yield from (value if isinstance(value, list) else [value])

    # -- (re)connect
    def start(self):
        """Settle on a server at startup (raises ServerUnavailable: the caller falls back)."""
        self.endpoint = self._ensure()
        self.server_build = self.endpoint.build
        self.log.note(f"using {self.endpoint.record.get('url')} as session {self.discriminator} nonce {self.nonce}")

    # -- lazy start: the client's handshake does not wait for the server (a cold uv environment takes ~30 s)
    MIN_INIT = {"capabilities": {"tools": {"listChanged": True}}, "serverInfo": {"name": SERVER_NAME, "version": "0"}}

    def cache_path(self):
        from taskmaster import localserve as ls
        return self.root / ".taskmaster" / "local" / ls.SERVE_DIR / "bridge-init-cache.json"

    def _cache_key(self):
        b = self.build or {}
        return f"{b.get('version')}|{b.get('digest')}"

    def load_cache(self):
        """(initialize result, tools list) cached by the last successful connect of this plugin build, else (None, None)."""
        from taskmaster import localserve as ls
        try:
            data = ls.read_private_json(self.cache_path())
        except (PermissionError, OSError):
            return None, None
        if not data or data.get("key") != self._cache_key() or not isinstance(data.get("initialize"), dict):
            return self._shipped_handshake()
        tools = data.get("tools")
        return data["initialize"], tools if isinstance(tools, list) else None

    def _shipped_handshake(self):
        """First-ever run (no cache of a live connect): the handshake that ships with the plugin, generated from the server's own
        registration by tests/surface_inventory.py --write-handshake (a test keeps it equal to the live surface)."""
        try:
            data = json.loads((Path(self.plugin_dir) / "tm_bridge_handshake.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, None
        init, tools = (data.get("initialize") if isinstance(data, dict) else None), (data.get("tools") if isinstance(data, dict) else None)
        return (init, tools) if isinstance(init, dict) and isinstance(tools, list) else (None, None)

    def save_cache(self, init_result, tools):
        from taskmaster import localserve as ls
        try:
            from taskmaster.coordinator.ownership import private_directory
            private_directory(self.root, ls.SERVE_DIR)
            ls.write_private_json(self.cache_path(), {"key": self._cache_key(), "initialize": init_result, "tools": tools})
        except (OSError, PermissionError) as exc:
            self.log.note(f"could not write the handshake cache: {exc!r}")

    def start_lazy(self):
        """Settle on a server in the background; handle() answers initialize/tools/list from the cache meanwhile."""
        self.lazy = True
        threading.Thread(target=self._boot, daemon=True).start()

    def _boot(self):
        try:
            self.endpoint = self._ensure()
            self.server_build = self.endpoint.build
            self.log.note(f"using {self.endpoint.record.get('url')} as session {self.discriminator} nonce {self.nonce}")
            with self._lazy_lock:
                init_msg = self.init_msg
                if init_msg is None:                       # no client handshake yet: the normal path takes it from here
                    self.upstream_ready.set()
                    return
            try:
                result = self._replay()
            except (Unsent, Ambiguous, OSError, http.client.HTTPException) as exc:
                # draining / provably unsent / ambiguous: re-run discovery and reconnect like the eager bridge (successor wait, skew
                # matrix, failure counting); only a genuine inability to get a server within the bound falls back
                self.log.note(f"initialize replay failed, reconnecting: {exc!r}")
                if not self.reconnect(self.epoch):
                    raise
                if self.fallback is not None:
                    self.upstream_ready.set()
                    return
                result = self.last_init_result
                self.epoch -= 1                            # reconnect() already counted the new session; counted once below
            if not self.inited_upstream:
                self.inited_upstream = True
                self._post_notification(self.inited_msg or {"jsonrpc": "2.0", "method": "notifications/initialized"})
            self.epoch += 1
            self._start_stream()
            self.upstream_ready.set()
            self._reconcile(result)
        except Exception as exc:                           # includes ServerUnavailable: the waiting callers fall back
            self.log.note(f"background start failed: {exc!r}")
            self.boot_error = exc
            self.upstream_ready.set()

    def _post_notification(self, message):
        conn, resp = self.open("POST", json.dumps(message).encode(), SHORT_TIMEOUT, sid=self.sid)
        try:
            resp.read()
        finally:
            conn.close()

    def _upstream_tools(self):
        message = {"jsonrpc": "2.0", "id": "tm-bridge-tools", "method": "tools/list", "params": {}}
        conn, resp = self.open("POST", json.dumps(message).encode(), SHORT_TIMEOUT, sid=self.sid)
        try:
            for item in self.events(resp):
                if item.get("id") == "tm-bridge-tools" and isinstance(item.get("result"), dict):
                    tools = item["result"].get("tools")
                    return tools if isinstance(tools, list) else None
        finally:
            conn.close()
        return None

    def _reconcile(self, live_init):
        """Upstream is up: tell the client when what it was served (cache or static answer) differs from the live server, and
        refresh the cache."""
        for _ in range(50):                                # list_changed only makes sense once the client has said initialized
            if self.inited_msg is not None or self.closed.is_set():
                break
            time.sleep(0.1)
        try:
            tools = self._upstream_tools()
        except (OSError, Unsent, Ambiguous, http.client.HTTPException) as exc:
            self.log.note(f"could not list tools to reconcile the handshake: {exc!r}")
            tools = None
        served = self.served_init or {}
        live = live_init or {}
        differs = (served.get("instructions") != live.get("instructions")
                   or (self.served_tools is not None and tools is not None and self.served_tools != tools))
        if live_init is not None and tools is not None and self.server_build == self.build:
            self.save_cache(live_init, tools)       # only this build's own handshake: another build's would be served as ours (cold M1)
        if differs and self.served_init is not None:
            if served.get("instructions") != live.get("instructions"):
                self.log.note("the live instructions differ from the served ones; clients read them at initialize, so the next session gets them")
            self.emit({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})

    def _enter_fallback(self):
        if self.fallback is None:
            self._switch_to_fallback(str(self.boot_error))

    def _lazy_handle(self, message):
        """True when the message was dealt with here (answered from the cache, or held then handed to fallback)."""
        method, mid = message.get("method"), message.get("id")
        if method == "notifications/cancelled":
            rid = (message.get("params") or {}).get("requestId")
            with self._lazy_lock:
                if rid is not None and rid in self._held_calls:
                    self._cancelled_held.add(rid)          # still held: drop it; MCP says a cancelled request gets no answer
                    return True
            return False
        if self.upstream_ready.is_set() and self.boot_error is None:
            if method == "notifications/initialized" and self.inited_upstream:
                self.inited_msg = message
                return True
            return False
        if self.fallback is not None:
            return False
        if method == "initialize" and mid is not None:
            cached, tools = self.load_cache()
            result = dict(cached) if cached else dict(self.MIN_INIT)
            asked = (message.get("params") or {}).get("protocolVersion")
            # echo only a version the server family speaks; anything else is answered with ours and the client decides (cold M4)
            result["protocolVersion"] = asked if asked in KNOWN_PROTOCOLS else (result.get("protocolVersion") or "2025-06-18")
            with self._lazy_lock:
                if self.upstream_ready.is_set() and self.boot_error is None:
                    return False
                self.init_msg, self.served_init = message, result
                self.served_tools = tools
            self.emit({"jsonrpc": "2.0", "id": mid, "result": result})
            return True
        if method == "ping" and mid is not None:
            self.emit({"jsonrpc": "2.0", "id": mid, "result": {}})
            return True
        if method == "server/discover" and mid is not None:
            # Claude Code probes the modern protocol first; the server answers method-not-found and the client uses initialize.
            # Holding it made the client give up and drop the connection when the late answer came (live cold-start run).
            self.emit({"jsonrpc": "2.0", "id": mid, "error": {"code": -32601, "message": "Method not found"}})
            return True
        empty = {"prompts/list": {"prompts": []}, "resources/list": {"resources": []},
                 "resources/templates/list": {"resourceTemplates": []}}
        if method in empty and mid is not None and self.boot_error is None:
            self.emit({"jsonrpc": "2.0", "id": mid, "result": empty[method]})      # the server has none; no reason to hold the client
            return True
        if method == "notifications/initialized":
            self.inited_msg = message
            if self.boot_error is not None:
                self._enter_fallback()
            return True
        if method == "tools/list" and mid is not None and self.served_tools is not None and self.boot_error is None:
            self.emit({"jsonrpc": "2.0", "id": mid, "result": {"tools": self.served_tools}})
            return True
        if method == "tools/list" and mid is not None and self.served_tools is None and self.boot_error is None:
            if not self.upstream_ready.wait(self.list_hold):
                # Claude Code gives up on a request after ~15-20 s and then drops the connection when the late answer arrives
                # (live cold-start run), so answer now and let list_changed bring the real list.
                self.served_tools = []
                self.emit({"jsonrpc": "2.0", "id": mid, "result": {"tools": []}})
                return True
        # everything else waits for the upstream session
        self.log.note(f"holding {method} (id {mid}) until the upstream session is ready")
        held = method == "tools/call" and mid is not None
        if held:
            with self._lazy_lock:
                self._held_calls.add(mid)
        try:
            return self._hold(message, method, mid)
        finally:
            if held:
                with self._lazy_lock:
                    self._held_calls.discard(mid)
                    self._cancelled_held.discard(mid)

    def _was_cancelled(self, mid):
        with self._lazy_lock:                              # a cancelled held call gets no answer on any branch (MCP; and a late
            return mid in self._cancelled_held             # answer to an abandoned id makes Claude Code drop the connection)

    def _hold(self, message, method, mid):
        # a tool call may wait long; any other request must be answered before the client gives up on it (a late answer to a
        # request the client abandoned makes it drop the connection)
        if not self.upstream_ready.wait(self.hold_bound if method == "tools/call" else self.list_hold):
            if mid is not None and method is not None and not self._was_cancelled(mid):
                self.emit_error(mid, may_have_committed_text((message.get("params") or {}).get("name") or method,
                                                             sent_known=True, place=self.place))
            return True
        if self.boot_error is not None:
            self._enter_fallback()
            if self.fallback is None:
                if mid is not None and method is not None and not self._was_cancelled(mid):
                    self.emit_error(mid, may_have_committed_text((message.get("params") or {}).get("name") or method,
                                                                 sent_known=True, place=self.place))
                return True
        with self._lazy_lock:                              # last look: a cancel that landed while held wins; after this it is forwarded
            if mid in self._cancelled_held:
                return True
            self._held_calls.discard(mid)
        return False

    def reconnect(self, seen_epoch, budget=None):
        """Re-establish the upstream session (eagerly, with backoff); True when a session newer than seen_epoch exists."""
        with self._rlock:
            if self.epoch != seen_epoch or self.fallback is not None:
                return True
            deadline = time.monotonic() + (self.budget if budget is None else budget)
            delay = RECONNECT_BACKOFF_START
            while not self.closed.is_set():
                try:
                    self.endpoint = self._ensure()
                    self._replay()
                    self.epoch += 1
                    self.stream_wake.set()
                    return True
                except (ServerUnavailable, Unsent, Ambiguous, OSError, http.client.HTTPException) as exc:
                    self.log.note(f"reconnect failed: {exc!r}")
                    if self.terminal:
                        return False
                    if isinstance(exc, ServerUnavailable) and self._count_failure():
                        self._switch_to_fallback(str(exc))
                        return True
                    if time.monotonic() >= deadline:
                        return False
                    time.sleep(delay)
                    delay = min(delay * 2, RECONNECT_BACKOFF_CAP)
            return False

    def _count_failure(self) -> bool:
        now = time.monotonic()
        self.failures = [t for t in self.failures if now - t < FALLBACK_WINDOW] + [now]
        return len(self.failures) >= FALLBACK_FAILURES

    def _switch_to_fallback(self, reason):
        """The local server is lost and relaunching keeps failing: serve the rest of this session from today's stdio server.
        Spawns it, replays the cached initialize + initialized into it, tells the client the tool list may have changed."""
        with self._fb_lock:
            if self.fallback is not None:
                return
            command = (self.fallback_command or _test_argv("TASKMASTER_BRIDGE_TEST_FALLBACK_ARGV")
                       or [shutil.which("uv") or "uv", "run", str(Path(self.plugin_dir) / "backlog_server.py")])
            kw = {"creationflags": NO_WINDOW} if WIN else {}
            with _ENV_LOCK:         # waits for a launch's environment build to finish instead of starting a second one
                child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None, **kw)
            self.log.once(f"the local server is lost and could not be relaunched ({reason}); switching to the per-session stdio server")
            if self.init_msg is not None:
                self._fb_write(child, dict(self.init_msg, id="tm-bridge-fb-init"))
                while True:
                    line = child.stdout.readline()
                    if not line:
                        raise ServerUnavailable("the stdio server exited during startup")
                    try:
                        if json.loads(line).get("id") == "tm-bridge-fb-init":
                            break
                    except ValueError:
                        continue
                if self.inited_msg is not None:
                    self._fb_write(child, self.inited_msg)
                    self.emit({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
            self.fallback = child
            threading.Thread(target=self._fb_pump, args=(child,), daemon=True).start()

    @staticmethod
    def _fb_write(child, message):
        child.stdin.write((json.dumps(message, separators=(",", ":")) + "\n").encode("utf-8"))
        child.stdin.flush()

    def _fb_pump(self, child):
        try:
            for line in child.stdout:
                with self.out_lock:
                    self.out.write(line if line.endswith(b"\n") else line + b"\n")
                    self.out.flush()
        except (OSError, ValueError):
            pass

    def _replay(self):
        """Replay the cached initialize + initialized on a fresh upstream session; tools/list_changed when the build changed.
        Returns the upstream's initialize result (None when there was nothing to replay)."""
        previous, old_sid = self.server_build, self.sid
        if self.init_msg is None:
            self.sid = None
            self.server_build = self.endpoint.build
            return None
        self.replays += 1
        message = dict(self.init_msg, id=f"tm-bridge-reinit-{self.replays}")
        conn, resp = self.open("POST", json.dumps(message).encode(), SHORT_TIMEOUT, sid=None)
        init_result = None
        try:
            sid = resp.getheader("Mcp-Session-Id")
            for item in self.events(resp):
                if item.get("id") == message["id"] and isinstance(item.get("result"), dict):
                    init_result = item["result"]
            if resp.status != 200 or not sid:
                raise Unsent(OSError(f"initialize replay answered HTTP {resp.status}"))
        except (OSError, http.client.HTTPException) as exc:
            raise Ambiguous(exc) from exc
        finally:
            conn.close()
        self.last_init_result = init_result
        if init_result is not None:
            self.protocol = init_result.get("protocolVersion") or self.protocol
        if self.inited_msg is not None:
            conn, resp = self.open("POST", json.dumps(self.inited_msg).encode(), SHORT_TIMEOUT, sid=sid)
            try:
                resp.read()
            finally:
                conn.close()
            self.inited_upstream = True
        with self._slock:
            self.sid = sid
        if old_sid and old_sid != sid:
            self._drop_session(old_sid)
        self.server_build = self.endpoint.build
        if previous != self.server_build and self.inited_msg is not None:
            self.emit({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})
        return init_result

    def _drop_session(self, sid):
        """End a replaced upstream session (best effort): the local profile keeps idle sessions, so one per reconnect would pile up."""
        try:
            conn, resp = self.open("DELETE", None, 2.0, sid=sid)
            try:
                resp.read()
            finally:
                conn.close()
        except (Unsent, Ambiguous, OSError, http.client.HTTPException):
            pass

    # -- requests
    def handle(self, message):
        if self.lazy and self._lazy_handle(message):
            return
        method, mid = message.get("method"), message.get("id")
        is_request = mid is not None and method is not None
        tool = (message.get("params") or {}).get("name") if method == "tools/call" else None
        timeout = None if method == "tools/call" else SHORT_TIMEOUT
        body = json.dumps(message).encode()
        ambiguous = False
        for _ in range(RETRY_ATTEMPTS):
            if self.fallback is not None:
                with self._fb_lock:
                    self._fb_write(self.fallback, message)
                return
            epoch = self.epoch
            try:
                conn, resp = self.open("POST", body, timeout, sid=None if method == "initialize" else self.sid)
            except Unsent:
                if self.reconnect(epoch):
                    continue
                break
            except Ambiguous as exc:
                self.log.note(f"{method}: connection lost after send: {exc!r}")
                if method == "tools/call":
                    ambiguous = True
                    break
                if self.reconnect(epoch):
                    continue
                break
            try:
                if resp.status == 404 and method != "initialize":      # "Session not found": answered before dispatch
                    resp.read()
                    conn.close()
                    if self.reconnect(epoch):
                        continue
                    break
                if resp.status == 202:
                    resp.read()
                    self._after_forward(message)
                    return
                if resp.status >= 400:
                    text = resp.read(2048).decode("utf-8", "replace")[:300]
                    if resp.status == 503 and DRAINING_TEXT in text:
                        # A draining server refuses new traffic before dispatch: the request never ran, so move on to
                        # its successor instead of handing Claude Code the 503 (local-server-002 M4).
                        conn.close()
                        if self.reconnect(epoch):
                            continue
                        break
                    if is_request:
                        if resp.status in (502, 504) and method == "tools/call":      # a proxy may have passed the call on first
                            self.emit_error(mid, may_have_committed_text(tool or method, place=self.place))
                        else:
                            self.emit_error(mid, self.status_text(resp.status, text))
                    return
                self._after_response(message, resp)
                self._after_forward(message)
                return
            except (OSError, http.client.HTTPException, ValueError) as exc:
                self.log.note(f"{method}: response lost: {exc!r}")
                if method == "tools/call":
                    ambiguous = True
                    break
                if self.reconnect(epoch):
                    continue
                break
            finally:
                conn.close()
        if ambiguous:
            self._background_reconnect()
        if is_request:
            self.emit_error(mid, may_have_committed_text(tool or method, sent_known=not ambiguous, place=self.place))

    def status_text(self, status, text):
        return f"taskmaster server answered HTTP {status}: {text}"

    def _background_reconnect(self):
        epoch = self.epoch
        threading.Thread(target=self.reconnect, args=(epoch,), kwargs={"budget": self.budget}, daemon=True).start()

    def _after_response(self, message, resp):
        method, mid = message.get("method"), message.get("id")
        sid = resp.getheader("Mcp-Session-Id")
        final = False
        try:
            for item in self.events(resp):
                if method == "initialize" and item.get("id") == mid and isinstance(item.get("result"), dict):
                    self.protocol = item["result"].get("protocolVersion")
                    with self._slock:       # recorded before the result reaches the client: its next messages need the session id
                        if sid:
                            self.sid = sid
                        self.init_msg = message
                    self.epoch += 1
                if item.get("id") == mid and ("result" in item or "error" in item):
                    final = True
                self.emit(item)
        except (OSError, http.client.HTTPException, ValueError):
            if not final:                   # a stream that fails after the answer went out must not answer or retry it again
                raise
        if mid is not None and method is not None and not final:
            raise OSError("response ended without an answer")

    def _after_forward(self, message):
        if message.get("method") == "notifications/initialized":
            self.inited_msg = message
            self._start_stream()

    # -- the GET stream (the bridge's liveness; also carries server notifications)
    def _start_stream(self):
        with self._slock:
            if self.stream_started:
                return
            self.stream_started = True
        threading.Thread(target=self._stream_main, daemon=True).start()

    def _stream_main(self):
        try:
            self._stream_loop()
        except Exception as exc:                           # a failed switch to the fallback must not die silently
            self.log.note(f"the upstream stream stopped: {exc!r}")

    def _stream_loop(self):
        failures = 0
        while not self.closed.is_set() and self.fallback is None:
            epoch, sid = self.epoch, self.sid
            if not sid:
                self.stream_wake.wait(1.0)
                self.stream_wake.clear()
                continue
            try:
                conn, resp = self.open("GET", None, None, sid=sid, accept="text/event-stream")
            except (Unsent, Ambiguous):
                failures += 1
                self.reconnect(epoch)
                time.sleep(GET_BACKOFF[min(failures, len(GET_BACKOFF)) - 1])
                continue
            try:
                if resp.status == 404:
                    resp.read()
                    self.reconnect(epoch)
                    continue
                if resp.status != 200:
                    resp.read()
                    failures += 1
                    self.closed.wait(min(GET_NON200_CAP, GET_NON200_STEP * failures))     # e.g. 405: this server offers no stream
                    continue
                failures = 0
                for item in self.events(resp):
                    if isinstance(item.get("id"), str) and item["id"].startswith("tm-bridge-"):
                        continue
                    self.emit(item)
                if not self.closed.is_set():
                    self.log.note("upstream stream ended; reconnecting")
                    self.reconnect(epoch)
            except (OSError, http.client.HTTPException, ValueError):
                if not self.closed.is_set():
                    failures += 1
                    self.reconnect(epoch)
                    time.sleep(GET_BACKOFF[min(failures, len(GET_BACKOFF)) - 1])
            finally:
                conn.close()

    # -- main loop
    def run(self, stdin=None):
        stdin = stdin if stdin is not None else sys.stdin.buffer
        threads = []
        for raw in stdin:
            raw = raw.strip()
            if not raw:
                continue
            try:
                parsed = json.loads(raw.decode("utf-8"))
            except ValueError:
                self.emit({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})
                continue
            for message in (parsed if isinstance(parsed, list) else [parsed]):      # a batch is handled message by message
                if not isinstance(message, dict):
                    continue
                thread = threading.Thread(target=self._safe_handle, args=(message,), daemon=True)
                thread.start()
                threads.append(thread)
            threads = [t for t in threads if t.is_alive()]
        self.close()

    def _safe_handle(self, message):
        try:
            self.handle(message)
        except Exception as exc:                          # one bad message must not end the session
            self.log.note(f"unhandled error: {exc!r}")
            if message.get("id") is not None and message.get("method"):
                self.emit_error(message["id"], f"taskmaster bridge error: {exc}")

    def close(self):
        """stdin closed: end our upstream session (best effort) and stop; the server is never killed."""
        self.closed.set()
        self.stream_wake.set()
        if self.fallback is not None:
            try:
                self.fallback.stdin.close()
                self.fallback.wait(timeout=5)
            except (OSError, subprocess.TimeoutExpired):
                self.fallback.terminate()
            return
        if self.sid and self.endpoint is not None:
            try:
                conn, resp = self.open("DELETE", None, 2.0, sid=self.sid)
                resp.read()
                conn.close()
            except (Unsent, Ambiguous, OSError, http.client.HTTPException):
                pass


# -- team clones: the same relay to the team server ---------------------------------------------------------------------

class TeamEndpoint:
    def __init__(self, ref, token):
        parts = urlsplit(ref.origin)
        self.scheme, self.host, self.port = parts.scheme, parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
        self.path = urlsplit(ref.url).path or "/mcp"
        self.token, self.build = token, None
        self.record = {"url": ref.origin + self.path}      # never the key


_SSL_LOCK = threading.Lock()


def _tls_connection(host, port, timeout):
    """HTTP over TLS with the default context (certificate chain and hostname verified, no override). The module-level ssl
    block above exists for the loopback path; a team clone lifts it here, lazily, and only then."""
    with _SSL_LOCK:
        if sys.modules.get("ssl", 0) is None:
            sys.modules.pop("ssl", None)
        import ssl
    context = ssl.create_default_context()

    class _Tls(http.client.HTTPConnection):
        def connect(self):
            super().connect()
            self.sock = context.wrap_socket(self.sock, server_hostname=self.host)

    return _Tls(host, port, timeout=timeout)


# Tool names the mods call, listed in no-key mode so a client can reach the set-key answer (no network, no heavy imports).
NO_KEY_TOOLS = ("backlog_server_setup", "backlog_get_task", "backlog_list_tasks", "backlog_query", "backlog_update_task",
                "backlog_complete_task", "backlog_pick_task", "backlog_claim", "backlog_archive_task", "backlog_record_gate",
                "backlog_skip_gate", "backlog_task_pipeline", "backlog_continuity_items", "backlog_issue_list", "backlog_open_viewer",
                "backlog_handover_create", "backlog_handover_get", "backlog_handover_list", "backlog_handover_update_status")


class TeamBridge(Bridge):
    """Relay to the team server a clone's marker names, with this member's key (taskmaster.remote locate and keys). Never
    launches, retires or falls back to a local store (a team clone has none: split brain), and sends no session headers
    (the team server keys sessions by member key and connection). Without a key it answers every tool call with the fix."""

    def __init__(self, root, ref, load_key, **kw):
        super().__init__(root, None, "team", ensure=self._team_endpoint, **kw)
        self.place = "team"
        self.ref, self.load_key, self.refusal = ref, load_key, None
        self._key_stale = False
        if ref is None:
            self.refusal = "this clone's server marker cannot be used"

    def _team_endpoint(self):
        try:
            key = self.load_key(self.ref)
        except Exception as exc:                  # NoKey and friends: their text names the fix, never the key
            raise ServerUnavailable(str(exc)) from exc
        return TeamEndpoint(self.ref, key.value)

    def start(self):
        if self.ref is not None:
            try:
                self.endpoint = self._team_endpoint()
            except ServerUnavailable as exc:
                self.refusal = str(exc)
        self.log.note(f"team server {self.ref.origin if self.ref is not None else '(unusable marker)'}" + (f"; refusing calls: {self.refusal}" if self.refusal else ""))
        if self.refusal:
            self.log.once(f"team clone, calls refused until fixed: {self.refusal}")

    def headers(self, sid, accept, *, body=True):
        h = {"Authorization": "Bearer " + self.endpoint.token, "Accept": accept}
        if body:
            h["Content-Type"] = "application/json"
        if sid:
            h["Mcp-Session-Id"] = sid
        if self.protocol and sid:
            h["MCP-Protocol-Version"] = self.protocol
        return h

    def open(self, verb, body, read_timeout, *, sid, accept="application/json, text/event-stream"):
        endpoint = self.endpoint
        if endpoint is None:
            raise Unsent(OSError("no team server endpoint"))
        try:
            conn = (_tls_connection(endpoint.host, endpoint.port, CONNECT_TIMEOUT) if endpoint.scheme == "https"
                    else http.client.HTTPConnection(endpoint.host, endpoint.port, timeout=CONNECT_TIMEOUT))
            conn.connect()
        except OSError as exc:                    # includes certificate and hostname failures: nothing was sent
            conn.close()
            raise Unsent(exc) from exc
        conn.sock.settimeout(read_timeout)
        try:
            conn.request(verb, endpoint.path, body, self.headers(sid, accept, body=body is not None))
            resp = conn.getresponse()
        except (OSError, http.client.HTTPException) as exc:
            conn.close()
            raise Ambiguous(exc) from exc
        return conn, resp

    def _replay(self):
        try:
            return super()._replay()
        except Unsent as exc:
            if re.search(r"HTTP (401|403)\b", str(exc)):      # a refused key will not be accepted by asking again within this call
                self.terminal = True
                self._key_stale = True
            raise

    def status_text(self, status, text):
        if status == 401:
            self._key_stale = True                 # the next call re-reads the key file, so a fixed key needs no restart
            return (f"the team server refused your key (expired, revoked or wrong); run: taskmaster remote set-key --url {self.ref.url} "
                    "and then repeat the call")
        if status == 403:
            return f"the team server denied this call for your role or scopes: {text}"
        return super().status_text(status, text)

    def _count_failure(self):
        return False                               # never the local stdio store

    def _refresh_key(self):
        """After a 401: pick up a key fixed by `taskmaster remote set-key` without restarting the session."""
        self._key_stale, self.terminal = False, False
        try:
            self.endpoint = self._team_endpoint()
        except ServerUnavailable:
            pass

    def _switch_to_fallback(self, reason):
        raise ServerUnavailable("a team clone has no local store to fall back to")

    def handle(self, message):
        if self.refusal is None:
            if self._key_stale:
                self._refresh_key()
            return super().handle(message)
        method, mid = message.get("method"), message.get("id")
        if mid is None or method is None:
            return
        if method == "initialize":
            version = (message.get("params") or {}).get("protocolVersion") or "2025-06-18"
            self.emit({"jsonrpc": "2.0", "id": mid, "result": {"protocolVersion": version, "capabilities": {"tools": {}},
                                                               "serverInfo": {"name": "taskmaster-team-unavailable", "version": "0"}}})
        elif method == "tools/list":
            self.emit({"jsonrpc": "2.0", "id": mid, "result": {"tools": [
                {"name": n, "description": f"Unavailable in this team clone until a key is set: {self.refusal}",
                 "inputSchema": {"type": "object", "additionalProperties": True}} for n in NO_KEY_TOOLS]}})
        elif method == "ping":
            self.emit({"jsonrpc": "2.0", "id": mid, "result": {}})
        elif method == "tools/call":
            self.emit({"jsonrpc": "2.0", "id": mid, "result": {"isError": True, "content": [{"type": "text", "text": (
                f"Error: this clone belongs to a team server and no usable key is set: {self.refusal}")}]}})
        else:
            self.emit_error(mid, self.refusal)


def _team_clone_root():
    """The project root when it is a team-server clone (a marker or latch), else None; starts nothing (the relay helper reuses it)."""
    if str(PLUGIN_DIR) not in sys.path:
        sys.path.insert(0, str(PLUGIN_DIR))
    from taskmaster.root import resolve_root
    root = resolve_root(Path.cwd()).root
    try:
        in_server_mode = _ls().server_mode_reason(root) is not None     # the same marker/latch rule the local bridge and the fence use
    except Exception:                                    # an import failure is for the local path to report (the env opt-out outranks it)
        in_server_mode = False
    return root if in_server_mode else None


def _start_team_bridge(log, root=None):
    """A TeamBridge when this project is a team-server clone (a marker or latch), else None. Reads two paths first, so a
    solo project never imports the remote package."""
    root = root if root is not None else _team_clone_root()
    if root is None:
        return None
    from taskmaster import server_marker
    from taskmaster.remote import keys, locate
    try:
        ref = locate.locate(root)
    except server_marker.ServerConfigError as exc:
        log.once(f"team clone with an unusable marker: {exc}")
        ref = None
    bridge = TeamBridge(root, ref, keys.load_key, log=log)
    bridge.start()
    return bridge


# -- fallback: today's stdio server as a child ----------------------------------------------------------------------

def run_fallback(reason, plugin_dir=PLUGIN_DIR, *, stdin=None, stdout=None, log=None, command=None) -> int:
    log = log or Log()
    log.once(f"using the per-session stdio server ({reason})")
    if command is None:
        command = _test_argv("TASKMASTER_BRIDGE_TEST_FALLBACK_ARGV")
    if command is None:
        command = [shutil.which("uv") or "uv", "run", str(Path(plugin_dir) / "backlog_server.py")]
        if not WIN and stdin is None and stdout is None:
            sys.stderr.flush()
            os.execvp(command[0], command)          # POSIX: become today's server, no relay process at all
    kw = {"creationflags": NO_WINDOW} if WIN else {}
    try:
        child = subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=None, **kw)
    except OSError as exc:
        print(f"tm-bridge: cannot start the stdio server: {exc}", file=sys.stderr)
        return 1
    source = stdin if stdin is not None else sys.stdin.buffer
    sink = stdout if stdout is not None else sys.stdout.buffer

    def pump_in():
        try:
            for line in source:
                child.stdin.write(line)
                child.stdin.flush()
        except (OSError, ValueError):
            pass
        finally:
            try:
                child.stdin.close()
            except OSError:
                pass

    threading.Thread(target=pump_in, daemon=True).start()
    try:
        while True:
            chunk = child.stdout.read1(65536)
            if not chunk:
                break
            sink.write(chunk)
            sink.flush()
    except (OSError, ValueError):
        pass
    try:
        return child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        child.terminate()
        return child.wait()


# -- test mode (only with TASKMASTER_BRIDGE_TESTING=1; otherwise an environment variable must not redirect what the bridge spawns)

TEST_BOUNDS = {"SHORT_TIMEOUT", "LIST_HOLD", "HOLD_BOUND", "RECONNECT_BUDGET", "FALLBACK_WINDOW", "GET_BACKOFF", "LAUNCH_BOUND",
               "DRAIN_WAIT", "SUCCESSOR_WAIT", "INIT_WAIT"}


def _testing() -> bool:
    return os.environ.get("TASKMASTER_BRIDGE_TESTING") == "1"


def _test_argv(name):
    """A JSON argv from a test-mode variable, or None (always None outside test mode)."""
    raw = os.environ.get(name) if _testing() else None
    if not raw:
        return None
    value = json.loads(raw)
    return [str(part) for part in value] if isinstance(value, list) and value else None


def _apply_test_bounds():
    """Shorten module bounds from TASKMASTER_BRIDGE_TEST_BOUNDS (name -> seconds, a list for GET_BACKOFF). Returns an error text for an
    unknown name (the caller exits), else None. Names this bridge does not have (INIT_WAIT) are accepted and ignored."""
    raw = os.environ.get("TASKMASTER_BRIDGE_TEST_BOUNDS") if _testing() else None
    if not raw:
        return None
    bounds = json.loads(raw)
    for name in bounds:
        if name not in TEST_BOUNDS:
            print(f"tm-bridge: unknown test bound {name}", file=sys.stderr, flush=True)
            return name
    for name, value in bounds.items():
        if name in globals():
            globals()[name] = tuple(value) if isinstance(value, list) else value
    return None


# -- the relay helper: the decisions of the bridge, answered as JSON lines for the compiled relay (spec section 3) ----------

def _helper_line(out, **fields):
    out.write((json.dumps({"v": 1, **fields}, separators=(",", ":")) + "\n").encode("utf-8"))
    out.flush()


def _helper_endpoint_line(out, root, build, discriminator, log):
    try:
        endpoint = ensure_server(root, build, discriminator, log=log)
    except ServerUnavailable as exc:
        return _helper_line(out, kind="unavailable", reason=str(exc))
    except Exception as exc:
        return _helper_line(out, kind="unavailable", reason=f"local server unavailable: {exc!r}")
    _helper_line(out, kind="endpoint", url=endpoint.record.get("url"), token=endpoint.token, build=endpoint.build)


def helper_main(ensure_only=False, stdin=None, stdout=None) -> int:
    """--relay-helper: line 1 session (mode local/team/fallback), for local line 2 endpoint or unavailable, exit 0.
    --relay-helper --ensure-only: no line 1; one endpoint/unavailable line per {"op":"ensure"} line on stdin, exit at EOF."""
    out = stdout if stdout is not None else sys.stdout.buffer
    source = stdin if stdin is not None else sys.stdin.buffer
    log = Log(os.environ.get("TASKMASTER_BRIDGE_LOG") or None)
    mode, reason, root, build = "local", None, None, None
    try:
        mode, reason, root, build = _local_decision()
    except ServerUnavailable as exc:
        mode, reason = "fallback", str(exc)
    except Exception as exc:        # a broken local server must never leave a session without tm (spec section 1)
        mode, reason = "fallback", f"local server unavailable: {exc!r}"
    discriminator = os.environ.get("TASKMASTER_BRIDGE_DISCRIMINATOR") or "bridge-" + secrets.token_hex(8)
    if ensure_only:
        for line in source:
            try:
                op = json.loads(line).get("op")
            except (ValueError, AttributeError):
                op = None
            if op != "ensure":
                _helper_line(out, kind="unavailable", reason=f"unknown op: {op!r}")
            elif mode == "local":
                _helper_endpoint_line(out, root, build, discriminator, log)
            else:
                _helper_line(out, kind="unavailable", reason=reason or f"mode {mode}")
        return 0
    _helper_line(out, kind="session", root=str(root) if root is not None else None, mode=mode, reason=reason, build=build,
                 plugin_dir=str(PLUGIN_DIR))
    if mode == "local":
        _helper_endpoint_line(out, root, build, discriminator, log)
    return 0


# -- entry ----------------------------------------------------------------------------------------------------------

def _local_decision():
    """The one decision sequence both the full bridge and the relay helper follow: (mode, reason, root, build) with mode
    team / fallback / local. Any exception sends the caller to the stdio fallback."""
    team_root = _team_clone_root()
    if team_root is not None:
        return "team", None, team_root, None
    if str(os.environ.get("TASKMASTER_LOCAL_SERVER", "")).strip().lower() in ("0", "off", "false", "no"):
        return "fallback", "TASKMASTER_LOCAL_SERVER=0", None, None      # read before localserve is imported so an import failure cannot override it
    ls = _ls()
    from taskmaster.root import resolve_root
    root = resolve_root(Path.cwd()).root
    reason = fallback_reason(root)
    if reason:
        return "fallback", reason, root, None
    build = {"version": ls.declared_version(PLUGIN_DIR), "digest": ls.package_digest(PLUGIN_DIR / "taskmaster")}
    from taskmaster.coordinator.ownership import private_directory
    private_directory(Path(root), ls.SERVE_DIR)       # a junction or foreign directory here fails now, not in the background
    return "local", None, root, build


def _start_local_bridge(log):
    """Everything on the way to a started local-server bridge; any exception here sends the caller to the stdio fallback."""
    mode, reason, root, my_build = _local_decision()
    if mode == "team":
        return None, _start_team_bridge(log, root)
    if mode == "fallback":
        return reason, None
    session = os.environ.get("CLAUDE_CODE_SESSION_ID")
    discriminator = session or "bridge-" + secrets.token_hex(8)
    log.note(f"discriminator {discriminator} from {'CLAUDE_CODE_SESSION_ID' if session else 'a random id (no CLAUDE_CODE_SESSION_ID)'}")
    bridge = Bridge(root, my_build, discriminator, log=log, budget=RECONNECT_BUDGET)
    bridge.start_lazy()
    return None, bridge


def bridge_main() -> int:
    log = Log(os.environ.get("TASKMASTER_BRIDGE_LOG") or None)
    try:
        reason, bridge = _start_local_bridge(log)
    except ServerUnavailable as exc:
        return run_fallback(str(exc), log=log)
    except Exception as exc:        # a broken local server must never leave a session without tm (spec section 1)
        return run_fallback(f"local server unavailable: {exc!r}", log=log)
    if bridge is None:
        return run_fallback(reason, log=log)
    bridge.run()
    return 0


def main(argv=None) -> int:
    argv = sys.argv[1:] if argv is None else list(argv)
    if _apply_test_bounds() is not None:
        return 2
    if argv[:1] == ["--relay-helper"]:
        return helper_main(argv[1:] == ["--ensure-only"])
    if argv[:1] == ["--print-python"]:
        print(sys.executable)
        return 0
    if argv[:1] == ["--launcher"]:
        rest = argv[1:]
        sep = rest.index("--")
        return launcher_main(rest[:sep] + rest[sep + 1:])
    return bridge_main()


if __name__ == "__main__":
    sys.exit(main())
