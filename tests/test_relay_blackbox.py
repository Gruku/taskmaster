# User intent: the one black-box suite both tm relays (the Python bridge and the compiled Rust relay) must pass, so a behaviour the Python
# bridge has today cannot silently differ in the binary: every test runs the relay as a real process against a fake local server.
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from fake_local_server import LIVE_INIT, LIVE_TOOLS, TOKEN, FakeServer  # noqa: F401
from taskmaster import localserve as ls
from taskmaster import server_fence
from test_tm_bridge import FAKE_STDIO, TRANSIENT, Host, native_root  # noqa: F401  (the fixture)

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
BRIDGE = PLUGIN_ROOT / "tm_bridge.py"
FAKE_LAUNCHER = PLUGIN_ROOT / "tests" / "fake_local_server.py"
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
RELAYS = ["python", "rust"]
TOOL = "\x24{tool}"


def _bridge_text():
    source = (PLUGIN_ROOT / "mods" / "taskmaster-mods" / "tests" / "fixtures" / "bridge-text.ts").read_text(encoding="utf-8")
    return dict(re.findall(r"export const (\w+) = \(tool: string\): string => " + chr(96) + r"(.+)" + chr(96), source))


BRIDGE_TEXT = _bridge_text()


def not_sent(tool):
    return BRIDGE_TEXT["bridgeNotSent"].replace(TOOL, tool)


def may_have_committed(tool):
    return BRIDGE_TEXT["bridgeMayHaveCommitted"].replace(TOOL, tool)


def rust_binary():
    if os.name == "nt":
        path = PLUGIN_ROOT / "bin" / "tm-relay.exe"
    else:
        arch = "aarch64" if os.uname().machine in ("aarch64", "arm64") else "x86_64"
        path = PLUGIN_ROOT / "bin" / f"tm-relay-linux-{arch}"
    return path if path.is_file() else None


@pytest.fixture(params=RELAYS)
def relay(request):
    """The relay under test as an argv; the rust variant is skipped when no binary exists (failed under TASKMASTER_REQUIRE_RELAY=1)."""
    if request.param == "python":
        return {"kind": "python", "argv": [sys.executable, str(BRIDGE)]}
    return _rust_relay()


@pytest.fixture
def rust_relay():
    return _rust_relay()


def _rust_relay():
    binary = rust_binary()
    if binary is None:
        if os.environ.get("TASKMASTER_REQUIRE_RELAY") == "1":
            pytest.fail("TASKMASTER_REQUIRE_RELAY=1 but no tm-relay binary exists for this platform")
        pytest.skip("no tm-relay binary for this platform yet")
    return {"kind": "rust", "argv": [str(binary)]}


LIVE_SESSIONS = []


def kill_sessions():
    """Stop every relay a test started, by PID. Fixtures that stop a server call this first: a relay still running would relaunch it."""
    while LIVE_SESSIONS:
        LIVE_SESSIONS.pop().kill()


class Session:
    """One relay process with pipes: newline JSON in and out, stderr collected, stopped by PID in teardown."""

    def __init__(self, argv, cwd, env):
        self.proc = subprocess.Popen(argv, cwd=str(cwd), env=env, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                     stderr=subprocess.PIPE, creationflags=NO_WINDOW)
        self.lines, self.errors, self.lock = [], [], threading.Lock()
        LIVE_SESSIONS.append(self)
        threading.Thread(target=self._read_out, daemon=True).start()
        threading.Thread(target=self._read_err, daemon=True).start()

    def _read_out(self):
        for raw in self.proc.stdout:
            try:
                item = json.loads(raw)
            except ValueError:
                item = {"unparsed": raw.decode("utf-8", "replace")}
            with self.lock:
                self.lines.append(item)

    def _read_err(self):
        for raw in self.proc.stderr:
            with self.lock:
                self.errors.append(raw.decode("utf-8", "replace"))

    def send(self, message):
        data = message if isinstance(message, (bytes, str)) else json.dumps(message)
        data = data.encode() if isinstance(data, str) else data
        self.proc.stdin.write(data + b"\n")
        self.proc.stdin.flush()

    def wait_for(self, predicate, timeout=20):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            with self.lock:
                for item in self.lines:
                    if predicate(item):
                        return item
            time.sleep(0.02)
        with self.lock:
            raise AssertionError(f"no matching message within {timeout}s in {self.lines}; stderr {self.errors}")

    def answer(self, mid, timeout=20):
        return self.wait_for(lambda m: m.get("id") == mid, timeout)

    def request(self, mid, method, timeout=20, **params):
        self.send({"jsonrpc": "2.0", "id": mid, "method": method, **({"params": params} if params else {})})
        return self.answer(mid, timeout)

    def call(self, mid, name, timeout=20):
        return self.request(mid, "tools/call", timeout, name=name, arguments={})

    def notify(self, method, **params):
        self.send({"jsonrpc": "2.0", "method": method, **({"params": params} if params else {})})

    def handshake(self, fake=None, timeout=30):
        """initialize + initialized; with a fake, also waits until the relay has an upstream session and its stream (the relay
        answers initialize before the server is up, so state a test sets right after the handshake would race the boot)."""
        result = self.request(1, "initialize", timeout, protocolVersion="2025-06-18", capabilities={},
                              clientInfo={"name": "t", "version": "0"})
        self.notify("notifications/initialized")
        if fake is not None:
            assert wait_until(lambda: fake.inits >= 1 and fake.stream_connects + len(fake.stream_times) >= 1, timeout), \
                f"the relay never connected upstream; stderr {self.stderr()}"
        return result

    def messages(self, method):
        with self.lock:
            return [m for m in self.lines if m.get("method") == method]

    def stderr(self):
        with self.lock:
            return "".join(self.errors)

    def close(self, timeout=30):
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        try:
            return self.proc.wait(timeout=timeout)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait()
            return None

    def kill(self):
        if self.proc.poll() is None:
            self.proc.kill()
            self.proc.wait()


def base_env(tmp_path, root, relay, *, bounds=None, testing=True, unset=(), **extra):
    env = {k: v for k, v in os.environ.items()
           if k not in ("PYTHONPATH", "TASKMASTER_LOCAL_SERVER", "TASKMASTER_RELAY") and not k.upper().startswith("CLAUDE")
           and not k.startswith("TASKMASTER_BRIDGE_")}
    env.update(CLAUDE_CODE_SESSION_ID="sess-bb", TASKMASTER_ROOT=str(root), TASKMASTER_BRIDGE_LOG=str(tmp_path / "bridge.log"))
    if testing:
        stdio = tmp_path / "fake_stdio.py"
        stdio.write_text(FAKE_STDIO, encoding="utf-8")
        env["TASKMASTER_BRIDGE_TESTING"] = "1"
        env["TASKMASTER_BRIDGE_TEST_FALLBACK_ARGV"] = json.dumps([sys.executable, str(stdio)])
        if relay["kind"] == "rust":
            env["TASKMASTER_RELAY_HELPER"] = json.dumps([sys.executable, str(BRIDGE)])
            env["TASKMASTER_BRIDGE_TEST_DELEGATE_ARGV"] = json.dumps([sys.executable, str(BRIDGE)])
    if bounds:
        env["TASKMASTER_BRIDGE_TEST_BOUNDS"] = json.dumps(bounds)
    env.update({k: str(v) for k, v in extra.items()})
    for name in unset:
        env.pop(name, None)
    return env


@pytest.fixture
def launch(tmp_path, native_root):
    """launch(relay, bounds=..., cwd=..., root=..., **env) -> Session; every session is stopped by PID in teardown."""
    def start(relay, *, bounds=None, cwd=None, root=None, testing=True, unset=(), **extra):
        root = root or native_root
        env = base_env(tmp_path, root, relay, bounds=bounds, testing=testing, unset=unset, **extra)
        return Session(relay["argv"], cwd or root, env)
    yield start
    kill_sessions()


@pytest.fixture
def fake_up(native_root):
    """fake_up(**kw) -> (fake, host): a FakeServer published through a real lease and discovery record of the native project."""
    from taskmaster.coordinator.protocol import build_identity
    made = []

    def up(build=None, sse=False, **switches):
        fake = FakeServer(build=build or build_identity(), sse=sse)
        for name, value in switches.items():
            assert hasattr(fake, name), name
            setattr(fake, name, value)
        token_file = native_root / ".taskmaster" / "local" / "serve" / "operator.token"
        server_fence.reset_for_tests()
        if not token_file.exists():
            from taskmaster.serve.auth import ensure_operator_token
            ensure_operator_token(native_root)
        token_file.write_text(TOKEN + "\n", encoding="utf-8")
        host = Host(native_root, fake)
        host.up()
        made.append((fake, host))
        return fake, host
    clear_cache(native_root)
    yield up
    kill_sessions()
    for fake, host in made:
        host.down()
        fake.stop()
    clear_cache(native_root)
    server_fence.reset_for_tests()


def clear_cache(root):
    """The Python bridge caches a live server's handshake per project; a stale one would be served in place of the shipped file."""
    try:
        (root / ".taskmaster" / "local" / "serve" / "bridge-init-cache.json").unlink()
    except OSError:
        pass


def wait_until(predicate, timeout=10, step=0.05):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(step)
    return False


def pid_alive(pid):
    if os.name == "nt":
        import ctypes
        handle = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)
        if not handle:
            return False
        code = ctypes.c_ulong()
        ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        ctypes.windll.kernel32.CloseHandle(handle)
        return code.value == 259
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


# -- framing, responses, headers, the stream (rows 17-19) ---------------------------------------------------------------

@pytest.mark.parametrize("sse,chunked", [(False, False), (True, False), (True, True)], ids=["json", "sse", "sse-chunked"])
def test_json_and_sse_responses_and_202(relay, launch, fake_up, sse, chunked):
    fake, _ = fake_up(sse=sse, chunked=chunked)
    session = launch(relay)
    init = session.handshake(fake)
    assert init["result"]["protocolVersion"] == "2025-06-18"
    assert session.call(2, "read")["result"]["content"][0]["text"] == "ok read"
    session.notify("notifications/roots/list_changed")
    assert session.call(3, "progress")["result"]["content"][0]["text"] == "done"
    assert wait_until(lambda: fake.inits >= 1)


def test_framing_blank_lines_parse_errors_batches_and_non_objects(relay, launch, fake_up):
    fake, _ = fake_up()
    session = launch(relay)
    session.handshake(fake)
    session.send(b"")
    session.send(b"{not json")
    assert session.wait_for(lambda m: m.get("error", {}).get("code") == -32700)["id"] is None
    session.send(json.dumps([{"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "a", "arguments": {}}},
                             {"jsonrpc": "2.0", "id": 11, "method": "tools/call", "params": {"name": "b", "arguments": {}}}, 5, "x"]))
    assert session.answer(10)["result"]["content"][0]["text"] == "ok a"
    assert session.answer(11)["result"]["content"][0]["text"] == "ok b"
    assert session.call(12, "after")["result"]["content"][0]["text"] == "ok after"


def test_requests_carry_session_headers_token_and_protocol(relay, launch, fake_up, native_root):
    fake, _ = fake_up()
    session = launch(relay, cwd=native_root)
    session.handshake(fake)
    session.call(2, "read")
    headers = [{k.lower(): v for k, v in h.items()} for h in fake.seen_headers]
    last = headers[-1]
    assert last["authorization"] == "Bearer " + TOKEN
    assert last["x-taskmaster-session"] == "sess-bb"
    assert re.fullmatch(r"[0-9a-f]{16}", last["x-taskmaster-session-nonce"])
    assert len({h["x-taskmaster-session-nonce"] for h in headers}) == 1
    assert ls.encode_cwd(str(native_root)) == last["x-taskmaster-cwd"]
    assert last["mcp-session-id"] == "sess-1" and last["mcp-protocol-version"] == "2025-06-18"
    assert "mcp-session-id" not in headers[0] and "mcp-protocol-version" not in headers[0]


def test_the_discriminator_is_a_random_bridge_id_without_a_claude_session_id(relay, launch, fake_up):
    fake, _ = fake_up()
    session = launch(relay, unset=("CLAUDE_CODE_SESSION_ID",))
    session.handshake(fake)
    session.call(2, "read")
    assert re.fullmatch(r"bridge-[0-9a-f]{16}", {k.lower(): v for k, v in fake.seen_headers[-1].items()}["x-taskmaster-session"])


@pytest.mark.parametrize("chunked", [False, True], ids=["close-delimited", "chunked"])
def test_get_stream_is_held_open_forwarded_without_internal_ids_and_closed_with_delete(relay, launch, fake_up, chunked):
    fake, _ = fake_up(chunked=chunked)
    session = launch(relay)
    session.handshake(fake)
    assert wait_until(lambda: fake.stream_connects == 1)
    session.call(2, "internal")              # makes the server push a tm-bridge-* message and a visible one on the stream
    session.wait_for(lambda m: m.get("method") == "notifications/message")
    assert not [m for m in session.lines if str(m.get("id", "")).startswith("tm-bridge-")]
    assert session.close() == 0
    assert fake.deleted == ["sess-1"]


def test_a_get_answered_500_is_retried_no_sooner_than_the_backoff(relay, launch, fake_up):
    fake, _ = fake_up(get_status=500)
    session = launch(relay)
    session.handshake(fake)
    assert wait_until(lambda: len(fake.stream_times) >= 2, timeout=10)
    first, second = fake.stream_times[:2]
    assert second - first >= 1.5, "a refused stream must wait step * failures before the next GET"


# -- retry rule, reconnect, wording (rows 22-24) ----------------------------------------------------------------------

def test_an_unknown_session_404_is_reinitialized_and_the_write_runs_once(relay, launch, fake_up):
    fake, _ = fake_up()
    session = launch(relay)
    session.handshake(fake)
    fake.sessions.clear()
    assert session.call(2, "write")["result"]["content"][0]["text"] == "ok write"
    assert fake.writes == 1 and fake.inits == 2
    assert wait_until(lambda: fake.stream_connects >= 1)


def test_two_requests_failing_on_the_same_loss_reinitialize_exactly_once(relay, launch, fake_up):
    fake, _ = fake_up()
    session = launch(relay)
    session.handshake(fake)
    fake.sessions.clear()
    session.send({"jsonrpc": "2.0", "id": 20, "method": "tools/list"})
    session.send({"jsonrpc": "2.0", "id": 21, "method": "ping"})
    session.send({"jsonrpc": "2.0", "id": 22, "method": "logging/setLevel"})
    session.answer(20)
    session.answer(21)
    session.answer(22)
    assert fake.inits == 2, "one reconnect per loss (the epoch guard)"


def test_a_replaced_upstream_session_is_deleted(relay, launch, fake_up):
    fake, _ = fake_up()
    session = launch(relay)
    session.handshake(fake)
    fake.sessions.discard("sess-1")
    session.call(2, "read")
    assert wait_until(lambda: fake.inits == 2)
    assert wait_until(lambda: "sess-1" in fake.deleted)


def test_a_server_that_restarts_with_another_build_is_reconnected_and_list_changed_follows(relay, launch, fake_up):
    first, host1 = fake_up()
    session = launch(relay)
    session.handshake(first)
    before = len(session.messages("notifications/tools/list_changed"))
    host1.down()
    first.stop()
    newer = {"version": "9.9.9", "digest": "c" * 32}
    second, _ = fake_up(build=newer)
    assert session.call(2, "write")["result"]["content"][0]["text"] == "ok write"
    assert second.writes == 1 and first.writes == 0
    assert wait_until(lambda: len(session.messages("notifications/tools/list_changed")) > before)


def test_an_initialize_refused_by_a_draining_server_reconnects_instead_of_failing(relay, launch, fake_up):
    fake, _ = fake_up()
    fake.draining_initializes = 1
    session = launch(relay)
    init = session.handshake(fake)
    assert "result" in init
    assert session.call(2, "read")["result"]["content"][0]["text"] == "ok read"


def test_unreachable_server_fails_the_call_as_not_sent_in_the_mods_wording(relay, launch, fake_up):
    fake, host = fake_up()
    session = launch(relay, bounds={"RECONNECT_BUDGET": 1.0, "FALLBACK_WINDOW": 0.01})
    session.handshake(fake)
    fake.stop()
    answer = session.call(30, "write", timeout=60)
    text = answer["error"]["message"]
    assert TRANSIENT.search(text)
    if relay["kind"] == "rust":
        assert text == not_sent("write")
    else:
        assert text.startswith('no connected MCP tool "write"') and text.endswith("this call was not sent")


def test_may_have_committed_after_a_cut_response_is_never_retried(relay, launch, fake_up):
    fake, _ = fake_up()
    fake.drop_tools = {"write"}
    session = launch(relay)
    session.handshake(fake)
    text = session.call(8, "write")["error"]["message"]
    assert text == may_have_committed("write") and TRANSIENT.search(text.split("\n")[0])
    time.sleep(1.0)                              # a background reconnect must not replay the call
    assert fake.writes == 1


def test_a_non_tool_request_cut_after_send_is_retried_after_reconnect(relay, launch, fake_up):
    fake, _ = fake_up()
    session = launch(relay)
    session.handshake(fake)
    fake.drop_once_methods = {"tools/list"}
    assert session.request(9, "tools/list")["result"]["tools"]
    assert fake.inits == 2


def test_a_server_http_error_is_returned_not_retried(relay, launch, fake_up):
    fake, _ = fake_up()
    session = launch(relay)
    session.handshake(fake)
    fake.token = "something-else"                # the relay's token is now refused
    answer = session.call(10, "write")
    assert "taskmaster server answered HTTP 401" in answer["error"]["message"]
    assert fake.writes == 0


def test_a_proxy_502_on_a_tool_call_may_have_committed(relay, launch, fake_up):
    fake, _ = fake_up()
    fake.status_for_tool = {"write": 502}
    session = launch(relay)
    session.handshake(fake)
    assert session.call(11, "write")["error"]["message"] == may_have_committed("write")


def test_an_oversized_response_is_refused(relay, launch, fake_up):
    fake, _ = fake_up()
    session = launch(relay)
    session.handshake(fake)
    answer = session.call(12, "big", timeout=120)
    assert "error" in answer and answer["id"] == 12


# -- timeouts (row 21) -------------------------------------------------------------------------------------------------

def test_a_tool_call_longer_than_the_read_bound_completes_and_a_list_past_it_is_cut_and_retried(relay, launch, fake_up):
    fake, _ = fake_up()
    fake.delays = {"tools/call:slow": 3.0}
    fake.delay_once = {"prompts/list": 3.0}      # not tools/list: the relay's own boot reconcile may use that one
    session = launch(relay, bounds={"SHORT_TIMEOUT": 1.0})
    session.handshake(fake)
    assert session.call(2, "slow", timeout=30)["result"]["content"][0]["text"] == "ok slow"
    assert "result" in session.request(3, "prompts/list", timeout=30)
    assert fake.inits == 2, "the cut list forced a reconnect and a retry"


# -- token privacy (row 33) -------------------------------------------------------------------------------------------

def test_the_token_never_reaches_the_log_file_or_stderr(relay, launch, fake_up, tmp_path):
    fake, _ = fake_up()
    session = launch(relay)
    session.handshake(fake)
    fake.sessions.clear()
    session.call(2, "read")                      # one reconnect
    session.close()
    assert TOKEN not in session.stderr()
    log = tmp_path / "bridge.log"          # base_env switches the debug log on; a relay that writes none must fail here, not pass
    assert log.exists(), "the relay ignored TASKMASTER_BRIDGE_LOG"
    text = log.read_text(encoding="utf-8", errors="replace")
    assert "tm-bridge: " in text, text
    assert TOKEN not in text


# -- test mode is honoured only with TASKMASTER_BRIDGE_TESTING=1 (production env must not redirect spawns) ---------------

REDIRECT_VARIABLES = ("TASKMASTER_RELAY_HELPER", "TASKMASTER_BRIDGE_TEST_SERVER_ARGV", "TASKMASTER_BRIDGE_TEST_FALLBACK_ARGV",
                      "TASKMASTER_BRIDGE_TEST_DELEGATE_ARGV")


@pytest.mark.parametrize("testing", [None, "true"], ids=["unset", "true"])
@pytest.mark.parametrize("scenario", ["normal", "python", "off"])
def test_test_mode_variables_are_ignored_without_the_testing_flag(relay, launch, fake_up, tmp_path, testing, scenario):
    """Production must not let an environment variable redirect what the relay spawns: every test variable (spawn redirects
    pointing at a marker-writing sentinel, and the bounds) is set with TASKMASTER_BRIDGE_TESTING not exactly "1"."""
    if not shutil.which("uv"):
        pytest.skip("uv is not installed: the relay's real helper and the real stdio fallback both run through it")
    if scenario == "python" and relay["kind"] != "rust":
        pytest.skip("TASKMASTER_RELAY=python is a Rust-relay switch (delegation to the Python bridge)")
    fake, _ = fake_up()
    markers = {name: tmp_path / f"marker-{name}" for name in REDIRECT_VARIABLES}
    extra = {name: json.dumps([sys.executable, "-c", f"open({str(marker)!r}, 'a').write('x')"]) for name, marker in markers.items()}
    extra["TASKMASTER_BRIDGE_TEST_BOUNDS"] = json.dumps({"NOT_A_BOUND": 1})
    if testing:
        extra["TASKMASTER_BRIDGE_TESTING"] = testing
    if scenario == "python":
        extra["TASKMASTER_RELAY"] = "python"
    if scenario == "off":
        extra["TASKMASTER_LOCAL_SERVER"] = "0"
    session = launch(relay, testing=False, **extra)
    init = session.handshake(fake if scenario != "off" else None, timeout=120)
    assert "result" in init and "unknown test bound" not in session.stderr()
    if scenario != "off":
        assert session.call(2, "read", 120)["result"]["content"][0]["text"] == "ok read"
    session.close()
    assert not [name for name, marker in markers.items() if marker.exists()], "a test variable redirected a spawn without the flag"


def test_an_unknown_test_bound_exits_at_startup_in_test_mode(relay, launch, fake_up):
    fake_up()
    session = launch(relay, bounds={"NOT_A_BOUND": 1})
    assert session.proc.wait(timeout=20) not in (0, None)
    assert "tm-bridge: unknown test bound NOT_A_BOUND" in session.stderr()


# -- cold start, fallback and the launch path (rows 26-30), against the fake launcher ----------------------------------------

class Cold:
    """The relay's launches start tests/fake_local_server.py; controls (startup_delay, result_status, serve_shipped, ...) are a JSON file."""

    def __init__(self, root, tmp_path):
        self.root, self.control = root, tmp_path / "control.json"
        self.configure()
        self.argv = json.dumps([sys.executable, str(FAKE_LAUNCHER), "--root", "{root}", "--result-file", "{result_file}",
                                "--control", str(self.control)])

    def configure(self, **controls):
        self.control.write_text(json.dumps(controls), encoding="utf-8")

    def stop(self):
        pids = self.root / ".taskmaster" / "local" / "serve" / "fake-server.pids"
        try:
            listed = [int(part) for part in pids.read_text(encoding="ascii").split()]
        except (OSError, ValueError):
            listed = []
        for pid in listed:
            if pid_alive(pid):
                try:
                    os.kill(pid, 9)
                except OSError:
                    pass
        try:
            pids.unlink()
        except OSError:
            pass
        time.sleep(0.3)
        ls.remove_discovery(self.root)


@pytest.fixture
def cold(native_root, tmp_path):
    server_fence.reset_for_tests()
    ls.remove_discovery(native_root)
    clear_cache(native_root)
    state = Cold(native_root, tmp_path)
    yield state
    kill_sessions()
    state.stop()
    clear_cache(native_root)
    server_fence.reset_for_tests()


def plugin_copy(relay, tmp_path):
    """A plugin directory with no shipped handshake, to see the relay without one: a copy of the relay entry (the executable under
    bin/, or tm_bridge.py with the taskmaster package and its version manifest linked in)."""
    plug = tmp_path / "plug"
    (plug / "bin").mkdir(parents=True)
    if relay["kind"] == "rust":
        target = plug / "bin" / Path(relay["argv"][0]).name
        target.write_bytes(Path(relay["argv"][0]).read_bytes())
        target.chmod(0o755)
        (plug / "tm_bridge.py").write_text("# the relay fails closed without tm_bridge.py beside bin/; it is never run here (the test server argv replaces it)\n")
        return {"kind": "rust", "argv": [str(target)]}
    (plug / "tm_bridge.py").write_bytes(BRIDGE.read_bytes())
    (plug / ".claude-plugin").mkdir()
    (plug / ".claude-plugin" / "plugin.json").write_bytes((PLUGIN_ROOT / ".claude-plugin" / "plugin.json").read_bytes())
    link, target = plug / "taskmaster", PLUGIN_ROOT / "taskmaster"
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True, timeout=30)
    else:
        os.symlink(target, link, target_is_directory=True)
    return {"kind": "python", "argv": [sys.executable, str(plug / "tm_bridge.py")]}


SHIPPED = json.loads((PLUGIN_ROOT / "tm_bridge_handshake.json").read_text(encoding="utf-8"))
KNOWN = ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25")


@pytest.mark.parametrize("asked,echoed", [("2025-11-25", True), ("1999-01-01", False)])
def test_initialize_is_answered_before_the_server_is_up_and_an_unknown_protocol_is_not_echoed(relay, launch, cold, asked, echoed):
    cold.configure(startup_delay=8)
    session = launch(relay, TASKMASTER_BRIDGE_TEST_SERVER_ARGV=cold.argv)
    started = time.monotonic()
    reply = session.request(1, "initialize", 10, protocolVersion=asked, capabilities={}, clientInfo={"name": "t", "version": "0"})
    assert time.monotonic() - started < 6, "initialize must not wait for the server"
    result = reply["result"]
    assert result["serverInfo"]["name"] == SHIPPED["initialize"]["serverInfo"]["name"]
    assert result["instructions"] == SHIPPED["initialize"]["instructions"]
    assert (result["protocolVersion"] == asked) if echoed else (result["protocolVersion"] in KNOWN and result["protocolVersion"] != asked)


def test_the_shipped_handshake_serves_the_list_at_once_calls_wait_and_a_different_live_list_emits_list_changed(relay, launch, cold):
    cold.configure(startup_delay=4)
    session = launch(relay, TASKMASTER_BRIDGE_TEST_SERVER_ARGV=cold.argv)
    session.handshake()
    started = time.monotonic()
    assert session.request(2, "tools/list", 5)["result"]["tools"] == SHIPPED["tools"]
    assert time.monotonic() - started < 3
    session.send({"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "write", "arguments": {}}})
    time.sleep(1.0)
    with session.lock:
        assert not [m for m in session.lines if m.get("id") == 3], "a call that arrives before the server is ready waits for it"
    assert session.answer(3, 90)["result"]["content"][0]["text"] == "ok write"
    session.wait_for(lambda m: m.get("method") == "notifications/tools/list_changed", 30)    # the fake's list differs from the shipped one


def test_no_list_changed_when_the_live_server_matches_what_was_served(relay, launch, cold):
    cold.configure(startup_delay=1.0, serve_shipped=True)
    session = launch(relay, TASKMASTER_BRIDGE_TEST_SERVER_ARGV=cold.argv)
    session.handshake()
    assert session.call(2, "read", 90)["result"]["content"][0]["text"] == "ok read"
    time.sleep(1.5)
    assert not session.messages("notifications/tools/list_changed")


def test_with_no_shipped_handshake_a_list_that_waits_too_long_is_answered_empty_and_list_changed_brings_the_real_one(relay, launch, cold, tmp_path):
    entry = plugin_copy(relay, tmp_path)
    cold.configure(startup_delay=3)
    session = launch(entry, bounds={"LIST_HOLD": 0.5}, TASKMASTER_BRIDGE_TEST_SERVER_ARGV=cold.argv)
    session.handshake()
    started = time.monotonic()
    assert session.request(2, "tools/list", 10)["result"]["tools"] == []
    assert time.monotonic() - started < 2.5
    session.wait_for(lambda m: m.get("method") == "notifications/tools/list_changed", 90)
    assert session.request(3, "tools/list", 30)["result"]["tools"] == LIVE_TOOLS


def test_ping_server_discover_and_the_empty_lists_are_answered_at_once_and_others_give_up_before_the_client_does(relay, launch, cold):
    cold.configure(startup_delay=8)
    session = launch(relay, bounds={"LIST_HOLD": 0.5}, TASKMASTER_BRIDGE_TEST_SERVER_ARGV=cold.argv)
    session.request(1, "initialize", 10, protocolVersion="2025-06-18", capabilities={}, clientInfo={"name": "t", "version": "0"})
    session.notify("notifications/initialized")
    assert session.request("probe", "server/discover", 2)["error"]["code"] == -32601
    assert session.request(2, "ping", 2)["result"] == {}
    assert session.request(3, "prompts/list", 2)["result"] == {"prompts": []}
    assert session.request(4, "resources/list", 2)["result"] == {"resources": []}
    assert session.request(5, "resources/templates/list", 2)["result"] == {"resourceTemplates": []}
    assert "error" in session.request(6, "completion/complete", 10)


def test_a_call_held_past_the_bound_fails_as_not_sent(relay, launch, cold):
    cold.configure(startup_delay=8)
    session = launch(relay, bounds={"HOLD_BOUND": 1.0}, TASKMASTER_BRIDGE_TEST_SERVER_ARGV=cold.argv)
    session.handshake()
    started = time.monotonic()
    text = session.call(5, "write", 20)["error"]["message"]
    assert time.monotonic() - started < 6
    assert text.endswith("this call was not sent") and TRANSIENT.search(text)
    if relay["kind"] == "rust":
        assert text == not_sent("write")


@pytest.mark.parametrize("cancel_after,hold_bound", [(0.3, None), (2.5, None), (0.3, 1.0)], ids=["soon", "late", "on-hold-timeout"])
def test_a_held_call_cancelled_by_the_client_is_dropped_and_never_answered(relay, launch, cold, cancel_after, hold_bound):
    cold.configure(startup_delay=3.5)
    session = launch(relay, bounds={"HOLD_BOUND": hold_bound} if hold_bound else None, TASKMASTER_BRIDGE_TEST_SERVER_ARGV=cold.argv)
    session.handshake()
    session.send({"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "write", "arguments": {}}})
    time.sleep(cancel_after)
    session.notify("notifications/cancelled", requestId=7)
    if hold_bound:                                   # the bound applies to every held call: ask again once the server is up
        deadline, mid = time.monotonic() + 60, 8
        while True:
            answer = session.call(mid, "read", 30)
            if "result" in answer or time.monotonic() > deadline:
                break
            mid += 1
            time.sleep(0.5)
    else:
        answer = session.call(8, "read", 90)
    assert answer["result"]["content"][0]["text"] == "ok read"     # served once the server is up
    time.sleep(1.0)
    with session.lock:
        assert not [m for m in session.lines if m.get("id") == 7], "a cancelled held request gets no answer on any branch"


def test_three_failed_relaunches_after_a_loss_switch_to_the_stdio_child(relay, launch, fake_up, cold):
    fake, host = fake_up()
    cold.configure(result_status="error", result_reason="boom")
    session = launch(relay, bounds={"RECONNECT_BUDGET": 30}, TASKMASTER_BRIDGE_TEST_SERVER_ARGV=cold.argv)
    session.handshake(fake)
    host.down()
    fake.stop()
    assert session.call(20, "read", 90)["result"]["content"][0]["text"] == "stdio read"
    session.wait_for(lambda m: m.get("method") == "notifications/tools/list_changed", 20)
    assert session.call(21, "read", 20)["result"]["content"][0]["text"] == "stdio read"
    assert "per-session stdio server" in session.stderr()
    with session.lock:
        assert not [m for m in session.lines if str(m.get("id", "")).startswith("tm-bridge-")]


def test_opt_outs_use_the_stdio_child_for_the_whole_session(relay, launch, native_root, tmp_path):
    session = launch(relay, TASKMASTER_LOCAL_SERVER="0")
    session.handshake()
    assert session.call(2, "write", 30)["result"]["content"][0]["text"] == "stdio write"
    assert "TASKMASTER_LOCAL_SERVER=0" in session.stderr()
    setting = native_root / ".taskmaster" / "local" / "settings.json"
    setting.write_text('{"local_server": "off"}', encoding="utf-8")
    try:
        second = launch(relay)
        second.handshake()
        assert second.call(2, "write", 30)["result"]["content"][0]["text"] == "stdio write"
        assert "local_server: off" in second.stderr()
    finally:
        setting.unlink()


def test_a_project_with_no_backlog_uses_the_stdio_child(relay, launch, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    session = launch(relay, root=empty, cwd=empty)
    session.handshake()
    assert session.call(2, "write", 30)["result"]["content"][0]["text"] == "stdio write"
    assert "no backlog" in session.stderr()


# -- the loopback rule (row 16) ----------------------------------------------------------------------------------------------

def test_a_non_loopback_endpoint_is_refused_and_the_token_is_never_sent(relay, launch, fake_up, native_root):
    fake, host = fake_up()
    token_file = native_root / ".taskmaster" / "local" / "serve" / "operator.token"
    ls.write_discovery(native_root, state=ls.STATE_SERVING, url=f"http://192.0.2.7:{fake.port}/mcp", pid=os.getpid(),
                       plugin_dir=native_root, token_file=token_file, build=fake.build, started="t", nonce="n")
    session = launch(relay)
    session.handshake()
    assert session.call(2, "write", 60)["result"]["content"][0]["text"] == "stdio write"      # the session fell back, nothing was sent
    assert fake.seen_headers == []


STUB_HELPER = '''
import json, sys
print(json.dumps({"v": 1, "kind": "session", "root": None, "mode": "local", "reason": None, "build": None, "plugin_dir": "x"}), flush=True)
print(json.dumps({"v": 1, "kind": "endpoint", "url": "http://192.0.2.7:%s/mcp" % sys.argv[1], "token": "secret", "build": None}), flush=True)
'''


def test_the_rust_relay_refuses_a_non_loopback_url_from_the_helper_itself(rust_relay, launch, fake_up, tmp_path):
    fake, _ = fake_up()
    stub = tmp_path / "stub_helper.py"
    stub.write_text(STUB_HELPER, encoding="utf-8")
    session = launch(rust_relay, TASKMASTER_RELAY_HELPER=json.dumps([sys.executable, str(stub), str(fake.port)]))
    session.handshake()
    assert session.call(2, "write", 60)["result"]["content"][0]["text"] == "stdio write"
    assert fake.seen_headers == [] and "secret" not in session.stderr()


# -- uv missing (spec 3, M5) ---------------------------------------------------------------------------------------------

def path_without_uv():
    keep = []
    for entry in os.environ.get("PATH", "").split(os.pathsep):
        if entry and not any((Path(entry) / name).exists() for name in ("uv", "uv.exe", "uv.cmd", "uv.bat")):
            keep.append(entry)
    return os.pathsep.join(keep)


def test_with_uv_missing_a_held_call_is_answered_with_the_uv_error_not_after_the_hold_bound(relay, launch, cold):
    cold.configure(no_start=True)                # the Python bridge must not launch a real server through a cached interpreter
    session = launch(relay, PATH=path_without_uv(), TASKMASTER_BRIDGE_TEST_SERVER_ARGV=cold.argv,
                     unset=("TASKMASTER_BRIDGE_TEST_FALLBACK_ARGV", "TASKMASTER_RELAY_HELPER"))
    session.handshake()
    started = time.monotonic()
    answer = session.call(2, "write", 30)
    assert time.monotonic() - started < (2.5 if relay["kind"] == "rust" else 15), "answered at once, not after HOLD_BOUND"
    text = answer["error"]["message"]
    assert text.startswith("taskmaster bridge error: ")
    if relay["kind"] == "rust":
        assert text == "taskmaster bridge error: uv not found on PATH"


# -- team clones (rows 4, 31; spec 3.1) --------------------------------------------------------------------------------

TEAM_KEY = "tm_u_0123456789abcdef_" + "A" * 43


@pytest.fixture
def team(tmp_path):
    """A team-clone project (marker file) and a fake team server that accepts the member key and has sentinel instructions."""
    project = tmp_path / "teamproj"
    (project / ".taskmaster").mkdir(parents=True)
    fake = FakeServer(token=TEAM_KEY)
    fake.instructions, fake.server_name = "TEAM-SENTINEL-INSTRUCTIONS", "team-sentinel"
    url = f"http://127.0.0.1:{fake.port}/mcp"
    (project / ".taskmaster-server.json").write_text(json.dumps({"format": 1, "host_id": "0" * 32, "mode": "server", "url": url}),
                                                     encoding="utf-8")
    env = {"TASKMASTER_SERVER_KEY": TEAM_KEY, "TASKMASTER_SERVER_URL": url, "TASKMASTER_CONFIG_DIR": str(tmp_path / "cfg")}
    yield project, fake, env
    fake.stop()


def test_a_team_clone_gets_the_team_servers_initialize_and_a_write_lands_there_with_the_member_key(relay, launch, team):
    project, fake, env = team
    session = launch(relay, root=project, cwd=project, **env)
    init = session.handshake()["result"]
    assert init["instructions"] == "TEAM-SENTINEL-INSTRUCTIONS" and init["serverInfo"]["name"] == "team-sentinel"
    assert session.call(2, "write", 30)["result"]["content"][0]["text"] == "ok write"
    assert fake.writes == 1
    headers = {k.lower(): v for k, v in fake.seen_headers[-1].items()}
    assert headers["authorization"] == "Bearer " + TEAM_KEY
    assert not [k for k in headers if k.startswith("x-taskmaster")]
    assert not (project / ".taskmaster" / "local").exists(), "a team clone never gets a local store"


# -- Rust-only: the helper, the reconnect episode, the delegates -------------------------------------------------------------

HELPER_WRAPPER = '''
import os, runpy, sys, time
log = os.environ["WRAPPER_LOG"]
with open(log, "a") as out:
    out.write(" ".join(sys.argv[1:]) + " pid=" + str(os.getpid()) + chr(10))
sys.argv = [os.environ["WRAPPER_BRIDGE"]] + sys.argv[1:]
try:
    runpy.run_path(os.environ["WRAPPER_BRIDGE"], run_name="__main__")
finally:
    if os.environ.get("WRAPPER_HANG"):
        time.sleep(600)
'''


def wrapped_helper(tmp_path, **extra):
    script = tmp_path / "helper_wrapper.py"
    script.write_text(HELPER_WRAPPER, encoding="utf-8")
    log = tmp_path / "helper-starts.log"
    env = {"TASKMASTER_RELAY_HELPER": json.dumps([sys.executable, str(script)]), "WRAPPER_LOG": str(log), "WRAPPER_BRIDGE": str(BRIDGE), **extra}
    return log, env


def helper_starts(log):
    try:
        return [line for line in log.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return []


def test_the_helper_is_gone_within_three_seconds_of_its_last_line(rust_relay, launch, fake_up, tmp_path):
    fake, _ = fake_up()
    log, env = wrapped_helper(tmp_path)
    session = launch(rust_relay, **env)
    session.handshake(fake)
    assert wait_until(lambda: helper_starts(log), 10)
    pid = int(helper_starts(log)[0].rsplit("pid=", 1)[1])
    assert wait_until(lambda: not pid_alive(pid), 3 + 1), "the helper must exit once the relay has its endpoint line"


def test_a_helper_that_lingers_is_stopped_five_seconds_after_its_last_line(rust_relay, launch, fake_up, tmp_path):
    fake, _ = fake_up()
    log, env = wrapped_helper(tmp_path, WRAPPER_HANG="1")
    session = launch(rust_relay, **env)
    session.handshake(fake)
    assert wait_until(lambda: "relay helper did not exit; stopped" in session.stderr(), 15)
    pid = int(helper_starts(log)[0].rsplit("pid=", 1)[1])
    assert wait_until(lambda: not pid_alive(pid), 5)


def test_a_loss_with_failed_attempts_spawns_one_ensure_helper(rust_relay, launch, fake_up, cold, tmp_path):
    fake, host = fake_up()
    log, env = wrapped_helper(tmp_path)
    cold.configure(result_status="error", result_reason="boom")
    # FALLBACK_WINDOW is tiny so the failed attempts never add up to FALLBACK_FAILURES: the test must not race the switch to stdio
    session = launch(rust_relay, bounds={"RECONNECT_BUDGET": 30, "FALLBACK_WINDOW": 0.1}, TASKMASTER_BRIDGE_TEST_SERVER_ARGV=cold.argv, **env)
    session.handshake(fake)
    host.down()
    fake.stop()
    session.send({"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "read", "arguments": {}}})
    debug_log = tmp_path / "bridge.log"          # the relay writes its attempt notes to the debug log, as the Python bridge does (row 33)
    assert wait_until(lambda: debug_log.exists() and debug_log.read_text(encoding="utf-8", errors="replace").count("reconnect failed") >= 2,
                      30), debug_log.read_text(encoding="utf-8", errors="replace") if debug_log.exists() else "no debug log"
    cold.configure()                                          # the next launch succeeds
    assert session.answer(5, 60)["result"]["content"][0]["text"] == "ok read"
    ensure_helpers = [line for line in helper_starts(log) if "--ensure-only" in line]
    assert len(ensure_helpers) == 1, helper_starts(log)


def test_taskmaster_relay_python_delegates_the_whole_session_without_a_helper(rust_relay, launch, fake_up, tmp_path):
    fake, _ = fake_up()
    log, env = wrapped_helper(tmp_path)
    session = launch(rust_relay, TASKMASTER_RELAY="python", **env)
    session.handshake(fake)
    assert session.call(2, "read")["result"]["content"][0]["text"] == "ok read"
    assert not helper_starts(log), "no helper runs when the relay delegates at once"


def test_a_team_clone_whose_line_one_comes_late_still_delegates_and_says_so(rust_relay, launch, team):
    project, fake, env = team
    session = launch(rust_relay, root=project, cwd=project, bounds={"INIT_WAIT": 0}, **env)
    session.handshake()
    assert session.call(2, "write", 60)["result"]["content"][0]["text"] == "ok write"
    assert fake.writes == 1
    assert "team clone detected after initialize" in session.stderr()
    session.wait_for(lambda m: m.get("method") == "notifications/tools/list_changed", 20)  # the client re-lists after the swap


def test_the_rust_relay_prints_its_version_and_spawns_nothing(rust_relay):
    out = subprocess.run(rust_relay["argv"] + ["--version"], capture_output=True, text=True, timeout=10, stdin=subprocess.DEVNULL,
                         creationflags=NO_WINDOW)
    assert out.returncode == 0 and re.fullmatch(r"tm-relay \S+ \S+", out.stdout.strip())
