# User intent: the per-clone table keeps each person's branch, worktree and merge records apart, reads as empty where
# it was never created, stays bounded, and the command envelope carries a clone id only when the bound session has one
# (team-server-011 D2, D3, Step 3).
from __future__ import annotations

import sqlite3

import pytest

from native_twins import make_twins
from taskmaster import backlog_server as bs
from taskmaster.native import clone_fields, commands, contracts

CLONE_A, CLONE_B = "c-" + "a" * 16, "c-" + "b" * 16


@pytest.fixture
def db():
    connection = sqlite3.connect(":memory:")
    yield connection
    connection.close()


def test_ensure_is_idempotent(db):
    clone_fields.ensure(db)
    clone_fields.ensure(db)
    assert clone_fields.exists(db)


def test_reads_on_a_store_without_the_table_are_empty_and_create_nothing(db):
    assert clone_fields.rows_for(db, "task", "t-1") == []
    assert clone_fields.own(db, "ann", CLONE_A, "task", "t-1") == {}
    assert clone_fields.branch_matches(db, "x") == []
    assert clone_fields.branch_rows(db) == []
    assert not clone_fields.exists(db)


def test_put_own_and_rows_for(db):
    clone_fields.put(db, "ann", "", "task", "t-1", "branch", "default-b", 1)
    clone_fields.put(db, "ann", CLONE_A, "task", "t-1", "branch", "clone-b", 2)
    clone_fields.put(db, "bob", CLONE_B, "task", "t-1", "worktree", ".worktrees/b", 3)
    assert clone_fields.own(db, "ann", CLONE_A, "task", "t-1") == {"branch": "clone-b"}      # the clone row wins
    assert clone_fields.own(db, "ann", CLONE_B, "task", "t-1") == {"branch": "default-b"}    # falls back to the default
    assert clone_fields.own(db, "ann", "", "task", "t-1") == {"branch": "default-b"}
    assert clone_fields.own(db, "carol", CLONE_A, "task", "t-1") == {}
    assert [(r["handle"], r["clone"], r["field"]) for r in clone_fields.rows_for(db, "task", "t-1")] == [
        ("ann", "", "branch"), ("ann", CLONE_A, "branch"), ("bob", CLONE_B, "worktree")]


def test_none_deletes_only_that_row(db):
    clone_fields.put(db, "ann", CLONE_A, "task", "t-1", "branch", "x", 1)
    clone_fields.put(db, "ann", CLONE_A, "task", "t-1", "worktree", "w", 1)
    clone_fields.put(db, "ann", CLONE_A, "task", "t-1", "branch", None, 2)
    assert clone_fields.own(db, "ann", CLONE_A, "task", "t-1") == {"worktree": "w"}


def test_branch_matches_uses_the_partial_index(db):
    clone_fields.put(db, "ann", CLONE_A, "task", "t-1", "branch", "fix/login", 1)
    clone_fields.put(db, "bob", "", "task", "t-2", "branch", "fix/login", 1)
    clone_fields.put(db, "bob", "", "task", "t-2", "worktree", "fix/login", 1)
    assert clone_fields.branch_matches(db, "fix/login") == [("ann", CLONE_A, "t-1"), ("bob", "", "t-2")]
    plan = " ".join(str(row) for row in db.execute(
        "EXPLAIN QUERY PLAN SELECT handle,clone,id FROM clone_fields WHERE field='branch' AND "
        "json_extract(value_json,'$')=? AND kind='task' ORDER BY id,handle,clone", ("fix/login",)))
    assert "ix_clone_fields_branch" in plan


def test_a_value_over_4_KiB_is_refused(db):
    with pytest.raises(ValueError):
        clone_fields.put(db, "ann", "", "task", "t-1", "branch", "x" * 5000, 1)


def test_the_65th_clone_of_a_handle_goes_to_the_default_row(db):
    for i in range(64):
        assert clone_fields.put(db, "ann", f"c-{i:016x}", "task", "t-1", "branch", f"b{i}", 1) == f"c-{i:016x}"
    assert clone_fields.put(db, "ann", CLONE_B, "task", "t-1", "branch", "overflow", 2) == ""
    assert clone_fields.own(db, "ann", "", "task", "t-1") == {"branch": "overflow"}
    assert clone_fields.put(db, "ann", f"c-{3:016x}", "task", "t-1", "branch", "again", 3) == f"c-{3:016x}"
    assert clone_fields.put(db, "bob", CLONE_B, "task", "t-1", "branch", "bob-ok", 4) == CLONE_B    # per handle


def test_a_clone_holds_at_most_1000_rows(db):
    for i in range(1000):
        clone_fields.put(db, "ann", CLONE_A, "task", f"t-{i}", "branch", "x", 1)
    with pytest.raises(ValueError):
        clone_fields.put(db, "ann", CLONE_A, "task", "t-1000", "branch", "x", 1)
    clone_fields.put(db, "ann", CLONE_A, "task", "t-0", "branch", "y", 2)           # an existing row still updates


def test_overlay_shows_the_callers_own_values_only(db):
    clone_fields.put(db, "ann", CLONE_A, "task", "t-1", "branch", "mine", 1)
    clone_fields.put(db, "ann", CLONE_A, "task", "t-1", "worktree", ".worktrees/a", 1)
    doc = {"id": "t-1", "branch": "shared", "worktree": "shared-w", "title": "T"}
    assert clone_fields.overlay(db, doc, "ann", CLONE_A) == {"id": "t-1", "branch": "mine",
                                                            "worktree": ".worktrees/a", "title": "T"}
    assert clone_fields.overlay(db, doc, "bob", CLONE_A) is doc
    assert doc["branch"] == "shared"


# ── the envelope ────────────────────────────────────────────────


def _envelope(**more):
    return {"protocol": 2, "store_id": "s", "caller_scope": "c", "request_id": "r", "operation": "task.patch",
            "arguments": {"id": "t-1", "set": {"priority": "high"}}, "expected_revisions": [], **more}


def test_validate_accepts_a_well_formed_clone_and_hashes_it():
    plain, plain_hash = contracts.validate(_envelope())
    request, fingerprint = contracts.validate(_envelope(clone=CLONE_A))
    assert request["clone"] == CLONE_A and fingerprint != plain_hash
    assert "clone" not in plain


@pytest.mark.parametrize("bad", ["", "c-xyz", "C-" + "a" * 16, "c-" + "a" * 17, 5, None, "c-" + "a" * 16 + "\n"])
def test_validate_refuses_a_malformed_clone(bad):
    with pytest.raises(ValueError):
        contracts.validate(_envelope(clone=bad))


def test_an_envelope_without_clone_has_the_fingerprint_it_always_had():
    _request, fingerprint = contracts.validate(_envelope())
    import hashlib
    from taskmaster.native.migrate import encode
    expected = hashlib.sha256(encode({k: v for k, v in contracts.validate(_envelope())[0].items()
                                      if k not in contracts.UNHASHED}).encode()).hexdigest()
    assert fingerprint == expected


# ── the adapter ─────────────────────────────────────────────────


class _Bound:
    def __init__(self, clone=None):
        self.session = bs.SESSION_ID
        if clone is not None:
            self.clone = clone


@pytest.fixture
def seen(tmp_path, monkeypatch):
    def seed():
        bs.backlog_add_task(title="One", epic="test-epic", phase="dev")
    twins = make_twins(tmp_path, monkeypatch, seed)
    envelopes = []
    real = commands.execute

    def spy(connection, envelope, **kwargs):
        envelopes.append(envelope)
        return real(connection, envelope, **kwargs)
    monkeypatch.setattr(commands, "execute", spy)
    twins.envelopes = envelopes
    return twins


@pytest.mark.parametrize("bound, expected", [(_Bound(CLONE_A), CLONE_A), (_Bound(""), None), (_Bound(), None), (None, None)])
def test_the_adapter_puts_the_bound_sessions_clone_in_the_envelope(seen, monkeypatch, bound, expected):
    monkeypatch.setattr(bs, "_bound_session", lambda: bound)
    seen.call("backlog_update_task", task_id="test-epic-001", field="priority", value="high")
    assert seen.envelopes, "no command reached the coordinator"
    assert seen.envelopes[-1].get("clone") == expected


def test_solo_and_host_local_writes_create_no_table_and_no_rows(tmp_path, monkeypatch):
    """No requester (solo, host-local): a branch update, a merge stamp and a task create stay shared writes."""
    def seed():
        bs.backlog_add_task(title="One", epic="test-epic", phase="dev")
    twins = make_twins(tmp_path, monkeypatch, seed)
    twins.same("backlog_update_task", task_id="test-epic-001", field="branch", value="fix/x")
    twins.same("backlog_update_task", task_id="test-epic-001", field="worktree", value=".worktrees/x")
    twins.same("backlog_record_merge", task_id="test-epic-001", rung="stage", sha="a" * 40)
    from contextlib import closing
    from taskmaster.coordinator import protocol
    with closing(protocol.connect(twins.native, readonly=True)) as connection:
        assert not clone_fields.exists(connection)
    from native_twins import committed
    doc = committed(twins.native)[("task", "test-epic-001")][0]
    assert doc["branch"] == "fix/x" and doc["worktree"] == ".worktrees/x"
    assert doc["merge_status"]["stage"]["merge_commit"] == "a" * 40
