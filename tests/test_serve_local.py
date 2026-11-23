# User intent: prove the local profile of `taskmaster serve` (local-server-002) is the shared per-project server the plan
# promises: one claim per session that no other session can adopt, restarts that keep claims, retire/drain, idle exit,
# launch outcomes in the result file, and refusals that never write a marker or latch.
from __future__ import annotations

import io
import json
import re
import threading
import time
from datetime import timedelta
from types import SimpleNamespace

import httpx
import pytest

from taskmaster import localserve, server_marker
from taskmaster.coordinator import client as client_module
from taskmaster.coordinator.ownership import ownership_held
from taskmaster.native import claims
from taskmaster.serve import local as local_profile
from taskmaster.serve.config import PROFILE_LOCAL, BindRefused, ServeConfig, ServeRefused, StoreOwnedBusy, validate
from taskmaster.serve.daemon import ServeDaemon
from taskmaster.untrusted import unframe
from test_native_service_build import root  # noqa: F401

ADD = {"title": "x", "epic": "test-epic", "phase": "dev"}
ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def poll(predicate, deadline=20.0, step=0.05):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(step)
    return predicate()


class Bridge:
    """What the stdio bridge does over HTTP: initialize, hold the GET stream, post tool calls. `kill` drops the sockets
    with no DELETE, like a hard-killed bridge."""

    def __init__(self, url, token, discriminator, nonce="a1b2c3d4e5f60718", cwd="/work/tree"):
        self.url, self.token, self.discriminator, self.nonce = url, token, discriminator, nonce
        self.http = httpx.Client(timeout=60)
        self.base = {"Authorization": f"Bearer {token}", "X-Taskmaster-Session": discriminator,
                     "X-Taskmaster-Session-Nonce": nonce, "X-Taskmaster-Cwd": cwd, **ACCEPT}
        self.next_id = 1
        self.sid = None
        self.stream_thread = None
        self.stream_open = threading.Event()
        self.alive = True
        self.initialize()

    def initialize(self):
        response = self.http.post(self.url, headers=self.base, json={
            "jsonrpc": "2.0", "id": 0, "method": "initialize", "params": {
                "protocolVersion": "2025-06-18", "capabilities": {},
                "clientInfo": {"name": "claude-code", "version": "1"}}})
        assert response.status_code == 200, response.text
        self.sid = response.headers["mcp-session-id"]
        accepted = self.http.post(self.url, headers={**self.base, "Mcp-Session-Id": self.sid},
                                  json={"jsonrpc": "2.0", "method": "notifications/initialized"})
        assert accepted.status_code == 202, accepted.text
        self.stream_thread = threading.Thread(target=self._stream, daemon=True)
        self.stream_thread.start()
        assert self.stream_open.wait(10), "the GET stream did not open"

    def _stream(self):
        try:
            with self.http.stream("GET", self.url, headers={**self.base, "Mcp-Session-Id": self.sid,
                                                              "Accept": "text/event-stream"}) as stream:
                self.stream_open.set()
                for _line in stream.iter_lines():
                    pass
        except Exception:  # noqa: BLE001 - closed by kill(), by the server's drain, or by close()
            pass
        self.alive = False

    def rpc(self, method, params=None, *, raw=False):
        self.next_id += 1
        response = self.http.post(self.url, headers={**self.base, "Mcp-Session-Id": self.sid},
                                  json={"jsonrpc": "2.0", "id": self.next_id, "method": method, "params": params or {}})
        if raw:
            return response
        assert response.status_code == 200, (response.status_code, response.text)
        return _message(response, self.next_id)

    def call(self, name, arguments=None):
        message = self.rpc("tools/call", {"name": name, "arguments": arguments or {}})
        blocks = [block["text"] for block in message["result"]["content"] if block.get("type") == "text"]
        return unframe(blocks[0]) if blocks else json.dumps(message["result"].get("structuredContent"))

    def kill(self):
        self.http.close()

    def close(self):
        try:
            self.http.delete(self.url, headers={**self.base, "Mcp-Session-Id": self.sid})
        except Exception:  # noqa: BLE001
            pass
        self.http.close()


def _message(response, request_id):
    if response.headers["content-type"].startswith("application/json"):
        return response.json()
    for line in response.text.splitlines():
        if line.startswith("data:"):
            data = json.loads(line[5:])
            if data.get("id") == request_id:
                return data
    raise AssertionError(response.text)


@pytest.fixture
def local(root, monkeypatch):  # noqa: F811
    monkeypatch.setattr(client_module, "AUTOSTART_ALLOWED", True)
    started, bridges = [], []

    def start(**settings):
        settings.setdefault("result_file", root.parent / f"result-{len(started)}.json")
        settings.setdefault("idle_exit_seconds", 0)
        out = io.StringIO()
        daemon = ServeDaemon(ServeConfig(root=root, port=0, profile=PROFILE_LOCAL, **settings),
                             stdout=out).start()
        codes = []
        thread = threading.Thread(target=lambda: codes.append(daemon.serve()), daemon=True)
        thread.start()
        assert daemon.ready.wait(30)
        world = SimpleNamespace(daemon=daemon, url=daemon.url, root=root, thread=thread, codes=codes, out=out,
                                base_url=daemon.url.rsplit("/mcp", 1)[0], result_file=settings["result_file"],
                                token=(root / ".taskmaster/local/serve/operator.token").read_text().strip())
        world.bridge = lambda disc, **kw: bridges.append(Bridge(world.url, world.token, disc, **kw)) or bridges[-1]
        world.auth = {"Authorization": f"Bearer {world.token}"}
        world.stop = lambda: (daemon.request_stop("test"), thread.join(30))
        started.append(world)
        return world
    yield start
    for bridge in bridges:
        bridge.close()
    for world in started:
        world.daemon.request_stop("test teardown")
        world.thread.join(30)
        assert not world.thread.is_alive()


def add_task(bridge, title="T"):
    text = bridge.call("backlog_add_task", dict(ADD, title=title))
    assert "[seq " in text, text
    return re.search(r"`(test-epic-\d+)`", text).group(1)


def session_of(bridge):
    return json.loads(bridge.call("backlog_claim", {"action": "status"}))["session"]


# -- launch, discovery, result file -----------------------------------------------------------------------------------

def test_local_server_starts_publishes_discovery_and_a_ready_result(local):
    w = local()
    result = json.loads(w.result_file.read_text())
    assert result["status"] == "ready" and result["url"] == w.url and result["pid"] > 0
    record = localserve.read_discovery(w.root)
    assert record["state"] == "serving" and record["url"] == w.url and record["root"] == str(w.root)
    assert record["profile"] == "local" and record["token_file"].endswith("operator.token")
    assert w.daemon.port != 7470 and w.url.startswith("http://127.0.0.1:")       # OS-chosen loopback port
    status = httpx.get(w.base_url + localserve.PATH_STATUS, headers=w.auth).json()
    assert status["state"] == "serving" and status["bridges"] == 0 and status["root"] == str(w.root)
    assert status["build"] == record["build"] and set(status) >= {"plugin_dir", "inflight", "idle_seconds"}
    w.stop()
    assert localserve.read_discovery(w.root) is None and not localserve.discovery_path(w.root).exists()


def test_status_and_retire_need_the_operator_token(local):
    w = local()
    assert httpx.get(w.base_url + localserve.PATH_STATUS).status_code == 401
    assert httpx.post(w.base_url + localserve.PATH_RETIRE, json={}).status_code == 401
    assert httpx.get(w.base_url + localserve.PATH_STATUS, headers={"Authorization": "Bearer nope"}).status_code == 401


def test_local_profile_installs_no_team_surface(local):
    from taskmaster.coordinator import client
    w = local()
    from taskmaster import query_guard
    assert client.serving() and w.daemon.config.deny_functions is False   # the coordinator guard stays; the SQL deny list is off
    assert query_guard.DENY_FUNCTIONS_WHEN_SERVING is False and query_guard.Authorizer().deny_functions is False
    assert claims.ADOPT_ACROSS_SESSIONS is False
    assert httpx.get(w.base_url + "/read/v1/meta", headers=w.auth).status_code in (404, 405)
    assert httpx.post(w.base_url + "/write/v1/x", headers=w.auth, json={}).status_code in (404, 405)
    w.stop()
    assert claims.ADOPT_ACROSS_SESSIONS is True and client.ORIGIN_PROVIDER is None
    assert query_guard.DENY_FUNCTIONS_WHEN_SERVING is True        # restored to the default


def test_the_local_profile_names_itself_in_the_handover_listing_and_forgets_it_on_stop(local):
    # local-server-012: the mods' connection status reads mode, project and version off the json listing.
    w = local()
    bs = w.daemon._bs
    a = w.bridge("claude-session-aaa")
    server = json.loads(a.call("backlog_handover_list", {"format": "json"}))["server"]
    assert (server["mode"], server["project"], server["version"], server["reason"]) == ("local", w.root.name, bs.VERSION, "")
    w.stop()
    assert bs.SERVE_PROFILE is None


def test_a_member_key_is_not_accepted_by_the_local_profile(local):
    from taskmaster.serve.principals import add_user
    w = local()
    key, _record, _ = add_user(w.root, "ann", name="Ann", emails=["ann@example.com"])
    assert httpx.get(w.base_url + localserve.PATH_STATUS, headers={"Authorization": f"Bearer {key}"}).status_code == 401


# -- refusals -----------------------------------------------------------------------------------------------------

def test_non_loopback_and_overrides_are_refused():
    for settings in ({"host": "0.0.0.0"}, {"host": "10.1.2.3"}, {"insecure_bind": True}, {"allowed_hosts": ("tm.example.com",)},
                     {"port": 7470}, {"viewer_port": 7471}, {"export_interval": 900}):
        base = {"root": "x", "profile": PROFILE_LOCAL, **settings}
        with pytest.raises(ServeRefused):
            validate(ServeConfig(**base))
    assert validate(ServeConfig(root="x", port=0, profile=PROFILE_LOCAL)).profile == PROFILE_LOCAL
    with pytest.raises(BindRefused):
        validate(ServeConfig(root="x", port=0, host="0.0.0.0", profile=PROFILE_LOCAL))


def test_a_marker_refuses_and_nothing_is_written(root):  # noqa: F811
    import subprocess
    from taskmaster.serve import cli
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    marker = server_marker.write_marker(root, "https://tm.example.com/mcp", server_marker.new_host_id())
    before = marker.read_bytes()
    result = root.parent / "refused.json"
    code = cli.run_main(["--local", "--root", str(root), "--result-file", str(result)])
    assert code == 2
    assert json.loads(result.read_text())["status"] == "refused"
    assert marker.read_bytes() == before                                    # never rewritten
    assert not server_marker.latch_path(root).exists()                      # never latched
    assert not localserve.discovery_path(root).exists()
    assert not (root / ".taskmaster/local/serve/host.lock").exists() or not ownership_held(root)


def test_a_latch_alone_refuses_too(root):  # noqa: F811
    import subprocess
    from taskmaster.serve import cli
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    marker = server_marker.Marker(url="https://tm.example.com/mcp", host_id=server_marker.new_host_id())
    server_marker.record_latch(root, marker)
    assert server_marker.latch_path(root).exists()
    result = root.parent / "latched.json"
    assert cli.run_main(["--local", "--root", str(root), "--result-file", str(result)]) == 2
    assert json.loads(result.read_text())["status"] == "refused"


def test_a_legacy_or_missing_store_refuses(tmp_path):
    from taskmaster.serve import cli
    project = tmp_path / "legacy"
    (project / ".taskmaster/local").mkdir(parents=True)
    result = tmp_path / "legacy.json"
    assert cli.run_main(["--local", "--root", str(project), "--result-file", str(result)]) == 2
    out = json.loads(result.read_text())
    assert out["status"] == "refused" and "native store" in out["reason"]


def test_a_second_server_loses_the_lease_and_says_so(local, root):  # noqa: F811
    from taskmaster.serve import cli
    w = local()
    result = root.parent / "second.json"
    code = cli.run_main(["--local", "--root", str(root), "--result-file", str(result)])
    assert code == 69 and json.loads(result.read_text())["status"] == "lease_held"
    assert localserve.read_discovery(root)["url"] == w.url                  # the winner's record stays


def test_add_user_is_refused_while_a_local_server_runs(local):
    from taskmaster.serve import cli
    w = local()
    assert cli.add_user_main(["ann", "--root", str(w.root)]) == 2
    from taskmaster.serve import principals
    assert not principals.principals_path(w.root).exists() or "ann" not in principals.principals_path(w.root).read_text()


# -- sessions and claims ----------------------------------------------------------------------------------------------

def test_two_bridges_are_two_sessions_derived_from_their_discriminators(local):
    w = local()
    key = local_profile.load_session_key(w.root)
    a, b = w.bridge("claude-session-aaa"), w.bridge("claude-session-bbb")
    assert session_of(a) == local_profile.derive_session_id(key, "operator", "claude-session-aaa")
    assert session_of(b) == local_profile.derive_session_id(key, "operator", "claude-session-bbb")
    assert session_of(a) != session_of(b)
    assert re.fullmatch(r"operator@[0-9a-f]{16}", session_of(a))
    assert (w.root / ".taskmaster/local/serve/session.key").is_file()
    status = httpx.get(w.base_url + localserve.PATH_STATUS, headers=w.auth).json()
    assert status["bridges"] == 2


def test_a_reconnecting_bridge_keeps_its_session_and_its_state(local):
    w = local()
    a = w.bridge("claude-session-aaa", nonce="aaaaaaaa11111111")
    ident = add_task(a)
    assert "Picked" in a.call("backlog_pick_task", {"task_id": ident})
    first = session_of(a)
    a.close()                                                # the same process re-initializes (e.g. after a 404)
    again = w.bridge("claude-session-aaa", nonce="aaaaaaaa11111111")
    assert session_of(again) == first
    assert w.daemon.sessions.by_transport(again.sid).task["id"] == ident        # the bound task followed the bridge
    held = json.loads(again.call("backlog_claim", {"action": "status"}))["claims"]
    assert [claim["task_id"] for claim in held] == [ident]


def test_a_second_live_process_with_one_discriminator_is_a_fresh_session_never_a_merge(local):
    w = local()
    one = w.bridge("claude-session-aaa", nonce="aaaaaaaa11111111")
    two = w.bridge("claude-session-aaa", nonce="bbbbbbbb22222222")        # claude --resume twice
    assert session_of(one) != session_of(two)
    ident = add_task(one)
    assert "Picked" in one.call("backlog_pick_task", {"task_id": ident})
    refused = two.call("backlog_pick_task", {"task_id": ident})
    assert "[seq" not in refused and "Error" in refused, refused


def test_a_claim_is_refused_to_another_session_even_after_quiet_hours(local, monkeypatch):
    w = local()
    a, b = w.bridge("claude-session-aaa"), w.bridge("claude-session-bbb")
    ident = add_task(a)
    assert "Picked" in a.call("backlog_pick_task", {"task_id": ident})
    refused = b.call("backlog_pick_task", {"task_id": ident})
    assert "Error" in refused and "[seq" not in refused and session_of(a) in refused, refused
    real = claims.now_utc
    quiet = lambda: real() + timedelta(hours=5)             # noqa: E731 - past the 4 h claim, with A's bridge still open
    monkeypatch.setattr(claims, "now_utc", quiet)
    monkeypatch.setattr(w.daemon.keeper, "clock", quiet)
    still = b.call("backlog_pick_task", {"task_id": ident})
    assert "Error" in still and "[seq" not in still and "expired" not in still, still
    w.daemon.keeper.run_pass()                                # A's open stream renews the stamp
    again = json.loads(a.call("backlog_claim", {"action": "status"}))["claims"]
    assert again[0]["expired"] is False and again[0]["holder"] == session_of(a)
    assert "Error" in b.call("backlog_pick_task", {"task_id": ident})


def test_a_hard_killed_bridge_frees_its_claim_only_after_the_grace(local, monkeypatch):
    w = local()
    a, b = w.bridge("claude-session-aaa"), w.bridge("claude-session-bbb")
    ident = add_task(a)
    assert "Picked" in a.call("backlog_pick_task", {"task_id": ident})
    holder = session_of(a)
    assert w.daemon.sessions.connected(holder)
    a.kill()                                                 # no DELETE: only the dropped stream says it is gone
    assert poll(lambda: not w.daemon.sessions.connected(holder)), "the dropped GET stream was not noticed"
    w.daemon.keeper.run_pass()                                # shortens the stamp to the disconnected grace
    refused = b.call("backlog_pick_task", {"task_id": ident})
    assert "Error" in refused and "[seq" not in refused and "adopted" not in refused, refused
    assert holder in refused
    real = claims.now_utc
    monkeypatch.setattr(claims, "now_utc", lambda: real() + timedelta(seconds=claims.DISCONNECTED_TTL_SECONDS + 120))
    expired = b.call("backlog_pick_task", {"task_id": ident})
    assert "has expired" in expired and "[seq" not in expired, expired
    released = json.loads(b.call("backlog_claim", {"action": "release", "task_id": ident}))
    assert released["ok"] is True
    assert "[seq" in b.call("backlog_pick_task", {"task_id": ident})
    assert [c["task_id"] for c in json.loads(b.call("backlog_claim", {"action": "status"}))["claims"]] == [ident]


def test_force_still_takes_another_sessions_claim(local):
    w = local()
    a, b = w.bridge("claude-session-aaa"), w.bridge("claude-session-bbb")
    ident = add_task(a)
    a.call("backlog_pick_task", {"task_id": ident})
    assert "[seq" in b.call("backlog_pick_task", {"task_id": ident, "force": True})
    assert json.loads(b.call("backlog_claim", {"action": "status"}))["claims"][0]["task_id"] == ident
    assert json.loads(a.call("backlog_claim", {"action": "status"}))["claims"] == []


def test_a_server_restart_within_the_grace_keeps_the_claim_and_refuses_the_others(local):
    w = local()
    a = w.bridge("claude-session-aaa", nonce="aaaaaaaa11111111")
    ident = add_task(a)
    a.call("backlog_pick_task", {"task_id": ident})
    holder = session_of(a)
    a.kill()
    w.stop()
    w2 = local()
    b = w2.bridge("claude-session-bbb")
    refused = b.call("backlog_pick_task", {"task_id": ident})                  # A has not reconnected yet
    assert "Error" in refused and "[seq" not in refused and "adopted" not in refused, refused
    a2 = w2.bridge("claude-session-aaa", nonce="aaaaaaaa11111111")             # the same bridge, reconnected
    assert session_of(a2) == holder
    claim = json.loads(a2.call("backlog_claim", {"action": "status"}))["claims"][0]
    assert claim["task_id"] == ident and claim["holder"] == holder and claim["expired"] is False
    assert "Error" in b.call("backlog_pick_task", {"task_id": ident})


def test_the_rate_limit_is_per_bridge_not_per_operator_token(local):
    w = local(rate_per_minute=60, rate_burst=5)
    a, b = w.bridge("claude-session-aaa"), w.bridge("claude-session-bbb")
    statuses = [a.rpc("ping", raw=True).status_code for _ in range(12)]
    assert 429 in statuses
    assert b.rpc("ping", raw=True).status_code == 200                      # B has its own bucket


# -- status, retire, drain, idle exit ----------------------------------------------------------------------------------

def post_retire(w, **body):
    return httpx.post(w.base_url + localserve.PATH_RETIRE, headers=w.auth, json=body)


def test_retire_is_refused_while_another_bridge_is_connected(local):
    w = local()
    a, b = w.bridge("claude-session-aaa"), w.bridge("claude-session-bbb")
    refused = post_retire(w, only_if_no_other_bridges=True, requester="claude-session-aaa")
    assert refused.status_code == 409 and "other bridge" in refused.json()["reason"]
    assert httpx.get(w.base_url + localserve.PATH_STATUS, headers=w.auth).json()["state"] == "serving"
    assert post_retire(w, only_if_no_other_bridges="yes").status_code == 400
    b.close()
    assert poll(lambda: httpx.get(w.base_url + localserve.PATH_STATUS, headers=w.auth).json()["bridges"] == 1)
    assert a.alive


def test_retire_drains_closes_streams_refuses_new_traffic_and_exits(local):
    w = local()
    a = w.bridge("claude-session-aaa")
    ident = add_task(a)
    a.call("backlog_pick_task", {"task_id": ident})
    accepted = post_retire(w, only_if_no_other_bridges=True, requester="claude-session-aaa")
    assert accepted.status_code == 202
    assert poll(lambda: not a.alive), "the GET stream was not closed by the drain"
    assert poll(lambda: not w.thread.is_alive(), 30), "the server did not exit after draining"
    assert w.codes == [0] and localserve.read_discovery(w.root) is None
    assert not ownership_held(w.root, localserve.SERVE_DIR, "host.lock")


def test_a_draining_server_answers_new_traffic_with_a_retryable_refusal(local):
    w = local()
    a = w.bridge("claude-session-aaa")
    w.daemon.local.inflight += 1                         # a call still in flight keeps the server draining, not gone
    try:
        assert post_retire(w, only_if_no_other_bridges=False, requester=None).status_code == 202
        assert httpx.get(w.base_url + localserve.PATH_STATUS, headers=w.auth).json()["state"] == "draining"
        assert localserve.read_discovery(w.root)["state"] == "draining"
        assert post_retire(w, only_if_no_other_bridges=False, requester=None).status_code == 202      # idempotent
        late = a.rpc("tools/call", {"name": "backlog_list_tasks", "arguments": {}}, raw=True)
        assert late.status_code == 404 and late.json()["error"]["message"] == "Session not found"
        fresh = httpx.post(w.url, headers={**w.auth, **ACCEPT}, json={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                                                       "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                                                                                  "clientInfo": {"name": "t", "version": "1"}}})
        assert fresh.status_code == 503 and fresh.headers["retry-after"] == "1"
        time.sleep(1.0)
        assert w.thread.is_alive()
    finally:
        w.daemon.local.inflight -= 1
    assert poll(lambda: not w.thread.is_alive(), 30)


class _FakeSessions:
    def __init__(self):
        self.entries = []

    def live(self):
        return list(self.entries)

    def close_streams(self):
        self.closed = True


def _runtime(root_, clock, *, idle=600.0, sessions=None, coordinator=None):
    return local_profile.LocalRuntime(root=root_, url_provider=lambda: "http://127.0.0.1:1/mcp", plugin_dir=root_,
                                      token_file=root_ / "t", sessions=sessions or _FakeSessions(),
                                      coordinator_provider=lambda: coordinator, idle_exit=idle, clock=clock)


def test_idle_exit_needs_no_bridge_no_inflight_no_coordinator_client_and_waits_the_full_window(tmp_path):
    (tmp_path / ".taskmaster/local").mkdir(parents=True)
    now = [1000.0]
    coordinator = SimpleNamespace(last_activity=1000.0, pending={})
    sessions = _FakeSessions()
    runtime = _runtime(tmp_path, lambda: now[0], sessions=sessions, coordinator=coordinator)
    now[0] += 599
    assert runtime.tick() is None
    sessions.entries = [SimpleNamespace(discriminator="a")]               # a bridge stream is open
    now[0] += 5000
    assert runtime.tick() is None and runtime.status()["bridges"] == 1
    sessions.entries = []                                                  # it closes: the window starts now
    now[0] += 599
    assert runtime.tick() is None
    runtime.enter()                                                        # a call in flight
    now[0] += 5000
    assert runtime.tick() is None
    runtime.leave()
    coordinator.last_activity = now[0]                                     # a CLI or hook used the coordinator just now
    now[0] += 599
    assert runtime.tick() is None
    now[0] += 2
    assert "idle for 600" in runtime.tick()


def test_a_signed_in_viewer_request_counts_as_activity(tmp_path):
    (tmp_path / ".taskmaster/local").mkdir(parents=True)
    now = [1000.0]
    runtime = _runtime(tmp_path, lambda: now[0], coordinator=SimpleNamespace(last_activity=1000.0, pending={}))
    now[0] += 500
    runtime.mark_active()
    now[0] += 500
    assert runtime.tick() is None
    now[0] += 101
    assert "idle for 600" in runtime.tick()


def test_refusal_warnings_are_logged_once_a_minute_per_key(caplog):
    from taskmaster.serve import daemon as serve_daemon
    serve_daemon._REFUSAL_LOGGED.clear()
    with caplog.at_level("WARNING", logger="taskmaster.serve"):
        for _ in range(50):
            serve_daemon._warn_rarely("hs:x", "refused %s", "x")
    assert len(caplog.records) == 1


def test_idle_exit_is_immediate_when_the_store_root_disappears(tmp_path):
    (tmp_path / ".taskmaster/local").mkdir(parents=True)
    runtime = _runtime(tmp_path, lambda: 0.0, idle=0)
    assert runtime.tick() is None
    (tmp_path / ".taskmaster/local").rmdir()
    assert runtime.tick() == "the store root disappeared"


def test_a_draining_runtime_exits_only_when_nothing_is_in_flight(tmp_path):
    (tmp_path / ".taskmaster/local").mkdir(parents=True)
    runtime = _runtime(tmp_path, lambda: 0.0, idle=0)
    runtime.enter()
    assert runtime.retire(only_if_no_other_bridges=False, requester=None)[0] == 202
    assert runtime.sessions.closed is True and runtime.state == "draining"
    assert runtime.tick() is None
    runtime.leave()
    assert runtime.tick() == "retired"


def test_the_server_exits_by_itself_after_the_last_bridge_goes_and_removes_its_discovery(local):
    w = local(idle_exit_seconds=2.0)
    a = w.bridge("claude-session-aaa")
    time.sleep(3.5)
    assert w.thread.is_alive(), "it exited while a bridge stream was open"
    a.close()
    assert poll(lambda: not w.thread.is_alive(), 30), "it did not idle out"
    assert w.codes == [0] and not localserve.discovery_path(w.root).exists()


def test_the_discovery_of_a_successor_survives_an_old_generations_exit(local):
    w = local()
    w.daemon.local.nonce = "somebody-elses-generation"                    # as if a successor had replaced local.json
    w.daemon.local.nonce = "old-generation"
    localserve.write_discovery(w.root, state="serving", url="http://127.0.0.1:9/mcp", pid=1, plugin_dir=w.root,
                               token_file=w.root / "t", build=w.daemon.local.build, started="x", nonce="successor")
    w.stop()
    assert localserve.read_private_json(localserve.discovery_path(w.root))["nonce"] == "successor"
    localserve.remove_discovery(w.root)


# -- taking the store over from a session coordinator ---------------------------------------------------------------

def _session_coordinator(root_, *, busy=False):
    from taskmaster.coordinator.service import Coordinator
    owner = Coordinator(root_)
    owner.start()
    if busy:
        owner.active_syncs = 1                                           # status reports it; shutdown must not be sent

    def lifecycle():                                                     # what service.main does around a stop
        while not owner.stopping.wait(0.05):
            pass
        owner.close()
    thread = threading.Thread(target=lifecycle, daemon=True)
    thread.start()
    return owner, thread


def test_an_idle_session_coordinator_is_handed_over(local, root):  # noqa: F811
    owner, thread = _session_coordinator(root)
    assert ownership_held(root)
    w = local()
    thread.join(10)
    assert owner.stopping.is_set() and not thread.is_alive()
    assert json.loads(w.result_file.read_text())["status"] == "ready"
    assert ownership_held(root) and w.daemon.coordinator is not owner     # the server's own coordinator holds it now


def test_a_busy_session_coordinator_is_retried_then_reported_store_owned_busy(root, monkeypatch):  # noqa: F811
    from taskmaster.serve import cli
    monkeypatch.setattr(local_profile, "HAND_OVER_WAIT_SECONDS", 1.0)
    owner, thread = _session_coordinator(root, busy=True)
    try:
        result = root.parent / "busy.json"
        started = time.monotonic()
        code = cli.run_main(["--local", "--root", str(root), "--result-file", str(result)])
        assert code == 68 and 0.9 <= time.monotonic() - started < 15
        out = json.loads(result.read_text())
        assert out["status"] == "store_owned_busy" and "busy" in out["reason"]
        assert not owner.stopping.is_set()                                # the busy owner was left alone
        assert not localserve.discovery_path(root).exists()
    finally:
        owner.active_syncs = 0
        owner.stop()
        thread.join(10)


def test_the_hand_over_helper_raises_store_owned_busy(root):  # noqa: F811
    owner, thread = _session_coordinator(root, busy=True)
    try:
        with pytest.raises(StoreOwnedBusy):
            local_profile.hand_over_session_coordinator(root, wait=0.6)
    finally:
        owner.active_syncs = 0
        owner.stop()
        thread.join(10)


# -- session-scoped outputs --------------------------------------------------------------------------------------------

def test_one_heartbeat_row_per_bridge_session_with_the_bridges_cwd(local):
    import sqlite3
    w = local()
    a, b = w.bridge("claude-session-aaa", cwd="/work/alpha"), w.bridge("claude-session-bbb", cwd="/work/beta")
    add_task(a)
    assert w.daemon.local.heartbeat_sessions() == 2
    rows = sqlite3.connect(str(w.root / ".taskmaster/local/store.db")).execute(
        "SELECT session, cwd FROM sessions WHERE session LIKE 'operator@%' ORDER BY cwd").fetchall()
    assert rows == [(session_of(a), "/work/alpha"), (session_of(b), "/work/beta")]


def test_session_scoped_output_matches_a_stdio_run(local):
    """The same calls through the shared server and through an in-process stdio-style client answer in the same
    shape; only the session id differs by design (host-pid-nonce under stdio, operator@hex here)."""
    import asyncio
    from fastmcp import Client
    from taskmaster import backlog_server as bs
    w = local()
    a = w.bridge("claude-session-aaa")
    ident = add_task(a)

    async def stdio(name, arguments):
        async with Client(bs.mcp._inner) as client:
            return (await client.call_tool(name, arguments, raise_on_error=False)).content[0].text
    served_pick = a.call("backlog_pick_task", {"task_id": ident})
    served = json.loads(a.call("backlog_claim", {"action": "status"}))
    stdio_pick = unframe(asyncio.run(stdio("backlog_pick_task", {"task_id": ident, "force": True})))
    stdio_status = json.loads(unframe(asyncio.run(stdio("backlog_claim", {"action": "status"}))))
    assert served_pick.split(" ")[0] == "Picked" and stdio_pick.split(" ")[0] in ("Picked", "Already")
    assert set(served) == set(stdio_status)
    assert re.fullmatch(r"operator@[0-9a-f]{16}", served["session"])
    assert not re.fullmatch(r"operator@[0-9a-f]{16}", stdio_status["session"])
    assert set(served["claims"][0]) == set(stdio_status["claims"][0])
    assert served["claims"][0]["holder"] == served["session"] and stdio_status["claims"][0]["holder"] == stdio_status["session"]


def test_local_profile_has_no_team_layers_and_no_session_idle_timeout(local):
    from taskmaster import backlog_server as bs
    from taskmaster import untrusted
    from taskmaster.serve.claim_keeper import AdoptionNotice
    from taskmaster.serve.framing import FrameUntrusted
    from taskmaster.serve.guard import HeavyCalls, MemberQueryScope
    w = local()
    layers = {type(m) for m in bs.mcp._inner.middleware}
    assert not layers & {FrameUntrusted, AdoptionNotice, MemberQueryScope, HeavyCalls}
    # hardening M-2: the local operator gets the stdio wording; the team profile's member wording and SERVER_NOTE stay team-only.
    assert bs.mcp._inner.instructions == bs.server_instructions(server_mode=False)
    assert "Members cannot run backlog_sync" not in bs.mcp._inner.instructions
    assert "Members cannot run backlog_sync" in bs.server_instructions(server_mode=True)
    assert untrusted.SERVER_NOTE not in bs.mcp._inner.instructions
    assert w.daemon.sessions._manager.session_idle_timeout is None
    a = w.bridge("claude-session-aaa")
    message = a.rpc("tools/call", {"name": "backlog_list_tasks", "arguments": {}})
    assert len(message["result"]["content"]) == 1                          # no adoption block, no framing
    assert untrusted.unframe(message["result"]["content"][0]["text"]) == message["result"]["content"][0]["text"]


def test_a_real_serve_local_process_launches_hidden_serves_and_idles_out(root, tmp_path):  # noqa: F811
    """The packaged command, as the bridge will start it: no window, outcome in the result file, exit after the idle window."""
    import os
    import subprocess
    import sys
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    from taskmaster.coordinator.ownership import private_directory
    result = private_directory(Path(root), localserve.SERVE_DIR) / "launch-result.json"      # the reader insists on a private directory
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
    child = subprocess.Popen(
        [sys.executable, str(repo / "taskmaster_cli.py"), "serve", "--local", "--root", str(root),
         "--result-file", str(result), "--idle-exit", "3", "--plugin-dir", str(repo)],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=open(tmp_path / "stderr.log", "wb"),
        creationflags=flags, env={**os.environ, "PYTHONPATH": str(repo)})
    try:
        out = localserve.read_result(result, timeout=60)
        assert out["status"] == "ready" and out["pid"] > 0, (out, (tmp_path / "stderr.log").read_text(errors="replace")[-3000:])
        record = localserve.read_discovery(root)
        # On Windows the venv's python.exe is a launcher, so the server's own pid (result and discovery) can differ from child.pid.
        assert record["pid"] == out["pid"] and record["plugin_dir"] == str(repo)
        token = (root / ".taskmaster/local/serve/operator.token").read_text().strip()
        status = httpx.get(record["url"].rsplit("/mcp", 1)[0] + localserve.PATH_STATUS,
                           headers={"Authorization": f"Bearer {token}"}).json()
        assert status["state"] == "serving" and status["bridges"] == 0
        assert (root / ".taskmaster/local/serve/serve.log").is_file()
        assert child.wait(60) == 0                                          # idle exit: no bridge ever connected
        assert not localserve.discovery_path(root).exists()
        assert not server_marker.marker_path(root).exists()
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(10)


def test_deny_functions_by_profile_and_query_behaviour(local):
    from taskmaster import query_guard
    from taskmaster.serve.config import PROFILE_TEAM
    assert ServeConfig(root="x").deny_functions is True and ServeConfig(root="x", profile=PROFILE_TEAM).deny_functions is True
    assert ServeConfig(root="x", profile=PROFILE_LOCAL).deny_functions is False
    w = local()
    a = w.bridge("claude-session-aaa")
    answer = a.call("backlog_query", {"sql": "SELECT hex('a') AS h"})
    assert "denied" not in answer.lower() and "not authorized" not in answer.lower() and "61" in answer, answer
    w.stop()
    assert query_guard.DENY_FUNCTIONS_WHEN_SERVING is True


def test_serve_log_is_written_once_by_its_own_handler_in_the_local_profile(local, root):  # noqa: F811
    """The launcher gives the server no stderr file of serve.log's, and the daemon adds no stream handler of its own, so each
    line appears once and the rotating handler owns the file (002 M1)."""
    import logging
    w = local()
    logging.getLogger("taskmaster.serve").info("line-written-once-7c1")
    for handler in w.daemon._logging.handlers:
        handler.flush()
    assert not [h for h in w.daemon._logging.handlers if type(h) is logging.StreamHandler]
    text = (root / ".taskmaster/local/serve/serve.log").read_text(encoding="utf-8")
    assert text.count("line-written-once-7c1") == 1


def test_a_team_run_still_logs_to_stderr_as_well(root, tmp_path):  # noqa: F811
    import logging
    from taskmaster.serve.daemon import configure_logging
    team = configure_logging(root, "info")
    try:
        assert [h for h in team.handlers if type(h) is logging.StreamHandler]
    finally:
        team.close()


def test_a_non_numeric_idle_exit_still_writes_a_result_file(root):  # noqa: F811
    from pathlib import Path
    from taskmaster.coordinator.ownership import private_directory
    from taskmaster.serve import cli
    result = private_directory(Path(root), localserve.SERVE_DIR) / "launch-result.json"
    code = cli.run_main(["--local", "--root", str(root), "--result-file", str(result), "--idle-exit", "soon"])
    assert code == 2
    out = localserve.read_result(result, timeout=5)
    assert out["status"] == "refused" and "--idle-exit" in out["reason"], out


def test_local_refuses_team_flags_instead_of_ignoring_them(root):  # noqa: F811
    from pathlib import Path
    from taskmaster.coordinator.ownership import private_directory
    from taskmaster.serve import cli
    for flag in (["--port", "9000"], ["--export-interval", "60"], ["--viewer-origin", "https://v.example"],
                 ["--allowed-host", "x.example"], ["--insecure-bind"], ["--export-push", "origin"]):
        result = private_directory(Path(root), localserve.SERVE_DIR) / "launch-result.json"
        result.unlink(missing_ok=True)
        assert cli.run_main(["--local", "--root", str(root), "--result-file", str(result), *flag]) == 2, flag
        out = localserve.read_result(result, timeout=5)
        assert out["status"] == "refused" and flag[0] in out["reason"], (flag, out)


def test_local_refuses_an_unset_export_interval_and_a_push(root):  # noqa: F811
    for settings in ({"export_interval": None}, {"export_push": "origin"}):
        with pytest.raises(ServeRefused):
            validate(ServeConfig(root=root, port=0, profile=PROFILE_LOCAL, idle_exit_seconds=0, **settings))


class FakeBusy:
    """What the runtime sees of a coordinator whose queue is empty but whose Git run or sync job is in flight."""
    pending = 0
    last_activity = 0.0

    def __init__(self, busy):
        self.busy = busy

    def busy_now(self):
        return self.busy


def test_drain_and_idle_exit_count_every_busy_item_not_only_the_queue(local):
    w = local()
    runtime = w.daemon.local
    real = w.daemon.coordinator
    fake = FakeBusy(True)
    runtime._coordinator = lambda: fake
    try:
        runtime.state = localserve.STATE_DRAINING
        assert runtime.tick() is None                       # draining, but a sync job is running: not yet
        fake.busy = False
        assert runtime.tick() == "retired"
    finally:
        runtime._coordinator = lambda: real
        runtime.state = localserve.STATE_SERVING


def serve_like_main(owner):
    """What service.main does: close the owner (releasing owner.lock) once it is stopping."""
    def run():
        owner.stopping.wait()
        owner.close()
    thread = threading.Thread(target=run, daemon=True)
    thread.start()
    return thread


def test_a_busy_coordinator_is_not_shut_down_by_the_hand_over_check(root):  # noqa: F811
    from taskmaster.coordinator.service import Coordinator
    owner = Coordinator(root)
    owner.start()
    try:
        owner.git_active = object()                         # a managed Git run is in flight; the queue is empty
        assert owner.queue.qsize() == 0 and not owner.pending
        assert owner.shutdown_if_idle() == {"state": "busy"} and not owner.stopping.is_set()
        owner.git_active = None
        assert owner.shutdown_if_idle() == {"state": "stopping"} and owner.stopping.is_set()
    finally:
        owner.git_active = None
        owner.close()


def test_the_hand_over_leaves_a_session_coordinator_with_git_work_running_and_reports_busy(root):  # noqa: F811
    from taskmaster.coordinator.service import Coordinator
    owner = Coordinator(root)
    owner.start()
    closer = serve_like_main(owner)
    try:
        owner.git_active = object()
        with pytest.raises(StoreOwnedBusy):
            local_profile.hand_over_session_coordinator(root, wait=0.6)
        assert not owner.stopping.is_set()
        owner.git_active = None
        local_profile.hand_over_session_coordinator(root, wait=10)       # idle now: it leaves
        assert owner.stopping.is_set() and not ownership_held(root)
    finally:
        owner.git_active = None
        owner.close()


def test_the_hand_over_retires_a_session_coordinator_of_another_build(root):  # noqa: F811
    from taskmaster.coordinator.service import Coordinator
    older = {"version": "0.0.1", "digest": "9" * 32}
    owner = Coordinator(root)
    owner.build = older
    owner.start()
    serve_like_main(owner)
    try:
        local_profile.hand_over_session_coordinator(root, wait=20)
        assert owner.stopping.is_set() and not ownership_held(root)
    finally:
        owner.close()
