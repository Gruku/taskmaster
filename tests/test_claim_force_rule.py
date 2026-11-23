# User intent: inside the command's own transaction, only an operator can force-take a teammate's claim or drop it by any other write; host-local and solo callers keep today's rule.
from __future__ import annotations

from contextlib import closing
import sqlite3

import pytest

from taskmaster.native import claims
from taskmaster.native.claims import Requester
from taskmaster.native.commands import Conflict, execute
from taskmaster.native.queries import Repository
from test_native_claims import burn_ttl
from test_native_commands import native  # noqa: F401
from test_native_migration import legacy  # noqa: F401
from test_native_service_build import root  # noqa: F401

ANN, ANN_OLD, ANN_LIVE = "ann@" + "a" * 16, "ann@" + "e" * 16, "ann@" + "c" * 16
BOB = "bob@" + "b" * 16
MEMBER, OPERATOR_ROLE = Requester("ann", False), Requester("opal", True)
_KEYS = iter(range(10 ** 6))


def run(connection, operation, arguments, *, scope, requester=None, key=None):
    request = {"protocol": 2, "store_id": "fixture-store", "caller_scope": scope,
               "request_id": key or f"r{next(_KEYS)}", "operation": operation, "arguments": arguments,
               "expected_revisions": []}
    return execute(connection, request, requester=requester)


def pick(connection, ident, session, *, force=False, requester=None):
    return run(connection, "task.pick", {"id": ident, "session": session, "force": force},
               scope=session, requester=requester)


def fields(connection, ident):
    with Repository(connection).snapshot() as query:
        return query.get("task", ident)["fields"]


def receipts(connection):
    return connection.execute("SELECT COUNT(*) FROM command_receipts").fetchone()[0]


@pytest.fixture
def db(native, monkeypatch):  # noqa: F811
    monkeypatch.setattr(claims, "CONNECTED", lambda session: session == ANN_LIVE)
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        run(connection, "phase.create", {"phase_id": "foundation", "name": "Foundation"}, scope="setup")
        run(connection, "epic.create", {"epic_id": "demo", "name": "Demo", "done_when": "shipped"}, scope="setup")
        for title in ("First", "Second", "Third", "Fourth"):
            run(connection, "task.create", {"title": title, "epic": "demo", "phase": "foundation"}, scope="setup")
        for ident in ("demo-003", "demo-004"):
            run(connection, "task.update", {"id": ident, "field": "bundle", "value": "pair"}, scope="setup")
        for gate in ("design-review", "review-gate"):   # so `done` is reachable for the status tests
            run(connection, "task.gate_skip", {"id": "demo-001", "gate": gate, "reason": "test"}, scope="setup")
        yield connection


# ── D7: force across people ─────────────────────────────────────────────────


def test_member_cannot_force_another_handles_claim(db):
    pick(db, "demo-001", BOB)
    before = receipts(db)
    with pytest.raises(Conflict, match="Only an operator") as raised:
        pick(db, "demo-001", ANN, force=True, requester=MEMBER)
    assert "bob" in str(raised.value)
    assert fields(db, "demo-001")["locked_by"] == BOB and receipts(db) == before


def test_operator_role_can_force(db):
    pick(db, "demo-001", BOB)
    pick(db, "demo-001", ANN, force=True, requester=OPERATOR_ROLE)
    assert fields(db, "demo-001")["locked_by"] == ANN


def test_same_handle_force_is_allowed(db):
    pick(db, "demo-001", ANN_LIVE)
    pick(db, "demo-001", ANN, force=True, requester=MEMBER)
    assert fields(db, "demo-001")["locked_by"] == ANN


def test_expired_claim_can_be_forced_by_anyone(db):
    pick(db, "demo-001", BOB)
    burn_ttl(db, "demo-001")
    pick(db, "demo-001", ANN, force=True, requester=MEMBER)
    assert fields(db, "demo-001")["locked_by"] == ANN


def test_bundle_force_checks_every_member(db):
    pick(db, "demo-003", BOB)
    before = {ident: fields(db, ident) for ident in ("demo-003", "demo-004")}
    with pytest.raises(Conflict, match="Only an operator"):
        pick(db, "demo-004", ANN, force=True, requester=MEMBER)
    assert {ident: fields(db, ident) for ident in before} == before


def test_no_requester_is_todays_rule(db):
    pick(db, "demo-001", BOB)
    pick(db, "demo-001", ANN, force=True, requester=None)
    assert fields(db, "demo-001")["locked_by"] == ANN


def test_requester_does_not_change_the_payload_hash(db):
    first = run(db, "task.pick", {"id": "demo-001", "session": ANN}, scope=ANN, key="same", requester=MEMBER)
    again = run(db, "task.pick", {"id": "demo-001", "session": ANN}, scope=ANN, key="same", requester=OPERATOR_ROLE)
    assert again == first


def test_adoption_without_force_inside_the_command(db):
    """Characterisation of Step 1 seen through the core: it passes once Step 1 lands."""
    pick(db, "demo-001", ANN_OLD)
    pick(db, "demo-001", ANN, requester=MEMBER)
    assert fields(db, "demo-001")["locked_by"] == ANN
    assert fields(db, "demo-001")[claims.EXPIRES_FOR_FIELD] == ANN
    newer = "ann@" + "f" * 16
    run(db, "task.claim_renew", {"id": "demo-001", "session": newer}, scope=newer, requester=MEMBER)
    assert fields(db, "demo-001")["locked_by"] == newer
    with pytest.raises(Conflict):
        run(db, "task.claim_release", {"id": "demo-001", "session": BOB}, scope=BOB, requester=Requester("bob", False))


def test_coordinator_passes_its_requester(root, monkeypatch):  # noqa: F811
    from taskmaster.coordinator.service import Coordinator

    class Member(Coordinator):
        def _requester(self, work):
            return MEMBER

    for cls, refused in ((Member, True), (Coordinator, False)):
        owner = cls(root).start()
        try:
            store_id = owner.identity["store_id"]

            def submit(operation, arguments, scope, key):
                envelope = {"protocol": 2, "store_id": store_id, "caller_scope": scope, "request_id": key,
                            "operation": operation, "arguments": arguments, "expected_revisions": []}
                return owner.submit(envelope).result(30)

            ident = submit("task.create", {"title": f"T {cls.__name__}", "epic": "test-epic", "phase": "dev"},
                           "setup", f"c-{cls.__name__}")["affected"][0]["id"]
            submit("task.pick", {"id": ident, "session": BOB}, BOB, f"b-{cls.__name__}")
            if refused:
                with pytest.raises(Conflict, match="Only an operator"):
                    submit("task.pick", {"id": ident, "session": ANN, "force": True}, ANN, f"a-{cls.__name__}")
            else:
                submit("task.pick", {"id": ident, "session": ANN, "force": True}, ANN, f"a-{cls.__name__}")
        finally:
            owner.close()


# ── D11: no member write drops or moves a teammate's live claim ─────────────

# (operation, arguments, the status the host moves the task to first, or None)
STATUS_WRITES = {
    "update-done": ("task.update", lambda i: {"id": i, "field": "status", "value": "done"}, None),
    "update-archived": ("task.update", lambda i: {"id": i, "field": "status", "value": "archived"}, None),
    "complete": ("task.complete", lambda i: {"id": i, "target_status": "done"}, None),
    "archive": ("task.archive", lambda i: {"id": i, "reason": "deprecated"}, "blocked"),
    "batch-line": ("task.batch_line", lambda i: {"id": i, "op": "status", "status": "archived"}, None),
    "viewer-update": ("task.viewer_update", lambda i: {"id": i, "patch": {"status": "archived"}, "if_match": ""}, None),
    "viewer-archive": ("task.viewer_archive", lambda i: {"id": i, "if_match": ""}, None),
}
# Writes that are legal from a `done` row (the stranded-claim case).
FROM_DONE = ("update-archived", "archive", "batch-line", "viewer-update", "viewer-archive")
HOST = "host-1-aaaaaaaa"


def prepare(db, write):
    """Move the task to the status the write starts from, as the host (a peer's claim survives it)."""
    status = STATUS_WRITES[write][2]
    if status:
        run(db, "task.update", {"id": "demo-001", "field": "status", "value": status}, scope=HOST)


def apply_write(db, write, *, scope, requester):
    operation, arguments, _ = STATUS_WRITES[write]
    return run(db, operation, arguments("demo-001"), scope=scope, requester=requester)


@pytest.mark.parametrize("write", sorted(STATUS_WRITES))
def test_member_status_move_cannot_drop_a_teammates_claim(db, write):
    pick(db, "demo-001", BOB)
    prepare(db, write)
    before, count = fields(db, "demo-001"), receipts(db)
    assert before["locked_by"] == BOB
    with pytest.raises(Conflict, match="only an operator") as raised:
        apply_write(db, write, scope=ANN, requester=MEMBER)
    assert "bob" in str(raised.value)
    assert fields(db, "demo-001") == before and receipts(db) == count


@pytest.mark.parametrize("write", sorted(STATUS_WRITES))
@pytest.mark.parametrize("requester", [OPERATOR_ROLE, None], ids=["operator-role", "host-local"])
def test_operator_and_host_may_drop_by_status(db, write, requester):
    pick(db, "demo-001", BOB)
    prepare(db, write)
    apply_write(db, write, scope="opal@" + "0" * 16, requester=requester)
    assert fields(db, "demo-001").get("locked_by") != BOB


def _strand(db, holder):
    """A claim left on a `done` row (data from before the terminal rule)."""
    pick(db, "demo-001", HOST)
    run(db, "task.update", {"id": "demo-001", "field": "status", "value": "done"}, scope=HOST)
    key = db.execute("SELECT entity_key FROM entity_core WHERE kind='task' AND public_id='demo-001'").fetchone()[0]
    db.execute("UPDATE task_operational SET locked_by_json=json_quote(?) WHERE entity_key=?", (holder, key))
    assert fields(db, "demo-001")["locked_by"] == holder


@pytest.mark.parametrize("story", ["own-live", "own-gone", "expired", "stranded", "unclaimed"])
@pytest.mark.parametrize("write", sorted(STATUS_WRITES))
def test_status_move_rule_spares_own_expired_and_unclaimed(db, write, story):
    if story == "stranded":
        if write not in FROM_DONE:
            pytest.skip("not a legal write from a done row")
        _strand(db, BOB)
    else:
        holder = {"own-live": ANN_LIVE, "own-gone": ANN_OLD, "expired": BOB, "unclaimed": None}[story]
        if holder:
            pick(db, "demo-001", holder)
        else:   # in progress, with the claim released
            pick(db, "demo-001", HOST)
            run(db, "task.claim_release", {"id": "demo-001", "session": HOST}, scope=HOST)
        if story == "expired":
            burn_ttl(db, "demo-001")
        prepare(db, write)
    apply_write(db, write, scope=ANN, requester=MEMBER)


@pytest.mark.parametrize("status", ["blocked", "todo"])
def test_member_cannot_move_a_teammates_live_claimed_task_to_any_status(db, status):
    # team-server-033: the claim would survive a non-terminal move, but the status may not change.
    pick(db, "demo-001", BOB)
    before = fields(db, "demo-001")
    with pytest.raises(Conflict, match="Only bob or an operator can change its status"):
        run(db, "task.update", {"id": "demo-001", "field": "status", "value": status}, scope=ANN, requester=MEMBER)
    assert fields(db, "demo-001") == before


NON_TERMINAL_WRITES = {
    "batch-line": ("task.batch_line", {"op": "status", "status": "blocked"}),
    "viewer-update": ("task.viewer_update", {"patch": {"status": "blocked"}, "if_match": ""}),
}


@pytest.mark.parametrize("write", sorted(NON_TERMINAL_WRITES))
def test_member_cannot_move_a_teammates_claimed_task_to_a_non_terminal_status_by_any_writer(db, write):
    # 033 review M-6: the status refusal holds on the batch and viewer paths too, not only task.update.
    operation, arguments = NON_TERMINAL_WRITES[write]
    pick(db, "demo-001", BOB)
    before, count = fields(db, "demo-001"), receipts(db)
    with pytest.raises(Conflict, match="Only bob or an operator can change its status"):
        run(db, operation, {"id": "demo-001", **arguments}, scope=ANN, requester=MEMBER)
    assert fields(db, "demo-001") == before and receipts(db) == count


@pytest.mark.parametrize("requester", [OPERATOR_ROLE, None], ids=["operator-role", "stdio-no-principal"])
def test_operator_and_principal_less_callers_may_move_a_claimed_tasks_status(db, requester):
    pick(db, "demo-001", BOB)
    run(db, "task.update", {"id": "demo-001", "field": "status", "value": "blocked"},
        scope="opal@" + "0" * 16, requester=requester)
    assert fields(db, "demo-001")["status"] == "blocked" and fields(db, "demo-001")["locked_by"] == BOB


def test_the_holders_person_and_unclaimed_tasks_move_freely(db):
    pick(db, "demo-001", ANN_LIVE)
    run(db, "task.update", {"id": "demo-001", "field": "status", "value": "blocked"}, scope=ANN, requester=MEMBER)
    assert fields(db, "demo-001")["status"] == "blocked"
    run(db, "task.update", {"id": "demo-002", "field": "status", "value": "blocked"}, scope=ANN, requester=MEMBER)


@pytest.mark.parametrize("patch", [{"remove": ["locked_by"]}, {"set": {"claim_expires": "2000-01-01T00:00:00Z"}}])
def test_patch_cannot_touch_claim_fields_of_a_teammate(db, patch):
    """The contract already refuses claim fields in `task.patch`; D11's funnel is the backstop."""
    pick(db, "demo-001", BOB)
    with pytest.raises((Conflict, ValueError)):
        run(db, "task.patch", {"id": "demo-001", **patch}, scope=ANN, requester=MEMBER)
    assert fields(db, "demo-001")["locked_by"] == BOB

