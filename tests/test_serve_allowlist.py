# User intent: every coordinator method is classified once, and both the serve-process client and the serve coordinator refuse admin methods to anyone but the host operator, failing closed when the principal is unknown.
from __future__ import annotations

import inspect
import re
import subprocess
import sys

import pytest

from taskmaster.coordinator import allowlist
from taskmaster.coordinator.service import Coordinator

MEMBER = "a" * 16


# ── Step 0: the table ────────────────────────────────────────────────────────


def test_every_dispatched_method_is_classified():
    source = inspect.getsource(Coordinator.dispatch)
    assert set(re.findall(r"method == '(\w+)'", source)) == allowlist.METHODS
    groups = (allowlist.REMOTE, allowlist.SERVER_REFUSED, allowlist.OPERATOR_ONLY)
    assert all(not (a & b) for i, a in enumerate(groups) for b in groups[i + 1:])
    assert allowlist.REMOTE | allowlist.SERVER_REFUSED | allowlist.OPERATOR_ONLY == allowlist.METHODS


def test_operator_constant_matches_serve():
    from taskmaster.serve.auth import OPERATOR_KEY_ID
    assert allowlist.OPERATOR == OPERATOR_KEY_ID


@pytest.mark.parametrize("method", sorted(allowlist.METHODS) + ["bogus", None])
@pytest.mark.parametrize("principal", [MEMBER, None, "operator"])
def test_refusal_table(method, principal):
    text = allowlist.refusal(method, {}, principal)
    if principal == "operator" or method in allowlist.SERVER_REFUSED or method in allowlist.REMOTE:
        assert text is None
    elif method in allowlist.OPERATOR_ONLY:
        assert allowlist.WHAT[method] in text and "nothing was changed" in text
    else:
        assert allowlist.UNKNOWN_WHAT in text


@pytest.mark.parametrize("arguments", [{"take_file": True, "files": ["x"]}, {"take_file": 1},
                                       {"take_file": "yes"}, {"take_file": None}])
def test_take_file_sync_is_host_only(arguments):
    text = allowlist.refusal("sync", arguments, MEMBER)
    assert allowlist.TAKE_FILE_WHAT in text and text.endswith(allowlist.TAKE_FILE_HINT)


def test_worktree_sync_is_host_only():
    assert allowlist.WORKTREE_WHAT in allowlist.refusal("sync", {"worktree": "/srv/x"}, MEMBER)


@pytest.mark.parametrize("principal", [MEMBER, None])
@pytest.mark.parametrize("method, arguments", [
    ("sync", {}), ("sync", {"take_file": False}), ("sync", {"take_file": False, "worktree": None}),
    ("sync", {"files": ["notes/NOTE-900.md"], "take_file": False}),
    ("sync_job", {}), ("sync_job", {"files": ["notes/NOTE-900.md"]}), ("sync_job", {"sync_id": "x"})])
def test_plain_sync_is_host_only(method, arguments, principal):
    """team-server-012 D1: a member never makes the host import its own files, in any form."""
    text = allowlist.refusal(method, arguments, principal)
    assert text == allowlist.HOST_SYNC_REFUSED
    assert allowlist.HOST_SYNC_WHAT in text and text.endswith(allowlist.REMOTE_SYNC_HINT)
    assert allowlist.refusal(method, arguments, "operator") is None


def _restore_envelope(**extra):
    arguments = {"kind": "task", "id": "x-001", "section": "spec", "path": "docs/spec.md", "body": "b",
                 "restore": True, "source": "upload", "imported_by": "ann", **extra}
    return {"operation": "document.import", "arguments": arguments}


@pytest.mark.parametrize("envelope", [
    _restore_envelope(),
    {"operation": "batch", "arguments": {"commands": [_restore_envelope()]}},
    {"operation": "batch", "arguments": {"commands": [{"operation": "task.patch", "arguments": {}}, _restore_envelope()]}},
])
def test_a_restore_is_the_operators_only(envelope):
    """team-server-012 D10 (and re-review Q-M2 for batches): restore=True never passes for a member."""
    for principal in (MEMBER, None):
        assert allowlist.refusal("execute", {"envelope": envelope}, principal) == allowlist.RESTORE_REFUSED
    assert allowlist.refusal("execute", {"envelope": envelope}, "operator") is None


def _plain_import(**extra):
    return {"operation": "document.import", "arguments": {"kind": "task", "id": "x-001", "section": "spec",
                                                          "path": "docs/spec.md", "body": "b", **extra}}


@pytest.mark.parametrize("envelope", [
    _plain_import(), _plain_import(source="file"), _plain_import(source=None),
    {"operation": "document.import", "arguments": "not-an-object"},
    {"operation": "batch", "arguments": {"commands": [_plain_import()]}},
    {"operation": "batch", "arguments": {"commands": [_plain_import(source="upload"), _plain_import()]}},
])
def test_a_document_import_without_source_upload_is_the_operators_only(envelope):
    """Codex review finding 2: a member's import is never recorded as a host file read, top level or in a batch."""
    for principal in (MEMBER, None):
        assert allowlist.refusal("execute", {"envelope": envelope}, principal) == allowlist.HOST_IMPORT_REFUSED
    assert allowlist.refusal("execute", {"envelope": envelope}, "operator") is None


@pytest.mark.parametrize("operation", sorted(allowlist.OPERATOR_OPERATIONS))
def test_the_manifest_and_linear_links_are_the_operators_only(operation):
    """User rulings on the security review's m-9 and m-7: top level or inside a batch, at any depth."""
    single = {"operation": operation, "arguments": {}}
    nested = {"operation": "batch", "arguments": {"commands": [
        {"operation": "task.patch", "arguments": {}},
        {"operation": "batch", "arguments": {"commands": [single]}}]}}
    for envelope in (single, nested):
        for principal in (MEMBER, None):
            refused = allowlist.refusal("execute", {"envelope": envelope}, principal)
            assert refused == allowlist.ADMIN_REFUSED.format(what=allowlist.OPERATOR_OPERATIONS[operation])
        assert allowlist.refusal("execute", {"envelope": envelope}, "operator") is None


@pytest.mark.parametrize("envelope", [
    {"operation": "document.import", "arguments": {"kind": "task", "source": "upload"}},
    {"operation": "batch", "arguments": {"commands": [_plain_import(source="upload")]}},
    {"operation": "task.patch", "arguments": {"restore": True}},
    {"operation": "batch", "arguments": {"commands": "nope"}}, None, "x", {"operation": "batch"},
])
def test_an_ordinary_execute_stays_remote(envelope):
    assert allowlist.refusal("execute", {"envelope": envelope}, MEMBER) is None


@pytest.mark.parametrize("value", ["", "TASKMASTER_LINEAR_TOKEN"])
def test_server_token_env_accepts_the_fixed_name(value):
    assert allowlist.server_token_env(value) == ("TASKMASTER_LINEAR_TOKEN", None)


@pytest.mark.parametrize("value", ["OPS_LINEAR_TOKEN", "AWS_SECRET_ACCESS_KEY", "taskmaster_linear_token"])
def test_server_token_env_refuses_other_names(value):
    assert allowlist.server_token_env(value) == (value, allowlist.LINEAR_TOKEN_ENV_REFUSED)


def test_bootstrap_entry_refusal():
    assert allowlist.bootstrap_entry_refusal({"token_env": "TASKMASTER_LINEAR_TOKEN", "alias": "x"}) is None
    for entry in (None, "x", {}, {"token_env": ""}, {"token_env": "OPS"}):
        assert allowlist.bootstrap_entry_refusal(entry) == allowlist.LINEAR_TOKEN_ENV_REFUSED


def test_log_helpers():
    assert allowlist.log_method("git_run") == "git_run"
    assert allowlist.log_method("x\n[fake]") == "other"
    assert allowlist.log_key(MEMBER) == MEMBER and allowlist.log_key("operator") == "operator"
    assert allowlist.log_key(None) == "-" and allowlist.log_key("../x") == "invalid"


def test_principal_of():
    assert allowlist.principal_of({"principal": MEMBER}) == MEMBER
    for origin in (None, "ann", {"principal": 5}, {"principal": MEMBER, "x": 1}):
        assert allowlist.principal_of(origin) is None


def test_allowlist_imports_nothing_from_serve():
    out = subprocess.run([sys.executable, "-c", "import taskmaster.coordinator.allowlist, sys; "
                          "print(any(m.startswith('taskmaster.serve') for m in sys.modules))"],
                         capture_output=True, text=True, check=True)
    assert out.stdout.strip() == "False"


# ── Step 1: the serve-process client ─────────────────────────────────────────

import threading  # noqa: E402
import time  # noqa: E402

from taskmaster.coordinator import client as client_module  # noqa: E402
from taskmaster.coordinator.client import Client  # noqa: E402
from test_native_service_build import request, root  # noqa: E402,F401

ARGS = {
    "git_run": dict(kind="not-a-kind", caller_scope="t", request_id="r", timeout=5),
    "git_recover": dict(acknowledge_quiescent="x"),
    "git_status": {},
    "receipt": dict(caller_scope="t", request_id="r"),
    "sync_status": dict(caller_scope="t", request_id="r"),
    "cancel": dict(caller_scope="t", request_id="r"),
    "linear_bootstrap": dict(entry={"alias": "", "team_id": "", "token_env": "TASKMASTER_LINEAR_TOKEN"},
                             default_workspace=False),
    "sync:take_file": dict(files=["tasks/none.md"], take_file=True, import_files=True, through=0,
                           caller_scope="t", request_id="r", timeout=5),
    "sync:worktree": dict(worktree="<root>/no-such-checkout", files=None, take_file=False, import_files=True,
                          through=0, caller_scope="t", request_id="r", timeout=5),
    # team-server-012 D1: plain path sync and the sync job are operator-only too.
    "sync:plain": dict(files=None, take_file=False, import_files=True, through=0, caller_scope="t",
                       request_id="r-plain", timeout=5),
    "sync_job": dict(sync_id=None, files=None, wait_seconds=0),
}


def args_for(key, root_):
    """(method, arguments) for an ARGS key; "<root>" becomes the store root."""
    arguments = {k: (v.replace("<root>", str(root_)) if isinstance(v, str) else v) for k, v in ARGS[key].items()}
    return key.split(":")[0], arguments


@pytest.fixture
def owner(root):  # noqa: F811
    started = Coordinator(root).start()
    yield started
    started.close()


@pytest.fixture
def sent(monkeypatch):
    """Every `_send` as (method, arguments), before the real `_send` (and its gate) runs."""
    calls = []
    real = Client._send

    def spy(self, record, method, **arguments):
        calls.append((method, arguments))
        return real(self, record, method, **arguments)
    monkeypatch.setattr(Client, "_send", spy)
    return calls


@pytest.fixture
def received(owner, monkeypatch):
    """The methods the coordinator actually received."""
    methods = []
    real = owner.dispatch

    def recorder(message):
        methods.append(message.get("method") if isinstance(message, dict) else None)
        return real(message)
    monkeypatch.setattr(owner, "dispatch", recorder)
    return methods


def provide(monkeypatch, value):
    monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", value)


def test_origin_travels_on_every_method_with_a_provider(root, owner, sent, monkeypatch):  # noqa: F811
    provide(monkeypatch, lambda: MEMBER)
    client = Client(root, autostart=False)
    client.status()
    client.flush(0)
    assert any(m == "status" and "origin" not in a for m, a in sent), "the probe's status carries no origin"
    assert any(m == "status" and a.get("origin") == {"principal": MEMBER} for m, a in sent)
    assert [a["origin"] for m, a in sent if m == "flush"] == [{"principal": MEMBER}]
    # `sync` is operator-only since team-server-012 D1: the operator's origin travels on it the same way.
    provide(monkeypatch, lambda: "operator")
    client.sync(files=None)
    assert [a["origin"] for m, a in sent if m == "sync"] == [{"principal": "operator"}]


def test_execute_retry_resends_the_same_origin(root, owner, monkeypatch):  # noqa: F811
    provide(monkeypatch, lambda: MEMBER)
    attempts = []
    real = Client._send

    def flaky(self, record, method, **arguments):
        if method == "execute":
            attempts.append(arguments.get("origin"))
            if len(attempts) == 1:
                raise ConnectionResetError("first execute lost")
        return real(self, record, method, **arguments)
    monkeypatch.setattr(Client, "_send", flaky)
    client = Client(root, autostart=False)
    assert client.execute(request(client, key="retry-1"))["receipt"]["affected"]
    assert attempts == [{"principal": MEMBER}, {"principal": MEMBER}]


def test_no_provider_sends_what_it_sent_before(root, owner, sent, monkeypatch):  # noqa: F811
    provide(monkeypatch, None)
    client = Client(root, autostart=False)
    client.execute(request(client, key="plain-1"))
    client.sync(files=None)
    client.status()
    client.git_status()
    assert all("origin" not in a for _, a in sent)
    assert set(next(a for m, a in sent if m == "execute")) - {"wait"} == {"envelope", "visibility"}


def test_a_caller_supplied_origin_is_refused_in_the_daemon(root, owner, received, monkeypatch):  # noqa: F811
    provide(monkeypatch, lambda: MEMBER)
    client = Client(root, autostart=False)
    with pytest.raises(ValueError, match="never by a caller"):
        client.call("execute", envelope=request(client, key="forged"), visibility="native",
                    origin={"principal": "operator"})
    assert "execute" not in received
    provide(monkeypatch, None)
    answer = client.call("execute", envelope=request(client, key="forged-2"), visibility="native",
                         origin={"principal": MEMBER})
    assert answer["receipt"]["affected"]


@pytest.mark.parametrize("key", sorted(ARGS))
@pytest.mark.parametrize("who", ["member", "unknown"])
def test_admin_methods_are_refused_in_the_daemon_without_the_operator(root, owner, received, monkeypatch,  # noqa: F811
                                                                       key, who):
    provide(monkeypatch, (lambda: MEMBER) if who == "member" else (lambda: None))
    method, arguments = args_for(key, root)
    with pytest.raises(ValueError, match="host operator action"):
        Client(root, autostart=False).call(method, **arguments)
    assert method not in received


def test_remote_methods_pass_without_a_principal(root, owner, monkeypatch):  # noqa: F811
    provide(monkeypatch, lambda: None)
    client = Client(root, autostart=False)
    assert client.status()["nonce"] == owner.nonce
    client.flush(0)
    assert client.execute(request(client, key="none-1"))["receipt"]["affected"]


def test_sync_without_a_principal_is_refused(root, owner, received, monkeypatch):  # noqa: F811
    """team-server-012 D1 with 005 D4: a daemon thread that lost its request cannot sync host files."""
    provide(monkeypatch, lambda: None)
    client = Client(root, autostart=False)
    with pytest.raises(ValueError) as caught:
        client.sync(files=None)
    assert str(caught.value) == allowlist.HOST_SYNC_REFUSED
    with pytest.raises(ValueError, match="host operator action"):
        client.sync_job(deadline=time.monotonic() + 30)
    assert "sync" not in received and "sync_job" not in received


@pytest.mark.parametrize("key", sorted(ARGS))
def test_the_operator_passes_the_client_gate(root, owner, received, monkeypatch, key):  # noqa: F811
    provide(monkeypatch, lambda: "operator")
    method, arguments = args_for(key, root)
    try:
        Client(root, autostart=False).call(method, **arguments)
    except Exception as exc:  # noqa: BLE001 - only the gate's refusal matters here
        assert "host operator action" not in str(exc)
    assert method in received


def test_sync_job_resolves_the_origin_in_the_calling_thread(root, owner, sent, monkeypatch):  # noqa: F811
    main = threading.get_ident()
    # The operator, since `sync_job` is operator-only (team-server-012 D1); the threading rule is the same.
    provide(monkeypatch, lambda: "operator" if threading.get_ident() == main else None)
    Client(root, autostart=False).sync_job(deadline=time.monotonic() + 30)
    assert [a.get("origin") for m, a in sent if m == "sync_job"][:1] == [{"principal": "operator"}]


def test_a_raising_provider_sends_nothing(root, owner, received, monkeypatch):  # noqa: F811
    def refuse():
        raise PermissionError("authenticated principal missing")
    provide(monkeypatch, refuse)
    with pytest.raises(PermissionError):
        Client(root, autostart=False).status()
    assert received == []


def test_served_principal(monkeypatch):
    provide(monkeypatch, None)
    assert client_module.served_principal() == (False, None) and client_module.serving() is False
    provide(monkeypatch, lambda: MEMBER)
    assert client_module.served_principal() == (True, MEMBER)
    provide(monkeypatch, lambda: None)
    assert client_module.served_principal() == (True, None)

    def never():
        raise AssertionError("serving() must not call the provider")
    provide(monkeypatch, never)
    assert client_module.serving() is True


def test_client_refusal_is_logged_without_arguments(root, owner, monkeypatch, caplog):  # noqa: F811
    provide(monkeypatch, lambda: MEMBER)
    caplog.set_level("WARNING", logger="taskmaster.coordinator")
    with pytest.raises(ValueError):
        Client(root, autostart=False).git_run(kind="not-a-kind", message="SECRET-MSG")
    assert f"refused git_run in the serve process for key {MEMBER}" in caplog.text
    assert "SECRET-MSG" not in caplog.text


# ── Step 2: the serve coordinator ────────────────────────────────────────────

from contextlib import closing  # noqa: E402

import yaml  # noqa: E402

from taskmaster.coordinator import protocol  # noqa: E402
from taskmaster.serve.daemon import SERVE_RETIRE_REFUSED, SERVE_SHUTDOWN_REFUSED  # noqa: E402
from test_serve_coordinator import FUTURE, serve_owner  # noqa: E402,F401

LINEAR_ENTRY = {"alias": "ops", "team_id": "T2", "token_env": "TASKMASTER_LINEAR_TOKEN"}


def high_water(root_):
    with closing(protocol.connect(root_, readonly=True)) as connection:
        return connection.execute("SELECT COALESCE(MAX(seq),0) FROM domain_events").fetchone()[0]


@pytest.mark.parametrize("key", sorted(ARGS))
def test_serve_coordinator_refuses_admin_methods_to_a_member(root, serve_owner, key):  # noqa: F811
    serve_owner(root)
    before = high_water(root)
    method, arguments = args_for(key, root)
    with pytest.raises(ValueError, match="host operator action"):
        Client(root, autostart=False).call(method, origin={"principal": MEMBER}, **arguments)
    assert not (root / ".taskmaster/linear.yaml").exists()
    assert high_water(root) == before


@pytest.mark.parametrize("origin", [None, {"principal": "operator"}])
@pytest.mark.parametrize("key", sorted(ARGS))
def test_a_host_process_is_the_operator(root, serve_owner, key, origin):  # noqa: F811
    serve_owner(root)
    method, arguments = args_for(key, root)
    extra = {} if origin is None else {"origin": origin}
    try:
        Client(root, autostart=False).call(method, **extra, **arguments)
    except Exception as exc:  # noqa: BLE001 - the method's own validation may refuse; the gate must not
        assert "host operator action" not in str(exc)


def test_remote_methods_pass_for_a_member(root, serve_owner):  # noqa: F811
    owner = serve_owner(root)
    client = Client(root, autostart=False)
    origin = {"principal": "operator"}
    assert client.call("status", origin=origin)["nonce"] == owner.nonce
    member = {"origin": {"principal": MEMBER}}
    assert client.call("status", **member)["nonce"] == owner.nonce
    client.call("flush", through=0, **member)
    sync = dict(files=None, take_file=False, import_files=True, through=0, caller_scope="t", timeout=30)
    # team-server-012 D1: plain sync is the operator's; the serve coordinator refuses it to a member.
    with pytest.raises(ValueError) as caught:
        client.call("sync", request_id="m1", **sync, **member)
    assert str(caught.value) == allowlist.HOST_SYNC_REFUSED
    # team-server-004 D2: the bootstrap operator over MCP names its own session, like a member.
    client.call("sync", request_id="o1", **dict(sync, caller_scope="operator@" + "0" * 16), origin=origin)


def test_bootstrap_token_name_is_fixed_on_a_server(root, serve_owner):  # noqa: F811
    serve_owner(root)
    client = Client(root, autostart=False)
    config = root / ".taskmaster/linear.yaml"
    for entry in (dict(LINEAR_ENTRY, token_env="OPS_LINEAR_TOKEN"), None):
        with pytest.raises(ValueError) as caught:
            client.call("linear_bootstrap", entry=entry, default_workspace=False)
        assert str(caught.value) == allowlist.LINEAR_TOKEN_ENV_REFUSED
        assert not config.exists()
    answer = client.call("linear_bootstrap", entry=dict(LINEAR_ENTRY), default_workspace=False)
    assert answer["ok"] is True
    workspaces = yaml.safe_load(config.read_text(encoding="utf-8"))["workspaces"]
    assert [ws["token_env"] for ws in workspaces] == ["TASKMASTER_LINEAR_TOKEN"]


def test_invalid_origin_is_refused_on_any_method(root, serve_owner):  # noqa: F811
    serve_owner(root)
    with pytest.raises(ValueError, match="invalid origin"):
        Client(root, autostart=False).call("status", origin={"principal": "../x"})


def test_shutdown_and_retire_keep_001s_refusals(root, serve_owner):  # noqa: F811
    serve_owner(root)
    client = Client(root, autostart=False)
    with pytest.raises(ValueError) as caught:
        client.call("shutdown", origin={"principal": MEMBER})
    assert str(caught.value) == SERVE_SHUTDOWN_REFUSED
    record = client._discovery()
    answer = client._send(record, "retire", origin={"principal": MEMBER})
    assert answer["state"] == "refused" and answer["reason"] == SERVE_RETIRE_REFUSED
    client.build = FUTURE
    answer = client._send(record, "retire", origin={"principal": MEMBER})
    assert answer["state"] == "refused" and answer["reason"] == SERVE_RETIRE_REFUSED


def test_session_coordinator_has_no_gate(root, owner):  # noqa: F811
    try:
        Client(root, autostart=False).call("git_status", origin={"principal": MEMBER})
    except Exception as exc:  # noqa: BLE001 - only the gate's refusal matters here
        assert "host operator action" not in str(exc)


def test_coordinator_refusal_is_logged(root, serve_owner, caplog):  # noqa: F811
    serve_owner(root)
    caplog.set_level("WARNING", logger="taskmaster.serve")
    with pytest.raises(ValueError):
        Client(root, autostart=False).call("git_status", origin={"principal": MEMBER})
    assert f"refused git_status for key {MEMBER}" in caplog.text


# ── Step 4: the operator principal is re-checked as host-local ───────────────

from starlette.requests import Request  # noqa: E402

from taskmaster.serve import auth  # noqa: E402


def _scope(client, host):
    return {"type": "http", "method": "POST", "path": "/mcp", "headers": [(b"host", host.encode())],
            "client": client, "server": ("127.0.0.1", 7470), "scheme": "http", "query_string": b""}


@pytest.fixture
def in_request(monkeypatch):
    import fastmcp.server.dependencies as dependencies

    def enter(principal, client, host):
        monkeypatch.setattr(dependencies, "get_http_request", lambda: Request(_scope(client, host)))
        monkeypatch.setattr(auth, "current_principal", lambda: principal)
    return enter


def test_operator_key_id_requires_a_host_local_request(in_request):
    in_request(auth.OPERATOR, ("203.0.113.5", 4000), "tm.example.com")
    with pytest.raises(PermissionError, match="works only on the host"):
        auth.current_key_id()
    in_request(auth.OPERATOR, ("127.0.0.1", 4000), "127.0.0.1:7470")
    assert auth.current_key_id() == "operator"


def test_member_key_id_needs_no_host_check(in_request):
    in_request(auth.Principal("ann", "member", MEMBER), ("203.0.113.5", 4000), "tm.example.com")
    assert auth.current_key_id() == MEMBER


def test_outside_a_request_is_none(monkeypatch):
    import fastmcp.server.dependencies as dependencies

    def outside():
        raise RuntimeError("no active HTTP request")
    monkeypatch.setattr(dependencies, "get_http_request", outside)
    assert auth.current_key_id() is None


# ── gate finding M4 ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("method", [["git_run"], {"m": 1}, {"git_run"}, 5])
def test_a_non_string_method_is_refused_cleanly(method):
    assert allowlist.UNKNOWN_WHAT in allowlist.refusal(method, {}, MEMBER)
    assert allowlist.UNKNOWN_WHAT in allowlist.refusal(method, {}, None)
    assert allowlist.refusal(method, {}, "operator") is None          # then "unsupported coordinator method"
    assert allowlist.log_method(method) == "other"
