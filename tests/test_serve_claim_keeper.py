# User intent: over real HTTP, a teammate's claims are kept while their connection is open, cut to 15 minutes when it
# closes, kept through a graceful stop, and given one grace TTL by a restarted server (team-server-010).
from __future__ import annotations

from datetime import timedelta
import json
import time

import pytest

from native_twins import committed
from serve_helpers import open_connection
from taskmaster.native import claims
from taskmaster.serve import claim_keeper
from taskmaster.serve.sessions import SessionRegistry
from test_native_service_build import root  # noqa: F401
from test_serve_principals_e2e import launch  # noqa: F401
from test_serve_sessions_e2e import add, poll


@pytest.fixture
def web(launch, monkeypatch):  # noqa: F811
    monkeypatch.setattr(claim_keeper, "KEEP_SECONDS", 0.2)
    w = launch()
    opened = []

    def connect(who):
        connection = open_connection(w.url, w.keys[who].key)
        opened.append(connection)
        return connection

    w.connect = connect
    yield w
    for connection in opened:
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass


def session_of(connection):
    return json.loads(connection.call("backlog_claim", {"action": "status"}))["session"]


def stamp(root_, ident):
    return claims.parse_stamp(committed(root_)[("task", ident)][0].get("claim_expires"))


def now():
    return claims.now_utc()


def picked(web):
    ann = web.connect("ann")
    task = add(ann, "Kept")
    assert "Picked" in ann.call("backlog_pick_task", {"task_id": task})
    return ann, task, session_of(ann)


def test_a_connected_claim_keeps_its_stamp(web):
    _ann, task, _session = picked(web)
    first = stamp(web.root, task)
    time.sleep(1.0)                                  # several keeper passes
    assert stamp(web.root, task) == first


def test_closing_the_connection_cuts_the_claim_to_the_short_ttl(web):
    ann, task, _session = picked(web)
    ann.close()
    goal = claims.DISCONNECTED_TTL_SECONDS
    assert poll(lambda: abs((stamp(web.root, task) - now()).total_seconds() - goal) < 10, deadline=8.0)


def test_a_connected_claim_below_the_renew_line_is_renewed(web, monkeypatch):
    monkeypatch.setattr(claims, "RENEW_BELOW_SECONDS", claims.DEFAULT_TTL_SECONDS + 3600)
    _ann, task, _session = picked(web)
    first = stamp(web.root, task)
    assert poll(lambda: stamp(web.root, task) > first, deadline=8.0)


def test_a_graceful_stop_does_not_shorten_live_claims_and_leaves_a_heartbeat(web):
    _ann, task, session = picked(web)
    first = stamp(web.root, task)
    beat = claim_keeper.heartbeat_path(web.root)

    def lists_session():
        try:
            return session in json.loads(beat.read_text(encoding="utf-8"))["connected"]
        except (OSError, ValueError, KeyError):
            return False

    assert poll(lists_session, deadline=8.0)
    web.daemon.request_stop("test")
    web.thread.join(30)
    assert not web.thread.is_alive()
    assert stamp(web.root, task) == first
    assert lists_session()


def test_a_restarted_server_grants_the_grace_before_serving(launch, monkeypatch):  # noqa: F811
    monkeypatch.setattr(claim_keeper, "KEEP_SECONDS", 0.2)
    first = launch()
    ann = open_connection(first.url, first.keys["ann"].key)
    task = add(ann, "Survivor")
    assert "Picked" in ann.call("backlog_pick_task", {"task_id": task})
    session = session_of(ann)
    beat = claim_keeper.heartbeat_path(first.root)
    assert poll(lambda: beat.exists() and session in json.loads(beat.read_text(encoding="utf-8"))["connected"], deadline=8.0)
    before = stamp(first.root, task)
    first.daemon.request_stop("test")
    first.thread.join(30)
    ann.close()
    second = launch(users=())                         # the keys already exist
    after = stamp(second.root, task)
    assert before - now() > timedelta(hours=3)
    assert abs((after - now()).total_seconds() - claims.DISCONNECTED_TTL_SECONDS) < 30


def test_sessions_are_attached_only_while_the_server_runs(web):
    assert SessionRegistry().attached() is False
    sessions = web.daemon.sessions
    assert sessions.attached() is True
    web.daemon.request_stop("test")
    web.thread.join(30)
    assert sessions.attached() is False


# ---- the adoption notice (Step 6) --------------------------------------------------------------------------------

import asyncio  # noqa: E402

import httpx  # noqa: E402

from taskmaster.serve import read_api  # noqa: E402


def old_session_with_claim(web):
    first = web.connect("ann")
    task = add(first, "Left behind")
    assert "Picked" in first.call("backlog_pick_task", {"task_id": task})
    old = session_of(first)
    first.close()
    return task, old


def test_a_new_connection_is_told_once_what_it_can_take_back(web):
    task, old = old_session_with_claim(web)
    second = web.connect("ann")
    blocks = second.call_all("backlog_claim", {"action": "status"})
    assert len(blocks) == 2
    assert json.loads(blocks[0])["ok"] is True                      # content[0] is the tool's own answer
    note = blocks[1]
    assert note.startswith("Note: your earlier session") and old in note and f"`{task}`" in note
    assert "backlog_pick_task" in note and "backlog_claim(action=\"renew\"" in note and "until" in note
    assert len(second.call_all("backlog_claim", {"action": "status"})) == 1


def test_every_open_claim_is_named(web):
    first = web.connect("ann")
    live, finished = add(first, "Live"), add(first, "Finished")
    for ident in (live, finished):
        assert "Picked" in first.call("backlog_pick_task", {"task_id": ident})
    first.close()
    notes = web.connect("ann").call_all("backlog_claim", {"action": "status"})
    assert len(notes) == 2 and f"`{live}`" in notes[1] and f"`{finished}`" in notes[1]


def test_nothing_adoptable_means_no_notice(web):
    assert len(web.connect("ann").call_all("backlog_claim", {"action": "status"})) == 1
    old_session_with_claim(web)
    assert len(web.connect("bob").call_all("backlog_claim", {"action": "status"})) == 1   # another person's claim


def test_the_read_endpoint_never_carries_it(web):
    old_session_with_claim(web)
    base = web.url.rsplit("/", 1)[0]
    tool = sorted(read_api.READ_TOOLS)[0]
    reply = httpx.post(f"{base}/read/v1/tools", json={"calls": [{"tool": tool, "args": {}}]},
                       headers={"Authorization": f"Bearer {web.keys['ann'].key}"}, timeout=60)
    assert "your earlier session" not in reply.text


def test_the_notice_reads_the_store_off_the_event_loop(web, monkeypatch):
    old_session_with_claim(web)
    real = claim_keeper.adoptable_notice
    seen = []

    def spy(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            seen.append("on-loop")
        except RuntimeError:
            seen.append("off-loop")
        return real(*args, **kwargs)

    monkeypatch.setattr(claim_keeper, "adoptable_notice", spy)
    web.connect("ann").call_all("backlog_claim", {"action": "status"})
    assert seen == ["off-loop"]
