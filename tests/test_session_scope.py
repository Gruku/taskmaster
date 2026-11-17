# User intent: every tool reads the session of the connection it serves, once per call, and per-session state (current task, bundle, agent) follows the connection; without a server nothing changes.
from __future__ import annotations

import itertools
import json
import re
from pathlib import Path
from types import SimpleNamespace

import pytest

from taskmaster import backlog_server as bs
from taskmaster.coordinator import client as client_module
from taskmaster.native import claims
from taskmaster.native.claims import Requester
from native_twins import committed
from test_batch_structured_commands import native_project  # noqa: F401

REPO = Path(__file__).resolve().parents[1]
A, B = "ann@" + "a" * 16, "bob@" + "b" * 16


def fake(session, agent="claude", handle=None, may_force_any=False):
    handle = handle or session.split("@")[0]
    return SimpleNamespace(session=session, agent=agent, task=None, bundle=None,
                           requester=lambda: Requester(handle, may_force_any))


@pytest.fixture
def envelopes(monkeypatch):
    """Every envelope a native call sends, by spying on the IPC client."""
    sent = []
    original = client_module.Client.execute

    def spy(self, envelope, *args, **kwargs):
        sent.append(envelope)
        return original(self, envelope, *args, **kwargs)

    monkeypatch.setattr(client_module.Client, "execute", spy)
    return sent


@pytest.fixture
def bound(monkeypatch):
    """Install a provider returning whatever `bound.current` is."""
    state = SimpleNamespace(current=None, calls=0)

    def provider():
        state.calls += 1
        return state.current

    monkeypatch.setattr(bs, "SESSION_PROVIDER", provider)
    monkeypatch.setattr(claims, "CONNECTED", lambda s: False)
    return state


def add(title, **extra):
    text = bs.backlog_add_task(title=title, epic="test-epic", phase="dev", tldr="t", **extra)
    assert "Error" not in text, text
    return re.search(r"`(test-epic-\d+)`", text).group(1)


def holder(root, ident):
    return committed(root)[("task", ident)][0].get("locked_by")


def test_without_a_provider_the_host_session_applies(monkeypatch):
    assert bs.SESSION_PROVIDER is None
    assert bs.current_session() == bs.SESSION_ID
    monkeypatch.setattr(bs, "SESSION_ID", "x-1-deadbeef")
    assert bs.current_session() == "x-1-deadbeef"


def test_route_native_derives_the_session_once(native_project, monkeypatch, envelopes):  # noqa: F811
    ident = add("Once")
    counter = itertools.count()
    made = []

    def provider():
        obj = fake(f"ann@{next(counter):016x}", agent="codex")
        made.append(obj)
        return obj

    monkeypatch.setattr(bs, "SESSION_PROVIDER", provider)
    envelopes.clear()
    text = bs.backlog_pick_task(ident)
    assert "Picked" in text, text
    assert len(made) == 1
    [obj] = made
    assert {e["caller_scope"] for e in envelopes} == {obj.session}
    picks = [e for e in envelopes if e["operation"] == "task.pick"]
    assert picks and all(e["arguments"]["session"] == obj.session for e in picks)
    assert all(e["actor"]["agent"] == "codex" for e in envelopes)
    assert obj.task and obj.task["id"] == ident
    assert bs._CALL_BOUND.get() is bs._UNBOUND
    assert holder(native_project, ident) == obj.session


def test_nested_calls_share_one_binding_and_raising_resets_it(monkeypatch):
    calls = []
    monkeypatch.setattr(bs, "SESSION_PROVIDER", lambda: calls.append(1) or fake(A))
    monkeypatch.setattr(bs, "_backlog_path", lambda: (_ for _ in ()).throw(RuntimeError("no project")))

    @bs._route_native
    def inner():
        return bs.current_session()

    @bs._route_native
    def outer():
        return bs.current_session(), inner(), bs.client_agent()

    assert outer() == (A, A, "claude") and len(calls) == 1

    @bs._route_native
    def boom():
        raise ValueError("x")

    with pytest.raises(ValueError):
        boom()
    assert bs._CALL_BOUND.get() is bs._UNBOUND


def test_every_routed_tool_uses_the_bound_session(native_project, bound, envelopes):  # noqa: F811
    bound.current = fake(A)
    first, second = add("One"), add("Two")
    envelopes.clear()
    assert "Picked" in bs.backlog_pick_task(first)
    assert holder(native_project, first) == A
    status = json.loads(bs.backlog_claim(action="status"))
    assert status["session"] == A and [c["task_id"] for c in status["claims"]] == [first]
    assert "Error" not in bs.backlog_next_available()
    bs.backlog_context(focus=first)
    batch = bs.backlog_batch_update(operations=f"pick {second}")
    assert "Error" not in batch, batch   # a batch `pick` line moves the status; it never writes a holder
    assert committed(native_project)[("task", second)][0]["status"] == "in-progress"
    assert "Dry-run" in bs.backlog_batch_preview(operations=f"pick {first}")
    assert envelopes and all(e["caller_scope"] == A for e in envelopes)
    assert all(bs.SESSION_ID not in json.dumps(e) for e in envelopes)


def test_per_session_state_follows_the_bound_session(native_project, bound, monkeypatch):  # noqa: F811
    # Earlier tests in the same process may leave the host's globals set; start them empty.
    monkeypatch.setattr(bs, "_session_task", None)
    monkeypatch.setattr(bs, "_session_bundle", None)
    one = add("Solo task")
    add("Pair one", bundle="pair")
    pair_two = add("Pair two", bundle="pair")
    a, b = fake(A), fake(B)
    bound.current = a
    assert "Picked" in bs.backlog_pick_task(one)
    bound.current = b
    assert "bundle" in bs.backlog_pick_task(pair_two)
    bound.current = a
    assert bs._get_session_task()["id"] == one and bs._get_session_bundle() is None
    bound.current = b
    assert bs._get_session_task()["id"] == pair_two and bs._get_session_bundle()["slug"] == "pair"
    bound.current = None
    assert bs._session_task is None and bs._session_bundle is None
    assert bs._get_session_task() is None

    def thread_of(text):
        found = re.search(r"`(HO-[\w-]+|handover-[\w-]+)`", text) or re.search(r"\b(HO-\d+)\b", text)
        return found

    bound.current = b
    created_b = bs.backlog_handover_create(tldr="b work", next_action="go on")
    bound.current = a
    created_a = bs.backlog_handover_create(tldr="a work", next_action="go on")
    assert "Error" not in created_a and "Error" not in created_b
    threads = [doc.get("thread") for (kind, _id), (doc, _body, _archived) in committed(native_project).items()
               if kind == "handover"]
    assert any("pair" in (t or "") for t in threads), threads
    assert sum("pair" in (t or "") for t in threads) == 1, threads


def test_client_agent_is_the_bound_sessions(bound, monkeypatch):
    monkeypatch.setattr(bs, "_CLIENT_AGENT", "claude")
    bound.current = fake(A, agent="codex")
    assert bs.client_agent() == "codex"
    bound.current = None
    assert bs.client_agent() == "claude"


def test_merge_stamp_replay_runs_as_the_host(native_project, monkeypatch, tmp_path):  # noqa: F811
    """Written after the `_route_native` conversion: a routed call under a bound session hands the
    replay the host session, and the replay thread runs in an empty context (D10)."""
    import threading
    from taskmaster.native_routing import merge_stamps, registry
    from taskmaster.serve import auth
    ident = add("Replay")
    seen, done = {}, threading.Event()

    def replay(backlog_dir, database, session, autostart=False):
        seen.update(session=session, provider_hits=None, viewer=auth.VIEWER_KEY_ID.get(),
                    bound=bs._CALL_BOUND.get() is bs._UNBOUND)
        done.set()

    real_exists = Path.exists
    monkeypatch.setattr(merge_stamps, "has_pending", lambda backlog_dir: True)
    monkeypatch.setattr(merge_stamps, "replay", replay)
    monkeypatch.setattr(registry.Path, "exists",
                        lambda self: True if self.name == "discovery.json" else real_exists(self))
    monkeypatch.setattr(bs, "SESSION_PROVIDER", lambda: fake(A))
    with auth.viewer_principal("a" * 16):
        bs.backlog_get_task(ident)
    assert done.wait(15)
    registry.wait_for_merge_stamp_replays(15)
    assert seen["session"] == bs.SESSION_ID
    assert seen["viewer"] is None and seen["bound"] is True


def test_viewer_session_endpoint_uses_the_accessors(bound):
    obj = fake(A)
    obj.task = {"id": "t-1", "title": "T", "epic": "e", "picked_at": "now"}
    bound.current = obj
    captured = {}
    handler = SimpleNamespace(_send_json=lambda status, payload, *a, **k: captured.update(payload))
    bs.ViewerHandler._serve_session(handler)
    assert captured["session_id"] == A and captured["id"] == "t-1"


def test_provider_errors_fail_the_call(native_project, monkeypatch, envelopes):  # noqa: F811
    ident = add("Refused")
    envelopes.clear()

    def provider():
        raise PermissionError("authenticated principal missing")

    monkeypatch.setattr(bs, "SESSION_PROVIDER", provider)
    with pytest.raises(PermissionError):
        bs.backlog_pick_task(ident)
    assert envelopes == []


def test_only_the_accessor_reads_the_global():
    allowed = {("taskmaster/backlog_server.py", "definition"), ("taskmaster/native_routing/registry.py", "replay"),
               ("taskmaster/native/claims.py", "comment")}
    hits = set()
    for path in (REPO / "taskmaster").rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        for line in path.read_text(encoding="utf-8").splitlines():
            if not re.search(r"\bSESSION_ID\b", line):
                continue
            stripped = line.strip()
            if rel == "taskmaster/backlog_server.py" and (stripped.startswith("SESSION_ID =")
                                                          or "else SESSION_ID" in stripped):
                hits.add((rel, "definition"))
            elif rel == "taskmaster/native_routing/registry.py" and "'SESSION_ID'" in stripped:
                hits.add((rel, "replay"))
            elif rel == "taskmaster/native/claims.py" and (stripped.startswith("#")
                                                           or "`backlog_server.SESSION_ID`" in stripped):
                hits.add((rel, "comment"))
            else:
                raise AssertionError(f"{rel}: reads SESSION_ID directly; use current_session() or call.session: {stripped}")
    assert hits <= allowed


# ── Step 6: adoption and refusal texts (design D6-D8) ──

OLD, OLD2, NEW = "ann@" + "1" * 16, "ann@" + "2" * 16, "ann@" + "3" * 16
OPAL = "opal@" + "c" * 16


@pytest.fixture
def connected(monkeypatch):
    """The set of sessions whose connection is open; everything else is gone."""
    open_ = set()
    monkeypatch.setattr(claims, "CONNECTED", lambda s: s in open_)
    return open_


def held_by(bound, session, *idents):
    bound.current = fake(session)
    for ident in idents:
        text = bs.backlog_pick_task(ident)
        assert "Error" not in text, text


def test_pick_adopts_a_gone_own_session(native_project, bound, connected):  # noqa: F811
    ident = add("Adopt")
    held_by(bound, OLD, ident)
    bound.current = fake(NEW)
    text = bs.backlog_pick_task(ident)
    assert "adopted from your earlier session" in text and OLD in text, text
    assert holder(native_project, ident) == NEW


def test_pick_adopts_an_in_progress_task(native_project, bound, connected):  # noqa: F811
    ident = add("In progress")
    held_by(bound, OLD, ident)
    assert committed(native_project)[("task", ident)][0]["status"] == "in-progress"
    bound.current = fake(NEW)
    text = bs.backlog_pick_task(ident)
    assert "Already in progress" not in text and "adopted from your earlier session" in text, text
    assert holder(native_project, ident) == NEW


def test_pick_refuses_an_open_own_session_without_force(native_project, bound, connected, envelopes):  # noqa: F811
    ident = add("Open")
    held_by(bound, OLD, ident)
    connected.add(OLD)
    bound.current = fake(NEW)
    envelopes.clear()
    text = bs.backlog_pick_task(ident)
    assert "your other open session" in text and OLD in text and "[seq" not in text, text
    assert not [e for e in envelopes if e["operation"] == "task.pick"]
    assert holder(native_project, ident) == OLD
    assert "Error" not in bs.backlog_pick_task(ident, force=True)
    assert holder(native_project, ident) == NEW


def test_bundle_pick_texts(native_project, bound, connected):  # noqa: F811
    first = add("Pair one", bundle="pair")
    add("Pair two", bundle="pair")
    held_by(bound, B, first)
    bound.current = fake(A)
    text = bs.backlog_pick_task(first, force=True)
    assert "Only an operator" in text and "Use force=True to steal." not in text, text

    trio = add("Trio one", bundle="trio")
    add("Trio two", bundle="trio")
    held_by(bound, OLD, trio)
    connected.add(OLD)
    bound.current = fake(NEW)
    text = bs.backlog_pick_task(trio)
    assert "your other open session" in text and "Use force=True to steal." not in text, text
    connected.discard(OLD)
    text = bs.backlog_pick_task(trio)
    assert "Picking bundle `trio`" in text, text
    assert holder(native_project, trio) == NEW


def test_member_force_on_a_teammate_is_refused_by_the_tool(native_project, bound, connected, envelopes):  # noqa: F811
    ident = add("Teammate")
    held_by(bound, B, ident)
    bound.current = fake(A)
    envelopes.clear()
    text = bs.backlog_pick_task(ident, force=True)
    assert text.startswith("Error:") and "Only an operator" in text and "Ask bob" in text, text
    assert "[seq" not in text and envelopes == []
    assert holder(native_project, ident) == B
    bound.current = fake(OPAL, may_force_any=True)
    assert "Error" not in bs.backlog_pick_task(ident, force=True)
    assert holder(native_project, ident) == OPAL


def test_claim_status_lists_adoptable(native_project, bound, connected):  # noqa: F811
    gone, open_, bobs = add("Gone"), add("Open"), add("Bobs")
    held_by(bound, OLD, gone)
    held_by(bound, OLD2, open_)
    held_by(bound, B, bobs)
    connected.add(OLD2)
    bound.current = fake(NEW)
    status = json.loads(bs.backlog_claim(action="status"))
    assert status["claims"] == []
    assert [(c["task_id"], c["holder"]) for c in status["adoptable"]] == [(gone, OLD)]
    assert status["adoptable"][0]["expires_at"]
    bound.current = None
    assert "adoptable" not in json.loads(bs.backlog_claim(action="status"))


def test_renew_adopts_and_says_so(native_project, bound, connected):  # noqa: F811
    ident = add("Renew")
    held_by(bound, OLD, ident)
    bound.current = fake(NEW)
    result = json.loads(bs.backlog_claim(action="renew", task_id=ident))
    assert result["ok"] and result["holder"] == NEW and result["adopted_from"] == OLD, result


def test_conflict_hints_are_requester_aware(native_project, bound, connected):  # noqa: F811
    bobs, open_ = add("Bobs"), add("Open")
    held_by(bound, B, bobs)
    held_by(bound, OLD2, open_)
    connected.add(OLD2)
    bound.current = fake(A)
    hint = json.loads(bs.backlog_claim(action="release", task_id=bobs))["hint"]
    assert "Ask bob" in hint and "force=True" not in hint, hint
    bound.current = fake(NEW)
    hint = json.loads(bs.backlog_claim(action="renew", task_id=open_))["hint"]
    assert "your other open session" in hint and "force=True" in hint, hint
    bound.current = fake(OPAL, may_force_any=True)
    hint = json.loads(bs.backlog_claim(action="renew", task_id=bobs))["hint"]
    assert hint == claims.HINT.format(task_id=bobs)


def test_next_available_offers_adoptable_tasks(native_project, bound, connected):  # noqa: F811
    ident = add("Parked")
    assert "Error" not in bs.backlog_update_epic("test-epic", "status", "active")
    bound.current = fake(NEW)
    assert f"`{ident}`" in bs.backlog_next_available(), bs.backlog_next_available()
    held_by(bound, OLD, ident)
    bound.current = None     # the host moves it back to todo; a peer's live claim survives that
    assert "Error" not in bs.backlog_update_task(ident, "status", "todo")
    assert holder(native_project, ident) == OLD
    assert committed(native_project)[("task", ident)][0]["status"] == "todo"
    bound.current = fake(NEW)
    listed = bs.backlog_next_available()
    assert f"`{ident}`" in listed.split("ready to pick")[1].split("\n\n")[0], listed
    bound.current = fake(B)
    listed = bs.backlog_next_available()
    assert "ready to pick" not in listed or f"`{ident}`" not in listed.split("ready to pick")[1].split("\n\n")[0]


# R4: the exact-equality claim comparisons left in the code are the documented ones (design §3 D6).
EQUALITY = re.compile(r"""locked_by"?\)?\s*[!=]=\s*session|holder\s*[!=]=\s*session|[!=]= SESSION_ID""")
ALLOWED_EQUALITY = {
    "taskmaster/native/claims.py": ("holder == session or adoptable(holder, session)",
                                    "holder != session and not adoptable(holder, session)",
                                    "if not holder or holder == session:"),
    "taskmaster/native/blockers.py": ("claim.mine or claim.holder == session",),
    "taskmaster/native_routing/tasks.py": ('if locked_by == session:',
                                           'm.get("locked_by") == session for m in members'),
    "taskmaster/native/workflow.py": ('entities[m]["fields"].get("locked_by") == session for m in members',),
    "taskmaster/backlog_server.py": ('task.get("locked_by") == session and task.get("status") in',),
}


def test_exact_equality_sites_are_the_documented_ones():
    for path in (REPO / "taskmaster").rglob("*.py"):
        rel = path.relative_to(REPO).as_posix()
        for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not EQUALITY.search(line):
                continue
            if any(allowed in line for allowed in ALLOWED_EQUALITY.get(rel, ())):
                continue
            raise AssertionError(f"{rel}:{number}: {line.strip()} - use claims.foreign_holder or read().mine, "
                                 f"or add the site to design §3 D6 with its reason")


KNOWN_THREADS = ("taskmaster-merge-stamp-replay", "taskmaster-sync-job-call", "_WRITER_WAIT_PUMP", "serve_forever")


# A thread that keeps a person's session but loses the request origin is refused by the client funnel
# (team-server-004 D4, tests/test_serve_lost_context.py); a thread that loses both is what this tripwire catches.
def test_tool_threads_are_the_known_ones():
    files = [REPO / "taskmaster/backlog_server.py", REPO / "taskmaster/coordinator/adapter.py",
             REPO / "taskmaster/coordinator/client.py", *(REPO / "taskmaster/native_routing").glob("*.py")]
    for path in files:
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines):
            if "threading.Thread(" not in line:
                continue
            window = "\n".join(lines[max(0, number - 1):number + 3])
            assert any(name in window for name in KNOWN_THREADS), (
                f"{path.name}:{number + 1} starts a thread on a tool path; pass the session and origin "
                f"explicitly or run it in a copied/empty context, then add it here (design §5)")
