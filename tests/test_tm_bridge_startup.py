# User intent: pin the review-fix behaviours of the bridge (any startup error falls back to stdio; initialize race; upgrade-day relaunch).
from __future__ import annotations

import io
import json
import os
import subprocess
import threading
import time
from pathlib import Path

import pytest

import tm_bridge as tb
from taskmaster import localserve as ls
from test_tm_bridge import BUILD, OLDER, TOKEN, FakeServer, Host, Out, handshake, msg, project, server, token_matches  # noqa: F401


def _junction(link: Path, target: Path):
    target.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "mklink", "/J", str(link), str(target)], check=True, capture_output=True)
    else:
        link.symlink_to(target, target_is_directory=True)


def _unlink_junction(link: Path):
    if os.name == "nt":
        subprocess.run(["cmd", "/c", "rmdir", str(link)], check=False, capture_output=True)
    else:
        link.unlink()


# -- I2: the session id is recorded before the initialize result is emitted -------------------------------------------

def test_instant_client_reply_after_initialize_carries_the_session_id(server, tmp_path):
    fake = server(sse=True)
    fake.sse_hold = 1.0                               # the SSE stream stays open after the result, as the real server's can
    holder = {}

    class Instant(Out):
        def write(self, data):
            super().write(data)
            if b'"protocolVersion"' in data and "bridge" in holder:
                bridge = holder.pop("bridge")
                for message in (msg("notifications/initialized"), msg("tools/list", 2)):
                    threading.Thread(target=bridge.handle, args=(message,), daemon=True).start()

    out = Instant()
    bridge = tb.Bridge(tmp_path, BUILD, "disc-1", stdout=out, ensure=fake.endpoint, log=tb.Log(stream=io.StringIO()), budget=10)
    bridge.start()
    holder["bridge"] = bridge
    bridge.handle(msg("initialize", 1, protocolVersion="2025-06-18"))
    assert out.wait_for(lambda m: m.get("id") == 2)["result"]["tools"]
    follow_ups = [h for h in fake.seen_headers if h.get("Mcp-Session-Id")]
    assert len(fake.seen_headers) == 3 and len(follow_ups) == 2 and fake.inits == 1
    bridge.closed.set()


# -- I3: upgrade day, two plugin builds ---------------------------------------------------------------------------

def _draining_old(project, old, host, free_after=0.4):
    def on_retire():
        record = old.record(str(host.token_file))
        ls.write_discovery(project, state=ls.STATE_DRAINING, url=record["url"], pid=os.getpid(), plugin_dir=project,
                           token_file=host.token_file, build=old.build, started="t", nonce="n")
        threading.Timer(free_after, host.down).start()
    return on_retire


def test_a_bridge_that_did_not_retire_waits_for_its_successor_instead_of_relaunching_the_old_build(server, project):
    token_matches(project)
    old, new = server(build=OLDER), server(build=BUILD)
    host = Host(project, old)
    host.up()
    launches = []
    old_result = {}

    def launch(root, plugin_dir, log=None):
        launches.append(plugin_dir)
        assert plugin_dir == "new_dir"
        time.sleep(0.5)                                   # the new build takes a moment to come up
        host.fake = new
        host.up()
        return {"status": "ready", "reason": ""}

    def old_bridge():
        old_result["endpoint"] = tb.ensure_server(project, OLDER, "old", plugin_dir="old_dir",
                                                  launch=lambda *a, **k: launches.append("old_dir") or {"status": "error", "reason": "x"})
    thread = threading.Thread(target=old_bridge)
    base = _draining_old(project, old, host)

    def on_retire():
        base()
        thread.start()                                    # an old session's bridge notices the drain at once
    old.on_retire = on_retire
    endpoint = tb.ensure_server(project, BUILD, "new", plugin_dir="new_dir", launch=launch)
    thread.join(20)
    assert endpoint.port == new.port and old_result["endpoint"].port == new.port
    assert launches == ["new_dir"]


def test_a_foreign_drain_with_no_successor_still_launches_after_the_bounded_wait(server, project, monkeypatch):
    token_matches(project)
    monkeypatch.setattr(tb, "SUCCESSOR_WAIT", 0.5)
    fake = server()
    host = Host(project, fake)
    host.up(state=ls.STATE_DRAINING)
    threading.Timer(0.2, host.down).start()
    seen = []

    def launch(root, plugin_dir, log=None):
        seen.append(1)
        host.up()
        return {"status": "ready", "reason": ""}
    assert tb.ensure_server(project, BUILD, "disc", launch=launch).port == fake.port and seen


def test_the_retiring_bridge_retires_an_older_build_that_won_the_relaunch_and_is_bounded(server, project):
    token_matches(project)
    old1, old2, new = server(build=OLDER), server(build=OLDER), server(build=BUILD)
    host = Host(project, old1)
    host.up()
    old1.on_retire = old2.on_retire = host.down
    queue_ = [old2, new]
    launches = []

    def launch(root, plugin_dir, log=None):
        launches.append(1)
        host.fake = queue_.pop(0)
        host.up()
        return {"status": "ready", "reason": ""}
    stream = io.StringIO()
    assert tb.ensure_server(project, BUILD, "disc", launch=launch, log=tb.Log(stream=stream)).port == new.port
    assert old1.retired.is_set() and old2.retired.is_set() and len(launches) == 2


def test_older_build_that_keeps_winning_is_used_after_the_bound_with_a_log_line(server, project):
    token_matches(project)
    olds = [server(build=OLDER) for _ in range(5)]
    host = Host(project, olds[0])
    host.up()
    for item in olds:
        item.on_retire = host.down
    queue_ = list(olds[1:])

    def launch(root, plugin_dir, log=None):
        host.fake = queue_.pop(0)
        host.up()
        return {"status": "ready", "reason": ""}
    stream = io.StringIO()
    endpoint = tb.ensure_server(project, BUILD, "disc", launch=launch, log=tb.Log(stream=stream))
    assert endpoint.port in {item.port for item in olds}
    assert "older taskmaster build is serving" in stream.getvalue()


# -- I1: any error on the way to the local server falls back to the stdio server ------------------------------------

def test_junction_at_serve_dir_falls_back_instead_of_crashing(project, tmp_path, monkeypatch):
    monkeypatch.delenv("TASKMASTER_LOCAL_SERVER", raising=False)
    monkeypatch.setattr(ls, "legacy_store", lambda root: False)
    monkeypatch.setattr(ls, "server_mode_reason", lambda root: None)
    monkeypatch.setattr(tb.Path, "cwd", classmethod(lambda cls: project))
    monkeypatch.setenv("TASKMASTER_ROOT", str(project))
    serve = project / ".taskmaster" / "local" / "serve"
    if serve.exists():
        for child in serve.iterdir():
            child.unlink()
        serve.rmdir()
    _junction(serve, tmp_path / "junction-target")
    seen = []
    monkeypatch.setattr(tb, "run_fallback", lambda reason, **kw: seen.append(reason) or 0)
    try:
        assert tb.bridge_main() == 0
    finally:
        _unlink_junction(serve)
    assert seen and "local server" in seen[0]


def test_unexpected_exception_while_starting_falls_back(monkeypatch):
    monkeypatch.delenv("TASKMASTER_LOCAL_SERVER", raising=False)
    monkeypatch.setattr(tb, "_start_local_bridge", lambda log: (_ for _ in ()).throw(ValueError("bad url")))
    seen = []
    monkeypatch.setattr(tb, "run_fallback", lambda reason, **kw: seen.append(reason) or 0)
    assert tb.bridge_main() == 0 and "bad url" in seen[0]


def test_env_opt_out_wins_even_when_localserve_cannot_be_imported(monkeypatch):
    monkeypatch.setenv("TASKMASTER_LOCAL_SERVER", "0")
    monkeypatch.setattr(tb, "_ls", lambda: (_ for _ in ()).throw(ImportError("broken")))
    seen = []
    monkeypatch.setattr(tb, "run_fallback", lambda reason, **kw: seen.append(reason) or 0)
    assert tb.bridge_main() == 0 and seen == ["TASKMASTER_LOCAL_SERVER=0"]
