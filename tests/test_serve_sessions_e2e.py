# User intent: over real HTTP, each teammate's connection holds claims as its own session, a teammate's claim cannot be taken, dropped or renewed by a member, and a restarted agent takes back its own work without force.
from __future__ import annotations

import json
import re
import time

import pytest

from native_twins import committed
from serve_helpers import open_connection
from taskmaster.serve.principals import KEY_RE, add_user
from test_native_service_build import root  # noqa: F401
from test_serve_principals_e2e import ADD, events, launch, serve_log  # noqa: F401

E7 = "only an operator can do that to a teammate's claim"


def poll(predicate, deadline=15.0):
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


@pytest.fixture
def world(launch, root):  # noqa: F811
    opal_key, _record, _ = add_user(root, "opal", role="operator", name="Opal Person", emails=["opal@example.com"])
    w = launch()
    w.opal = opal_key
    opened = []

    def connect(who, client_name="claude-code"):
        key = w.opal if who == "opal" else w.keys[who].key
        connection = open_connection(w.url, key, client_name)
        opened.append(connection)
        return connection

    w.connect = connect
    yield w
    for connection in opened:
        try:
            connection.close()
        except Exception:  # noqa: BLE001 - a test may have closed it already
            pass


def add(connection, title, **extra):
    text = connection.call("backlog_add_task", dict(ADD, title=title, **extra))
    assert "[seq " in text, text
    return re.search(r"`(test-epic-\d+)`", text).group(1)


def holder(root_, ident):
    return committed(root_)[("task", ident)][0].get("locked_by")


def status_of(root_, ident):
    return committed(root_)[("task", ident)][0].get("status")


def session_of(world, connection):
    """The session the server minted for this connection: the holder of a fresh claim status."""
    return json.loads(connection.call("backlog_claim", {"action": "status"}))["session"]


def test_teammates_hold_claims_as_themselves(world):
    ann, bob = world.connect("ann"), world.connect("bob")
    first, second = add(ann, "A"), add(bob, "B")
    assert "Picked" in ann.call("backlog_pick_task", {"task_id": first})
    assert "Picked" in bob.call("backlog_pick_task", {"task_id": second})
    assert holder(world.root, first).startswith("ann@") and holder(world.root, second).startswith("bob@")
    picks = [e for e in events(world.root) if e[0] == "task" and e[1] in (first, second) and e[3]]
    assert {e[3]["handle"] for e in picks if e[1] == first} == {"ann"}
    assert {e[3]["handle"] for e in picks if e[1] == second} == {"bob"}


def test_one_key_two_connections_are_two_sessions(world):
    one, two = world.connect("ann"), world.connect("ann")
    first, second = add(one, "One"), add(two, "Two")
    one.call("backlog_pick_task", {"task_id": first})
    two.call("backlog_pick_task", {"task_id": second})
    s1 = json.loads(one.call("backlog_claim", {"action": "status"}))
    s2 = json.loads(two.call("backlog_claim", {"action": "status"}))
    assert s1["session"] != s2["session"]
    assert [c["task_id"] for c in s1["claims"]] == [first]
    assert [c["task_id"] for c in s2["claims"]] == [second]


def test_teammate_cannot_take_free_or_renew(world):
    ann, bob, opal = world.connect("ann"), world.connect("bob"), world.connect("opal")
    ident = add(bob, "Bobs")
    bob.call("backlog_pick_task", {"task_id": ident})
    bobs = holder(world.root, ident)
    assert "[seq" not in ann.call("backlog_pick_task", {"task_id": ident})
    forced = ann.call("backlog_pick_task", {"task_id": ident, "force": True})
    assert "Only an operator" in forced and "[seq" not in forced, forced
    renew = json.loads(ann.call("backlog_claim", {"action": "renew", "task_id": ident}))
    release = json.loads(ann.call("backlog_claim", {"action": "release", "task_id": ident}))
    assert renew["ok"] is False and release["ok"] is False
    assert "Ask bob" in release["hint"] and "force=True" not in release["hint"]
    assert holder(world.root, ident) == bobs
    taken = opal.call("backlog_pick_task", {"task_id": ident, "force": True})
    assert "Error" not in taken, taken
    assert holder(world.root, ident).startswith("opal@")


def test_teammate_cannot_drop_a_claim_by_status(world):
    ann, bob, opal = world.connect("ann"), world.connect("bob"), world.connect("opal")
    ident = add(bob, "Bobs")
    parked = add(bob, "Parked")
    bob.call("backlog_pick_task", {"task_id": ident})
    bob.call("backlog_pick_task", {"task_id": parked})
    for gate in ("design-review", "review-gate"):   # so completion reaches the claim rule, not the gates
        skipped = bob.call("backlog_skip_gate", {"task_id": ident, "gate": gate, "reason": "test"})
        assert "[seq" in skipped, skipped
    bobs = holder(world.root, ident)
    # An operator parks one as blocked; a peer's live claim survives that status move.
    assert "[seq" in opal.call("backlog_update_task", {"task_id": parked, "field": "status", "value": "blocked"})
    assert holder(world.root, parked) == bobs
    attempts = [
        ann.call("backlog_update_task", {"task_id": ident, "field": "status", "value": "archived"}),
        ann.call("backlog_complete_task", {"task_id": ident}),
        ann.call("backlog_archive_task", {"task_id": parked, "reason": "wont-fix"}),
        ann.call("backlog_batch_update", {"operations": f"status {ident} done"}),
    ]
    for answer in attempts:
        assert E7 in answer and "[seq" not in answer.split(E7)[0], answer
    assert holder(world.root, ident) == bobs and status_of(world.root, ident) == "in-progress"
    assert holder(world.root, parked) == bobs and status_of(world.root, parked) == "blocked"
    done = opal.call("backlog_update_task", {"task_id": ident, "field": "status", "value": "archived"})
    assert "[seq" in done, done
    assert not holder(world.root, ident)


def test_restarted_agent_adopts_its_work(world):
    first = world.connect("ann")
    ident = add(first, "Mine")
    first.call("backlog_pick_task", {"task_id": ident})
    old = holder(world.root, ident)
    first.close()
    assert poll(lambda: not world.daemon.sessions.connected(old))
    second = world.connect("ann")
    text = second.call("backlog_pick_task", {"task_id": ident})
    assert "adopted from your earlier session" in text and old in text, text
    assert holder(world.root, ident) == session_of(world, second) != old


def test_open_own_session_needs_force(world):
    first, second = world.connect("ann"), world.connect("ann")
    ident = add(first, "Mine")
    first.call("backlog_pick_task", {"task_id": ident})
    old = holder(world.root, ident)
    refused = second.call("backlog_pick_task", {"task_id": ident})
    assert "your other open session" in refused and "[seq" not in refused, refused
    assert holder(world.root, ident) == old
    assert "Error" not in second.call("backlog_pick_task", {"task_id": ident, "force": True})
    assert holder(world.root, ident) == session_of(world, second)


def test_current_task_and_agent_are_per_connection(world):
    ann, bob = world.connect("ann", "claude-code"), world.connect("bob", "codex")
    alpha = add(ann, "Alpha one", bundle="alpha")
    beta = add(bob, "Beta one", bundle="beta")
    assert "Picking bundle `alpha`" in ann.call("backlog_pick_task", {"task_id": alpha})
    assert "Picking bundle `beta`" in bob.call("backlog_pick_task", {"task_id": beta})
    ann.call("backlog_handover_create", {"tldr": "ann work", "next_action": "go on"})
    bob.call("backlog_handover_create", {"tldr": "bob work", "next_action": "go on"})
    threads = {doc.get("tldr"): doc.get("thread") for (kind, _id), (doc, _b, _a) in committed(world.root).items()
               if kind == "handover"}
    assert "alpha" in threads["ann work"] and "beta" in threads["bob work"], threads
    agents = {(e[3]["handle"], e[3]["agent"]) for e in events(world.root) if e[0] == "task" and e[3]
              and e[3]["handle"] in ("ann", "bob")}
    assert agents == {("ann", "claude"), ("bob", "codex")}, agents


def test_sessions_hold_no_transport_ids_or_keys(world):
    ann, bob = world.connect("ann"), world.connect("bob")
    for connection, title in ((ann, "A"), (bob, "B")):
        connection.call("backlog_pick_task", {"task_id": add(connection, title)})
    transports = [entry.transport_id for entry in world.daemon.sessions.live()]
    assert len(transports) == 2
    secrets = [KEY_RE.fullmatch(world.keys[h].key).group(2) for h in ("ann", "bob")]
    holders = [doc.get("locked_by") or "" for (kind, _id), (doc, _b, _a) in committed(world.root).items()
               if kind == "task"]
    exported = "\n".join(path.read_text(encoding="utf-8", errors="replace")
                         for path in (world.root / ".taskmaster").rglob("*")
                         if path.is_file() and "local" not in path.relative_to(world.root / ".taskmaster").parts
                         and path.suffix in (".yaml", ".md", ".json"))
    log = serve_log(world.root)
    for needle in [*transports, *secrets]:
        assert all(needle not in value for value in holders)
        assert needle not in exported and needle not in log
