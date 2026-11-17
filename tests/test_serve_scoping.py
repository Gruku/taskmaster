# User intent: prove over real HTTP, and across every origin and scope shape, that a team-server member can reach
# only receipts, queued work and request ids filed under their own handle (team-server-004).
from __future__ import annotations

import json

import pytest

from serve_helpers import open_connection
from taskmaster.coordinator.linear_worker import LinearWorker
from taskmaster.serve.principals import add_user  # noqa: F401
from taskmaster.serve.sessions import SESSION_REFUSED
from test_native_service_build import root  # noqa: F401
from test_serve_principals_e2e import _value, launch  # noqa: F401
from test_serve_session_guard import ANN, BOB, OPAL, envelope, world  # noqa: F401


@pytest.fixture
def web(launch, monkeypatch):  # noqa: F811
    monkeypatch.setattr(LinearWorker, "_config", lambda self: {"workspaces": {}})
    w = launch()
    opened = []

    def connect_token(token):
        connection = open_connection(w.url, token)
        opened.append(connection)
        return connection

    w.connect = lambda who: connect_token(w.keys[who].key)
    w.connect_token = connect_token
    yield w
    for connection in opened:
        try:
            connection.close()
        except Exception:  # noqa: BLE001
            pass


def session_of(connection):
    return json.loads(connection.call("backlog_claim", {"action": "status"}))["session"]


def receipts(root_, scope):
    return _value(root_, f"SELECT request_id FROM command_receipts WHERE caller_scope='{scope}'")


def test_a_members_retry_is_refused_as_operator_only_and_files_nothing(web):
    """User ruling on the security review's m-7: Linear retry is the operator's on a team server, whatever scope a
    member names, own, foreign or internal."""
    ann, bob = web.connect("ann"), web.connect("bob")
    bob_scope, ann_scope = session_of(bob), session_of(ann)
    for scope in (bob_scope, "linear-settle", ann_scope, None):
        arguments = {"action": "retry", "request_id": f"steal-{scope}"}
        if scope is not None:
            arguments["caller_scope"] = scope
        answer = json.loads(ann.call("backlog_linear", arguments))
        assert "operator action on a team server" in answer["error"], (scope, answer)
    assert receipts(web.root, bob_scope) == [] and receipts(web.root, ann_scope) == []


def test_the_operators_retry_still_works_over_the_server(web):
    operator = web.connect_token(web.operator)
    scope = session_of(operator)
    answer = json.loads(operator.call("backlog_linear", {"action": "retry", "request_id": "own1"}))
    assert answer.get("error") is None and answer["caller_scope"] == scope


def test_reconnected_operator_recovers_its_retry_receipt(web):
    first = web.connect_token(web.operator)
    old_scope = session_of(first)
    one = json.loads(first.call("backlog_linear", {"action": "retry", "request_id": "again1"}))
    assert receipts(web.root, old_scope) != []
    first.close()
    second = web.connect_token(web.operator)
    assert session_of(second) != old_scope
    two = json.loads(second.call("backlog_linear", {"action": "retry", "caller_scope": old_scope, "request_id": "again1"}))
    assert two == one and "error" not in two
    assert len(receipts(web.root, old_scope)) == len(set(receipts(web.root, old_scope)))


# ---- the inventory (spec Part A section 6) ------------------------------------------------------------------------

from taskmaster.coordinator import allowlist, scopes  # noqa: E402
from taskmaster.coordinator.client import Client  # noqa: E402
from taskmaster.serve import auth  # noqa: E402
from taskmaster.team_identity import IdentityRefused  # noqa: E402

OPERATOR_SCOPE = "operator@" + "0" * 16
FOREIGN = [BOB, "bob@viewer", "host-1-aaaaaaaa", *sorted(scopes.INTERNAL), "sync-" + "0" * 64, "git:ann@" + "a" * 16]


def test_scope_inventory(world, monkeypatch):  # noqa: F811
    owner = world.owner
    seen = []
    monkeypatch.setattr(owner.linear, "response", lambda scope, rid, target_id="": seen.append(scope) or {"ok": True})
    client = Client(world.root, autostart=False)
    origins = {"ann": (world.ann, ANN, ["ann@" + "f" * 16, "ann@viewer", "ann@read"]),
               "opal": (world.opal, OPAL, []),
               "operator": (auth.OPERATOR_KEY_ID, OPERATOR_SCOPE, [])}
    pending_before = dict(owner.pending)

    def retry(scope, origin=None):
        extra = {} if origin is None else {"origin": {"principal": origin}}
        return client.call("linear_retry", caller_scope=scope, request_id="inv", **extra)

    def run(scope, origin=None):
        extra = {} if origin is None else {"origin": {"principal": origin}}
        return client.call("execute", envelope=envelope(world, scope), visibility="native", **extra)

    operator_only = allowlist.ADMIN_REFUSED.format(what=allowlist.WHAT["linear_retry"])
    for name, (key, own, extras) in origins.items():
        member = name != "operator"
        # 1. an own-handle scope passes execute (and ann's older session and own channels); retry is the operator's
        #    on a team server (m-7 ruling), so a member's is refused whatever its scope
        for scope in [own, *extras]:
            if member:
                with pytest.raises(ValueError) as raised:
                    retry(scope, key)
                assert str(raised.value) == operator_only, (name, scope)
            else:
                assert retry(scope, key) == {"ok": True}, (name, scope)
            assert run(scope, key), (name, scope)
        # 2. anything else is refused with 003's text (a member's retry with the operator-only text), takes no slot
        for scope in FOREIGN + ([ANN] if name != "ann" else []):
            before = len(seen)
            for call in (retry, run):
                expected = (ValueError, operator_only) if member and call is retry else (IdentityRefused, SESSION_REFUSED)
                with pytest.raises(expected[0]) as raised:
                    call(scope, key)
                assert str(raised.value) == expected[1], (name, scope, call)
            assert len(seen) == before
        # 3. the named-scope methods: members keep 005's text, the bootstrap operator is scope-checked
        for method, kwargs in (("receipt", dict(request_id="x")), ("cancel", dict(request_id="x")), ("sync_status", {}),
                               ("git_run", dict(kind="status")), ("sync", dict(files=None, take_file=False,
                                                                                import_files=True, through=0))):
            for scope in (BOB, "explicit-sync"):
                with pytest.raises((ValueError, IdentityRefused)) as raised:
                    client.call(method, caller_scope=scope, origin={"principal": key}, **kwargs)
                if name == "operator":
                    assert type(raised.value) is IdentityRefused and str(raised.value) == SESSION_REFUSED, (method, scope)
                else:
                    assert type(raised.value) is ValueError and str(raised.value) != SESSION_REFUSED, (method, scope, name)
    # 4. LOCAL (no origin) is unrestricted, and nothing refused above took a pending slot
    for scope in FOREIGN:
        assert retry(scope) == {"ok": True}
    assert client.call("receipt", caller_scope=BOB, request_id="never")["state"] == "unknown"
    assert owner.pending == pending_before and not owner.linear.jobs
    assert allowlist.OPERATOR_ONLY >= {"receipt", "cancel", "sync_status", "sync", "git_run"}
