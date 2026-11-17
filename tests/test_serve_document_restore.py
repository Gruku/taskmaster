# User intent: on a team server an uploaded document is credited to the person who sent it, team mode refuses an upload from anyone who is not a person, and only the host operator can replay 013's backup through the restore path, which keeps the original uploader (team-server-012 D4, M5, D10).
from __future__ import annotations

import pytest

from taskmaster.coordinator import allowlist
from taskmaster.coordinator import client as client_module
from taskmaster.coordinator.actor_resolver import LOCAL, Origin
from taskmaster.coordinator.client import Client
from taskmaster.native import documents as native_documents
from taskmaster.serve.principals import add_user
from taskmaster.team_identity import IdentityRefused
from native_twins import native_connection
from test_native_service_build import root  # noqa: F401  (fixture)
from test_serve_identity import ANN_SCOPE, _value, no_host_identity, scope_of, team_on, world  # noqa: F401  (fixtures)

TASK = "test-epic-001"


@pytest.fixture
def documented(world):  # noqa: F811
    from taskmaster.native.commands import _write_field  # test-only: the task declares two documents
    with native_connection(world.root) as connection:
        key = connection.execute("SELECT entity_key FROM entity_core WHERE kind='task' AND public_id=?",
                                 (TASK,)).fetchone()[0]
        _write_field(connection, key, "task", "docs", {"spec": "docs/spec.md", "plan": "docs/plan.md"})
    return world


def _import(world, key, section="spec", body="Uploaded prose.\n", **extra):
    arguments = {"kind": "task", "id": TASK, "section": section, "path": f"docs/{section}.md", "body": body, **extra}
    return {"protocol": 2, "store_id": world.owner.identity["store_id"], "caller_scope": ANN_SCOPE,
            "request_id": key, "operation": "document.import", "arguments": arguments, "expected_revisions": []}


def _restore(world, key, **extra):
    return _import(world, key, restore=True, **{"source": "upload", "imported_by": "ann", **extra})


# Served commands run in their key's own session (team-server-003 D5): the scope names the principal's handle.
def _scoped(world, value, principal, scope=None):
    if principal is None:
        return value
    return {**value, "caller_scope": scope or scope_of(world, principal)}


def _submit(world, value, origin=LOCAL):
    return world.owner.submit(_scoped(world, value, origin.principal), origin=origin).result(30)


def _call(world, value, principal, scope=None):
    return Client(world.root, autostart=False).call("execute", envelope=_scoped(world, value, principal, scope),
                                                    visibility="native", origin={"principal": principal})


def _rows(root_):
    return _value(root_, "SELECT section, body FROM external_documents ORDER BY section")


def _provenance(root_, section="spec"):
    from taskmaster.native.queries import Repository
    with native_connection(root_) as connection, Repository(connection).snapshot() as snapshot:
        return native_documents.retrieve(snapshot, "task", TASK, sections=[section])["provenance"][section]


def _origin(root_, section="spec"):
    with native_connection(root_) as connection:
        seq = connection.execute("SELECT imported_seq FROM external_documents WHERE section=?", (section,)).fetchone()[0]
        return native_documents.import_origin(connection, seq)


# ── M5: an upload needs a person in team mode; a file import does not ────────


def test_a_members_upload_is_credited_to_the_member(documented, no_host_identity):
    receipt = _submit(documented, _import(documented, "u1", source="upload"), Origin(principal=documented.ann))
    assert receipt["actor"]["handle"] == "ann"
    facts = _provenance(documented.root)
    assert facts["origin"] == "upload" and facts["imported_by"] == "ann"      # the first write upgraded to schema 5


def test_the_operator_cannot_upload_in_team_mode(documented, no_host_identity):
    for origin in (LOCAL, Origin(principal="operator")):
        with pytest.raises(IdentityRefused, match="add-user"):
            _submit(documented, _import(documented, f"o-{origin.principal}", source="upload"), origin)
    assert _rows(documented.root) == []


def test_a_deleted_profile_cannot_upload(documented, no_host_identity):
    _submit(documented, _import(documented, "d1", source="upload"), Origin(principal=documented.ann))
    with native_connection(documented.root) as connection:
        connection.execute("UPDATE entity_core SET deleted=1 WHERE kind='profile' AND public_id='ann'")
    with pytest.raises(IdentityRefused, match="was deleted"):
        _submit(documented, _import(documented, "d2", body="Other.\n", source="upload"), Origin(principal=documented.ann))


def test_the_operators_file_import_still_works_in_team_mode(documented, no_host_identity):
    receipt = _submit(documented, _import(documented, "f1"))
    assert receipt["actor"]["handle"] == "operator"
    assert "origin" not in _provenance(documented.root)


# ── D10: the restore path ────────────────────────────────────────────────────


def test_a_member_cannot_restore(documented, no_host_identity):
    _, ops, _ = add_user(documented.root, "ops", role="operator")
    for key_id, scope in ((documented.ann, None), (ops.key_id, "ops@" + "0" * 16)):
        with pytest.raises(ValueError) as caught:
            _call(documented, _restore(documented, f"r-{key_id}"), key_id, scope)
        assert str(caught.value) == allowlist.RESTORE_REFUSED
        assert "add-user" not in str(caught.value)                            # never reached the identity gate
    assert _rows(documented.root) == []


def test_the_serve_client_refuses_a_members_restore_before_sending(documented, monkeypatch):
    received = []
    real = documented.owner.dispatch

    def recorder(message):
        received.append(message.get("method") if isinstance(message, dict) else None)
        return real(message)
    monkeypatch.setattr(documented.owner, "dispatch", recorder)
    for principal in (documented.ann, None):
        monkeypatch.setattr(client_module, "ORIGIN_PROVIDER", lambda principal=principal: principal)
        with pytest.raises(ValueError, match="document.import restore|no authenticated key"):
            Client(documented.root, autostart=False).execute(_restore(documented, f"c-{principal}"))
    assert "execute" not in received
    assert _rows(documented.root) == []


@pytest.mark.parametrize("how", ["host process", "operator key"])
def test_the_operator_restores_an_upload_with_the_original_uploader(documented, no_host_identity, how):
    value = _restore(documented, "rs1", original_seq=812)
    if how == "host process":
        receipt = _submit(documented, value)                                 # 013's restore-documents: LOCAL
    else:
        receipt = _call(documented, value, "operator")["receipt"]
    assert receipt["actor"]["handle"] == "operator"
    facts = _provenance(documented.root)
    assert facts["origin"] == "restore" and facts["restored_origin"] == "upload"
    assert facts["imported_by"] == "ann" and facts["original_seq"] == 812
    origin = _origin(documented.root)
    assert (origin["source"], origin["imported_by"]) == ("upload", "ann")


def test_a_file_import_restores_too(documented, no_host_identity):
    _submit(documented, _restore(documented, "rf1", source="file", imported_by="bob"))
    facts = _provenance(documented.root)
    assert facts["restored_origin"] == "file" and facts["imported_by"] == "bob"
    assert (_origin(documented.root)["source"], _origin(documented.root)["imported_by"]) == ("file", "bob")


def test_restoring_twice_is_a_no_op(documented, no_host_identity):
    first = _submit(documented, _restore(documented, "tw1"))
    again = _submit(documented, _restore(documented, "tw2"))
    assert first["affected"] and again["affected"] == []


@pytest.mark.parametrize("source, handle", [("upload", "ann"), ("file", "bob")])
def test_a_second_backup_reproduces_the_same_values(documented, no_host_identity, source, handle):
    """Back up a restored copy (read the helper 013 uses) and restore it again: the pair survives unchanged."""
    _submit(documented, _restore(documented, "b1", source=source, imported_by=handle))
    backed_up = _origin(documented.root)
    _submit(documented, _restore(documented, "b2", section="plan", source=backed_up["source"],
                                 imported_by=backed_up["imported_by"]))
    again = _origin(documented.root, "plan")
    assert (again["source"], again["imported_by"]) == (source, handle)
    spec, plan = _value(documented.root, "SELECT content_hash FROM external_documents ORDER BY section DESC")
    assert spec == plan                                                          # same body, same sha256


# ── Codex review finding 2: a member's import without source="upload" ──────


def test_a_members_raw_import_without_source_is_refused(documented, no_host_identity):
    for extra in ({}, {"source": "file"}):
        with pytest.raises(ValueError) as caught:
            _call(documented, _import(documented, f"raw-{len(extra)}", **extra), documented.ann)
        assert str(caught.value) == allowlist.HOST_IMPORT_REFUSED
    assert _rows(documented.root) == []


@pytest.mark.parametrize("how", ["host process", "operator key"])
def test_the_operators_raw_import_without_source_is_accepted(documented, no_host_identity, how):
    value = _import(documented, f"op-{how}")
    if how == "host process":
        receipt = _submit(documented, value)
    else:
        receipt = _call(documented, value, "operator")["receipt"]
    assert receipt["affected"] and receipt["actor"]["handle"] == "operator"
    assert "origin" not in _provenance(documented.root)


def test_the_identity_gate_needs_a_person_for_any_member_import(documented, no_host_identity):
    """Past the allowlist (an in-process submit), a member's import without source still needs a live profile."""
    _submit(documented, _import(documented, "first", source="upload"), Origin(principal=documented.ann))
    with native_connection(documented.root) as connection:
        connection.execute("UPDATE entity_core SET deleted=1 WHERE kind='profile' AND public_id='ann'")
    for extra in ({}, {"source": "upload"}):
        with pytest.raises(IdentityRefused, match="was deleted"):
            _submit(documented, _import(documented, f"del-{len(extra)}", body="Other.\n", **extra),
                    Origin(principal=documented.ann))
    assert _rows(documented.root) == [("spec", "Uploaded prose.\n")]


def test_a_members_first_import_without_source_creates_the_profile(documented, no_host_identity):
    _submit(documented, _import(documented, "np1"), Origin(principal=documented.ann))
    assert _value(documented.root, "SELECT public_id FROM entity_core WHERE kind='profile'") == [("ann",)]


def test_a_batch_cannot_smuggle_a_restore_past_the_gate(documented):
    restore = _restore(documented, "inner")
    batch = {"protocol": 2, "store_id": documented.owner.identity["store_id"], "caller_scope": ANN_SCOPE,
             "request_id": "batch-1", "operation": "batch", "expected_revisions": [],
             "arguments": {"commands": [{"operation": "document.import", "arguments": restore["arguments"]}]}}
    with pytest.raises(ValueError) as caught:
        _call(documented, batch, documented.ann)
    assert str(caught.value) == allowlist.RESTORE_REFUSED
    assert _rows(documented.root) == []
