# User intent: the team server's /read/v1 endpoint sits behind every auth and limit layer, answers each view from
# the same code the solo hooks run, picks a branch's task per caller, and gives a key exactly what its MCP read
# tools give it, minus the host's private tables (team-server-007 D1-D7, ruling I6).
from __future__ import annotations

import io
import json
import sys
import threading
import time
from contextlib import closing
from types import SimpleNamespace

import httpx
import pytest

from taskmaster.untrusted import unframe
import yaml

from native_twins import hand_edit_task, make_twins
from serve_helpers import call_tool
from taskmaster import backlog_server as bs
from taskmaster.coordinator import client as client_module
from taskmaster.coordinator import identity_gate, protocol
from taskmaster.native.commands import Transaction
from taskmaster.native_routing import hook_reads
from taskmaster.serve import read_api
from taskmaster.serve.config import ServeConfig, ServeRefused, validate
from taskmaster.serve.daemon import ServeDaemon
from taskmaster.serve.principals import add_user

MANIFEST = {"schema_version": 1, "meta": {"name": "Reads", "slug": "reads", "kind": "app"},
            "conventions": {"policies": {"review_gate_required_for_merge": True,
                                         "merge_targets": [{"label": "qa", "branches": ["qa-line"]}]}}}


def _seed():
    bs.backlog_project_set(yaml_content=yaml.safe_dump(MANIFEST))
    for title, branch, owner in (("Ann's work", "shared", "ann"), ("Bob's work", "shared", "bob"),
                                 ("Bob's solo", "solo-b", "bob")):
        bs.backlog_add_task(title=title, epic="test-epic", phase="dev")
    bs.backlog_add_task(title="Anchored", epic="test-epic", phase="dev", options={"anchors": "api/model.py"})
    for ident, branch, owner in (("test-epic-001", "shared", "ann"), ("test-epic-002", "shared", "bob"),
                                 ("test-epic-003", "solo-b", "bob")):
        bs.backlog_update_task(task_id=ident, field="branch", value=branch)
        hand_edit_task(ident, lambda doc, owner=owner: doc.__setitem__("owner", owner))


@pytest.fixture
def root(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, _seed, visibility=None)
    with twins.at(twins.native):
        yield twins.native


@pytest.fixture
def serve(root, monkeypatch):
    monkeypatch.setattr(client_module, "AUTOSTART_ALLOWED", True)
    monkeypatch.setenv("TASKMASTER_SERVICE_IDLE_SECONDS", "0.5")
    monkeypatch.setattr(identity_gate, "team_mode_enabled", lambda root_, *, project_doc=None: True)
    started = []

    def start(**config):
        keys = {}
        for handle in ("ann", "bob", "carol"):
            key, _record, _ = add_user(root, handle, name=f"{handle.title()} Person", emails=[f"{handle}@example.com"])
            keys[handle] = key
        daemon = ServeDaemon(ServeConfig(root=root, port=0, **config), stdout=io.StringIO()).start()
        codes = []
        thread = threading.Thread(target=lambda: codes.append(daemon.serve()), daemon=True)
        thread.start()
        assert daemon.ready.wait(30)
        base = daemon.url.rsplit("/", 1)[0]
        world = SimpleNamespace(daemon=daemon, url=daemon.url, base=base, root=root, keys=keys, thread=thread,
                                operator=(root / ".taskmaster/local/serve/operator.token").read_text().strip())
        started.append(world)
        return world
    yield start
    for world in started:
        world.daemon.request_stop("test teardown")
        world.thread.join(30)
        assert not world.thread.is_alive()


def get(world, view, params=None, who="ann", **kwargs):
    token = world.operator if who == "operator" else world.keys[who] if who else None
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    headers.update(kwargs.pop("headers", {}))
    return httpx.get(f"{world.base}/read/v1/{view}", params=params or {}, headers=headers, timeout=30, **kwargs)


def tools(world, calls, who="ann"):
    return httpx.post(f"{world.base}/read/v1/tools", json={"calls": calls},
                      headers={"Authorization": f"Bearer {world.keys[who]}"}, timeout=60)


def local(root_, fn, *args):
    with closing(protocol.connect(root_, readonly=True)) as connection:
        connection.execute("BEGIN")
        return fn(connection, *args)


# ── auth and layers ─────────────────────────────────────────────


def test_every_route_needs_a_key(serve):
    world = serve()
    for view in ("meta", "paths", "merge-gate", "merge-target"):
        assert httpx.get(f"{world.base}/read/v1/{view}", timeout=30).status_code == 401
    assert httpx.post(f"{world.base}/read/v1/tools", content=b'{"calls": []}', timeout=30).status_code == 401
    assert httpx.get(f"{world.base}/read/v2/meta", timeout=30).status_code == 401


def test_the_operator_reads_on_the_host_only(serve):
    world = serve()
    assert get(world, "meta", who="operator").status_code == 200
    assert get(world, "meta", who="operator", headers={"X-Forwarded-For": "203.0.113.9"}).status_code == 403


def test_a_wrong_host_is_refused(serve):
    world = serve()
    assert get(world, "meta", headers={"Host": "evil.example"}).status_code == 421


def test_the_principal_rate_limit_applies(serve):
    world = serve(rate_burst=2, rate_per_minute=1)
    assert [get(world, "meta").status_code for _ in range(3)] == [200, 200, 429]
    assert get(world, "meta", who="bob").status_code == 200


def test_the_in_flight_cap_applies(serve, monkeypatch):
    world = serve(max_inflight=1, rate_burst=50)
    inner = sys.modules["taskmaster.backlog_server"].mcp._inner

    def t007_slow() -> str:
        time.sleep(1.5)
        return "slow"
    inner.tool(t007_slow, name="t007_slow")
    monkeypatch.setattr(read_api, "READ_TOOLS", read_api.READ_TOOLS + ("t007_slow",))
    try:
        holder = threading.Thread(target=lambda: tools(world, [{"tool": "t007_slow", "args": {}}]))
        holder.start()
        time.sleep(0.5)
        assert get(world, "meta").status_code == 429
        holder.join(30)
    finally:
        inner.local_provider.remove_tool("t007_slow")


# ── envelope and views ──────────────────────────────────────────


def test_meta_and_the_envelope(serve):
    world = serve()
    reply = get(world, "meta")
    assert reply.status_code == 200 and reply.headers["cache-control"] == "no-store" and reply.headers["x-request-id"]
    body = reply.json()
    assert body["v"] == 1 and body["view"] == "meta" and isinstance(body["revision"], int)
    assert body["store_id"] and body["generated_at"].endswith("Z")
    assert body["data"]["supported"] == [1] and set(body["data"]["tools"]) == set(read_api.READ_TOOLS)


def test_paths_equals_the_hook_view(serve, root):
    world = serve()
    for rel in ("api/model.py", "web/none.py"):
        assert get(world, "paths", {"path": rel}).json()["data"] == local(root, hook_reads.paths_view, rel)


def test_merge_gate_picks_the_callers_task(serve):
    world = serve()

    def gate(who, branch="shared"):
        return get(world, "merge-gate", {"branch": branch}, who=who).json()["data"]
    ann, bob, carol = gate("ann"), gate("bob"), gate("carol")
    assert ann["task"]["id"] == "test-epic-001" and ann["task"]["mine"] is True and not ann["ambiguous"]
    assert bob["task"]["id"] == "test-epic-002" and bob["task"]["mine"] is True
    assert carol["ambiguous"] is True and carol["task"] is None
    assert carol["candidates"] == ["test-epic-001", "test-epic-002"]
    solo = gate("ann", "solo-b")
    assert solo["task"]["id"] == "test-epic-003" and solo["task"]["mine"] is False
    assert gate("ann", "untracked")["task"] is None
    assert ann["policy_required"] is True and ann["task"]["review_gate"] is None


def test_merge_target_picks_the_callers_task_and_resolves_the_rung(serve):
    world = serve()
    data = get(world, "merge-target", {"branch": "shared", "target": "qa-line"}, who="bob").json()["data"]
    assert data == {"ambiguous": False, "candidates": ["test-epic-001", "test-epic-002"],
                    "task_id": "test-epic-002", "rung": "qa"}
    other = get(world, "merge-target", {"branch": "solo-b", "target": "master"}).json()["data"]
    assert other["task_id"] == "test-epic-003" and other["rung"] == "branch:master"


@pytest.mark.parametrize("view, params", [
    ("paths", {}), ("paths", {"path": "../etc/passwd"}), ("paths", {"path": "/abs"}), ("paths", {"path": "C:/x"}),
    ("paths", {"path": "a\\b"}), ("merge-gate", {}), ("merge-gate", {"branch": "-x"}),
    ("merge-gate", {"branch": "a..b"}), ("merge-target", {"branch": "ok"}),
])
def test_bad_parameters_are_refused_without_echo(serve, view, params):
    world = serve()
    reply = get(world, view, params)
    assert reply.status_code == 400 and reply.json()["error"]["code"] == "bad-request"
    for value in params.values():
        if value:
            assert value not in reply.text


def test_unknown_views_and_versions(serve):
    world = serve()
    reply = get(world, "nothing")
    assert reply.status_code == 404 and reply.json()["error"]["code"] == "unknown-view"
    reply = httpx.get(f"{world.base}/read/v2/meta", headers={"Authorization": f"Bearer {world.keys['ann']}"},
                      timeout=30)
    assert reply.status_code == 404 and reply.json()["error"]["code"] == "unsupported-version"
    assert reply.json()["error"]["supported"] == [1]


def test_a_get_with_a_body_is_refused(serve):
    world = serve()
    reply = httpx.request("GET", f"{world.base}/read/v1/meta", content=b"x" * 10,
                          headers={"Authorization": f"Bearer {world.keys['ann']}"}, timeout=30)
    assert reply.status_code == 400


def test_a_path_under_read_is_refused():
    with pytest.raises(ServeRefused):
        validate(ServeConfig(root=".", path="/read/x"))
    with pytest.raises(ServeRefused):
        validate(ServeConfig(root=".", path="/read"))


# ── tools ───────────────────────────────────────────────────────


PARITY = [
    ("backlog_list_tasks", {"status": "todo", "limit": 50}),
    ("backlog_issue_list", {"status": "open"}),
    ("backlog_continuity_items", {"action_class": "review", "limit": 50}),
    ("backlog_handover_list", {"format": "json", "status": "open"}),
    ("backlog_handover_get", {"handover_id": "no-such-handover"}),
    ("backlog_get_task", {"task_id": "test-epic-001"}),
    ("backlog_get_task", {"task_id": "no-such-task"}),
    ("backlog_task_pipeline", {"task_id": "test-epic-001"}),
    ("backlog_query", {"sql": "SELECT id, status FROM entities WHERE kind='task' ORDER BY id"}),
    ("backlog_query", {"sql": "SELECT nope FROM entities"}),
]


def test_every_read_tool_answers_as_it_does_over_mcp(serve):
    world = serve()
    reply = tools(world, [{"tool": t, "args": a} for t, a in PARITY])
    assert reply.status_code == 200, reply.text
    results = reply.json()["data"]["results"]
    assert [r["tool"] for r in results] == [t for t, _ in PARITY]
    for (tool, args), result in zip(PARITY, results):
        assert unframe(result["text"]) == call_tool(world.url, world.keys["ann"], tool, args), (tool, args)
    assert {t for t, _ in PARITY} == set(read_api.READ_TOOLS)


@pytest.mark.parametrize("table", ["sessions", "linear_queue", "projection"])
def test_host_private_tables_are_refused_here_and_to_members_over_mcp(serve, table):
    world = serve()
    sql = f"SELECT * FROM {table}"
    text = unframe(tools(world, [{"tool": "backlog_query", "args": {"sql": sql}}]).json()["data"]["results"][0]["text"])
    assert text.startswith("Error: not authorized") and table in text
    # team-server-026: a member's MCP query is held to the same tables; the operator's is not.
    member = call_tool(world.url, world.keys["ann"], "backlog_query", {"sql": sql})
    assert "not authorized" in member and table in member
    assert "not authorized" not in call_tool(world.url, world.operator, "backlog_query", {"sql": sql})
    allowed = call_tool(world.url, world.keys["ann"], "backlog_query", {"sql": "SELECT id FROM tasks LIMIT 1"})
    assert "not authorized" not in allowed
    hidden = tools(world, [{"tool": "backlog_query", "args": {"sql": f"WITH x AS (SELECT * FROM main.{table}) "
                                                                       "SELECT * FROM x"}}])
    assert "not authorized" in unframe(hidden.json()["data"]["results"][0]["text"])


def test_a_docs_path_outside_the_tracked_set_is_refused_through_tools(serve, root):
    (root / ".env").write_text("ENV-SECRET-MARKER\n", encoding="utf-8")
    world = serve()
    updated = call_tool(world.url, world.keys["ann"], "backlog_update_task",
                        {"task_id": "test-epic-004", "field": "docs", "value": "spec:.env"})
    assert "Updated" in updated, updated
    args = {"task_id": "test-epic-004", "sections": ["spec"]}
    text = unframe(tools(world, [{"tool": "backlog_get_task", "args": args}]).json()["data"]["results"][0]["text"])
    assert "ENV-SECRET-MARKER" not in text
    assert text == call_tool(world.url, world.keys["ann"], "backlog_get_task", args)


def test_a_tool_outside_the_list_never_runs(serve):
    world = serve()
    reply = tools(world, [{"tool": "backlog_add_task", "args": {"title": "x", "epic": "test-epic", "phase": "dev"}}])
    assert reply.status_code == 400 and "not readable here" in reply.json()["error"]["message"]
    assert "x" not in json.dumps([t for t in bs.backlog_list_tasks(limit=50).splitlines() if "| x |" in t])


@pytest.mark.parametrize("body, status", [(b"not json", 400), (b'{"calls": []}', 400),
                                          (json.dumps({"calls": [{"tool": "backlog_query"}] * 17}).encode(), 400),
                                          (b"x" * (64 * 1024 + 1), 413)], ids=["not-json", "empty", "17-calls", "oversize"])
def test_bad_batches(serve, body, status):
    world = serve()
    reply = httpx.post(f"{world.base}/read/v1/tools", content=body, timeout=30,
                       headers={"Authorization": f"Bearer {world.keys['ann']}", "Content-Type": "application/json"})
    assert reply.status_code == status


def test_a_batch_costs_a_token_per_call(serve):
    world = serve(rate_burst=6, rate_per_minute=1)
    call = {"tool": "backlog_query", "args": {"sql": "SELECT 1"}}
    assert tools(world, [call] * 3).status_code == 200            # 3 of 6
    assert tools(world, [call] * 5).status_code == 429            # 3 left: the request token, then 4 short
    assert tools(world, [call] * 2).status_code == 200            # 2 left: 1 for the request, 1 more


def test_a_batch_larger_than_the_burst_is_a_bad_request(serve):
    world = serve(rate_burst=4)
    reply = tools(world, [{"tool": "backlog_query", "args": {"sql": "SELECT 1"}}] * 5)
    assert reply.status_code == 400 and "exceeds the rate burst 4" in reply.json()["error"]["message"]


@pytest.fixture
def stub_tools(monkeypatch):
    inner = sys.modules["taskmaster.backlog_server"].mcp._inner
    added = []

    def add(name, fn):
        inner.tool(fn, name=name)
        added.append(name)
        monkeypatch.setattr(read_api, "READ_TOOLS", read_api.READ_TOOLS + (name,))
    yield add
    for name in added:
        inner.local_provider.remove_tool(name)


def test_the_batch_time_budget(serve, stub_tools, monkeypatch):
    world = serve()
    stub_tools("t007_slow", lambda: time.sleep(0.4) or "slow")
    monkeypatch.setattr(read_api, "BATCH_BUDGET_SECONDS", 0.2)
    results = tools(world, [{"tool": "t007_slow", "args": {}}] * 3).json()["data"]["results"]
    assert results[0]["text"] == "slow"
    assert [r["text"] for r in results[1:]] == [read_api.NOT_RUN_BUDGET] * 2


def test_the_reply_size_budget(serve, stub_tools, monkeypatch):
    world = serve()
    stub_tools("t007_big", lambda: "x" * 1000)
    monkeypatch.setattr(read_api, "BATCH_TEXT_CAP", 1500)
    results = tools(world, [{"tool": "t007_big", "args": {}}] * 3).json()["data"]["results"]
    assert results[0]["text"] == "x" * 1000
    assert [r["text"] for r in results[1:]] == [read_api.NOT_RUN_SIZE] * 2


def test_the_revision_is_read_before_the_batch(serve, stub_tools):
    world = serve()
    stub_tools("t007_write", lambda: bs.backlog_add_task(title="Mid-batch", epic="test-epic", phase="dev"))
    before = get(world, "meta").json()["revision"]
    reply = tools(world, [{"tool": "t007_write", "args": {}}]).json()
    assert "[seq" in reply["data"]["results"][0]["text"]
    assert reply["revision"] == before < get(world, "meta").json()["revision"]


def test_no_read_tool_sends_a_coordinator_message(serve, monkeypatch):
    world = serve()
    sent = []
    real = client_module.Client.call

    def spy(self, method, *args, **kwargs):
        sent.append(method)
        return real(self, method, *args, **kwargs)
    monkeypatch.setattr(client_module.Client, "call", spy)
    reply = tools(world, [{"tool": t, "args": a} for t, a in PARITY])
    assert reply.status_code == 200
    assert not [m for m in sent if m not in ("status",)], sent


def test_read_tools_run_as_the_callers_read_channel(serve, stub_tools):
    world = serve()
    stub_tools("t007_session", lambda: bs.current_session())
    text = tools(world, [{"tool": "t007_session", "args": {}}], who="bob").json()["data"]["results"][0]["text"]
    assert text == "bob@read"


# ── review fixes (I1, M2, M5) ───────────────────────────────────


def test_a_pathological_stored_glob_cannot_stall_the_server(serve):
    """Review I1: a member's anchor `*a*a*a*a*a*a*b` against a long path held the GIL for seconds with the regex."""
    world = serve()
    added = call_tool(world.url, world.keys["ann"], "backlog_add_task",
                      {"title": "Glob bomb", "epic": "test-epic", "phase": "dev", "options": {"anchors": "*a*a*a*a*a*a*b"}})
    assert "[seq " in added, added
    # The reviewer's probe (ts007-gate/probe/test_glob_probe.py): ann's paths read with bob's meta sent meanwhile.
    # With the regex, 70 characters held both for about 4 s; here the path is the longest allowed.
    result = {}

    def slow():
        started = time.monotonic()
        reply = get(world, "paths", {"path": "a" * 1024})
        result["paths"] = (reply.status_code, reply.json()["data"]["listed"], time.monotonic() - started)
    thread = threading.Thread(target=slow)
    thread.start()
    time.sleep(0.3)
    started = time.monotonic()
    meta = get(world, "meta", who="bob")
    meta_seconds = time.monotonic() - started
    thread.join(60)
    assert not thread.is_alive()
    status, listed, paths_seconds = result["paths"]
    assert status == 200 and listed == [] and paths_seconds < 3.0
    assert meta.status_code == 200 and meta_seconds < 2.0


def test_a_view_past_its_deadline_answers_503_timeout(serve, monkeypatch):
    world = serve()
    monkeypatch.setattr(read_api, "VIEW_TIMEOUT_S", 0.0)
    reply = get(world, "paths", {"path": "api/model.py"})
    assert reply.status_code == 503 and reply.json()["error"]["code"] == "timeout"
    monkeypatch.undo()
    assert get(world, "paths", {"path": "api/model.py"}).status_code == 200


@pytest.mark.parametrize("length", ["abc", "-1", "1e3", " 5"])
def test_a_non_numeric_content_length_on_a_get_is_a_bad_request(length):
    """Review M2: was a ValueError, answered 500. (httpx will not send such a header, so the check is called directly.)"""
    with pytest.raises(read_api.Refused) as refused:
        read_api.ReadApi._no_body(SimpleNamespace(headers={"content-length": length}))
    assert refused.value.status == 400

def test_a_known_view_with_the_wrong_method_is_405(serve):
    world = serve()
    auth_header = {"Authorization": f"Bearer {world.keys['ann']}"}
    reply = httpx.get(f"{world.base}/read/v1/tools", headers=auth_header, timeout=30)
    assert reply.status_code == 405 and reply.headers["allow"] == "POST"
    assert reply.json()["error"]["code"] == "method-not-allowed"
    reply = httpx.post(f"{world.base}/read/v1/meta", headers=auth_header, content=b"", timeout=30)
    assert reply.status_code == 405 and reply.headers["allow"] == "GET"

# ── review fixes, round 2 ───────────────────────────────────────


def test_a_huge_star_anchor_cannot_outrun_the_view(serve):
    """Round-2 I1: 240,000 `*` as a member's anchor took 11.7 s on a 255-char path, past the 5 s deadline."""
    world = serve()
    star_bomb = {"title": "Star bomb", "epic": "test-epic", "phase": "dev", "options": {"anchors": "*" * 240_000}}
    # team-server-023: the normal write path now refuses an over-long anchor ...
    refused = call_tool(world.url, world.keys["ann"], "backlog_add_task", star_bomb)
    assert "longest an anchor can be" in refused, refused
    # ... but stores written before 023 still carry them, so seed one the way old data exists (the write-time check
    # off for this call only) and keep proving the read side is bounded.
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(Transaction, "_check_anchor_lengths", staticmethod(lambda before, after: None))
        added = call_tool(world.url, world.keys["ann"], "backlog_add_task", star_bomb)
    assert "[seq " in added, added
    started = time.monotonic()
    reply = get(world, "paths", {"path": "a" * 255})
    assert time.monotonic() - started < 3.0
    assert reply.status_code in (200, 503)
    if reply.status_code == 200:
        assert reply.json()["data"]["listed"] == []


def test_a_held_write_lock_cannot_outrun_the_view_deadline(serve, root, monkeypatch):
    """Round-2 I2: the read connection waited up to 30 s on SQLite's busy timeout, past the 5 s view deadline."""
    world = serve()
    monkeypatch.setattr(read_api, "VIEW_TIMEOUT_S", 0.5)
    import sqlite3
    path = root / ".taskmaster/local/store.db"
    blocker = sqlite3.connect(str(path), isolation_level=None, timeout=1)
    try:
        blocker.execute("PRAGMA journal_mode=DELETE")     # in WAL mode readers never wait; a rollback journal makes them
        blocker.execute("BEGIN EXCLUSIVE")
        started = time.monotonic()
        reply = get(world, "paths", {"path": "api/model.py"})
        elapsed = time.monotonic() - started
    finally:
        blocker.execute("ROLLBACK")
        blocker.close()
    assert elapsed < 3.0, elapsed
    assert reply.status_code == 503 and reply.json()["error"]["code"] in ("timeout", "store-unavailable")


@pytest.mark.parametrize("table", ["sessions", "linear_queue", "projection"])
@pytest.mark.parametrize("sql", [
    "SELECT count(*) FROM {t} WINDOW {t} AS ()",
    "SELECT count(*) FROM {t} WINDOW {t} AS () LIMIT 1",
    "WITH {t} AS (SELECT 1) SELECT count(*) FROM main.{t}",
])
def test_declared_names_cannot_unlock_host_private_tables(serve, table, sql):
    """Round-2 I3: a window name equal to a restricted table let the schema-less read through (real row counts)."""
    world = serve()
    text = unframe(tools(world, [{"tool": "backlog_query", "args": {"sql": sql.format(t=table)}}]).json()["data"]["results"][0]["text"])
    assert text.startswith("Error: not authorized"), text
    over_mcp = call_tool(world.url, world.keys["ann"], "backlog_query", {"sql": sql.format(t=table)})
    assert over_mcp.startswith("Error: not authorized"), over_mcp  # team-server-026: members too, over MCP


# ── team-server-027: framing on the read endpoint (C4) and prose-free views (C5) ──

MARKER = "TM-INJECT-7f3a"


def _ann_plants(world):
    """Ann, over MCP, puts the marker into a task's notes, tldr and next step."""
    for field in ("notes", "tldr", "next_step"):
        call_tool(world.url, world.keys["ann"], "backlog_update_task",
                  {"task_id": "test-epic-001", "field": field, "value": f"{MARKER} {field}"})


def test_027_bobs_read_batch_frames_anns_marker(serve):
    from taskmaster import untrusted
    world = serve()
    _ann_plants(world)
    calls = [{"tool": t, "args": a} for t, a in PARITY if "no-such" not in json.dumps(a)]
    reply = tools(world, calls, who="bob")
    assert reply.status_code == 200, reply.text
    seen = 0
    for result in reply.json()["data"]["results"]:
        if MARKER in result["text"]:
            seen += 1
            assert untrusted.audit(result["text"], MARKER, "ann") == [], result["tool"]
    assert seen >= 2


def test_027_batch_size_cap_counts_the_framed_text(serve, monkeypatch):
    world = serve()
    _ann_plants(world)
    args = {"task_id": "test-epic-001"}
    plain = len(unframe(tools(world, [{"tool": "backlog_get_task", "args": args}], who="bob")
                        .json()["data"]["results"][0]["text"]))
    framed = len(tools(world, [{"tool": "backlog_get_task", "args": args}], who="bob")
                 .json()["data"]["results"][0]["text"])
    assert framed > plain + 100
    monkeypatch.setattr(read_api, "BATCH_TEXT_CAP", plain + 50)
    result = tools(world, [{"tool": "backlog_get_task", "args": args}], who="bob").json()["data"]["results"][0]
    assert result["text"] == read_api.NOT_RUN_SIZE


# Every string leaf of a view sits under one of these keys, each a validated or enum value (027 D8, design 6).
VIEW_STRING_KEYS = {
    "path": "the caller's own path", "id": "entity id", "kind": "entity kind enum", "status": "status enum",
    "candidates": "task ids", "task_id": "task id", "verdict": "verdict enum",
    "commit_sha": "hex only (D8)", "rung": "branch-like ladder label (D8) or branch:<target>",
    "merge_gate_freshness": "freshness enum", "branch": "the branch as the agent typed it",
    "view": "view name", "store_id": "store id", "generated_at": "timestamp", "build": "build string",
    "unavailable": "fixed text", "reason": "fixed text",
}


def _leaves(value, key=None):
    if isinstance(value, dict):
        for k, v in value.items():
            yield from _leaves(v, k)
    elif isinstance(value, list):
        for v in value:
            yield from _leaves(v, key)
    elif isinstance(value, str):
        yield key, value


def test_027_views_never_carry_the_marker_and_use_allowlisted_keys(serve):
    world = serve()
    _ann_plants(world)
    params = {"meta": {}, "paths": {"path": "api/model.py"}, "merge-gate": {"branch": "shared"},
              "merge-target": {"branch": "shared", "target": "qa-line"}}
    covered = set()
    for view in read_api.VIEWS:
        if view == "tools":
            continue
        reply = get(world, view, params.get(view, {}), who="bob")
        covered.add(view)
        assert MARKER not in reply.text, view
        for key, text in _leaves(reply.json()):
            assert key in VIEW_STRING_KEYS or key in {"tools", "views", "supported"} or key is None, (view, key, text)
    assert covered == set(read_api.VIEWS) - {"tools"}

@pytest.mark.parametrize("view", ["merge-gate", "merge-target"])
def test_a_malformed_clone_parameter_is_400_and_a_good_one_is_accepted(serve, view):
    world = serve()
    params = {"branch": "shared", "target": "qa-line"}
    assert get(world, view, {**params, "clone": "c-xyz"}).status_code == 400
    assert get(world, view, {**params, "clone": "C-0123456789abcdef"}).status_code == 400
    assert get(world, view, {**params, "clone": "c-0123456789abcdef"}).status_code == 200


def test_027_stopping_the_daemon_removes_its_framing_middleware(serve):
    """The framer was never removed on stop: every start in one process stacked another one, and the next start's
    list_tools hit RecursionError (Linux serve tier, one process). The server's middleware list returns to its start."""
    from taskmaster.serve.framing import FrameUntrusted
    world = serve()
    inner = world.daemon._bs.mcp._inner
    assert sum(isinstance(m, FrameUntrusted) for m in inner.middleware) == 1
    world.daemon.request_stop("test")
    world.thread.join(30)
    assert not any(isinstance(m, FrameUntrusted) for m in inner.middleware)
