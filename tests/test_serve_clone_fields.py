# User intent: on a team server a member's branch, worktree and merge records land in their own clone rows, other
# people's views of the shared task stay as they were, the overlay shows each caller their own, the operator and solo
# keep shared writes, and branch rows survive a restore (team-server-011 D4-D7, D10).
from __future__ import annotations

import asyncio
import json
import logging
from contextlib import closing

import pytest

from taskmaster import backlog_server as bs
from taskmaster.coordinator import protocol
from taskmaster.native import clone_fields
from test_serve_read_api import root, serve  # noqa: F401  (fixtures)

CLONE_A, CLONE_B = "c-" + "a" * 16, "c-" + "b" * 16
T1, T2, T3 = "test-epic-001", "test-epic-002", "test-epic-003"


def call(world, who, tool, args=None, clone=None, headers=None):
    """One MCP call as `who`, on a fresh connection that names its clone through the header."""
    from fastmcp import Client
    from fastmcp.client.transports import StreamableHttpTransport
    sent = dict(headers or {})
    if clone is not None:
        sent["X-Taskmaster-Clone"] = clone

    async def go():
        transport = StreamableHttpTransport(world.url, headers=sent, auth=world.keys[who])
        async with Client(transport) as client:
            return (await client.call_tool(tool, args or {})).content[0].text
    return asyncio.run(go())


def sql(world, query, *args):
    with closing(protocol.connect(world.root, readonly=True)) as connection:
        return connection.execute(query, args).fetchall()


def shared_branch(world, ident):
    return sql(world, "SELECT t.branch_json FROM task_operational t JOIN entity_core c ON c.entity_key=t.entity_key "
                      "WHERE c.public_id=?", ident)[0][0]


def fields(world, ident):
    from taskmaster.native_routing import hook_reads
    with closing(protocol.connect(world.root, readonly=True)) as connection:
        connection.execute("BEGIN")
        return hook_reads._snapshot(connection, hook_reads.admit(connection)).get("task", ident)["fields"]


def events(world, ident):
    return sql(world, "SELECT COUNT(*) FROM domain_events WHERE kind='task' AND id=?", ident)[0][0]


def revision(world, ident):
    return sql(world, "SELECT revision FROM entity_core WHERE kind='task' AND public_id=?", ident)[0][0]


def rows(world, ident):
    if not sql(world, "SELECT 1 FROM sqlite_schema WHERE name='clone_fields'"):
        return []
    return sql(world, "SELECT handle,clone,field,value_json FROM clone_fields WHERE id=? ORDER BY handle,clone,field",
               ident)


# ── Step 5: diverting writes ────────────────────────────────────


def test_a_members_branch_update_leaves_the_task_alone_and_writes_the_clone_row(serve):
    world = serve()
    before = (shared_branch(world, T1), events(world, T1), revision(world, T1))
    reply = call(world, "ann", "backlog_update_task", {"task_id": T1, "field": "branch", "value": "fix/mine"}, CLONE_A)
    assert "branch recorded for your clone (c-aaaa" in reply and "teammates keep their own" in reply, reply
    assert (shared_branch(world, T1), events(world, T1), revision(world, T1)) == before
    assert rows(world, T1) == [("ann", CLONE_A, "branch", json.dumps("fix/mine"))]


def test_clearing_deletes_only_that_row(serve):
    world = serve()
    call(world, "ann", "backlog_update_task", {"task_id": T1, "field": "branch", "value": "fix/mine"}, CLONE_A)
    call(world, "bob", "backlog_update_task", {"task_id": T1, "field": "branch", "value": "fix/bobs"}, CLONE_B)
    reply = call(world, "ann", "backlog_update_task", {"task_id": T1, "field": "branch", "value": ""}, CLONE_A)
    assert "cleared" in reply
    assert rows(world, T1) == [("bob", CLONE_B, "branch", json.dumps("fix/bobs"))]
    assert shared_branch(world, T1) == json.dumps("shared")


def test_without_the_header_the_default_row_is_written(serve):
    world = serve()
    reply = call(world, "ann", "backlog_update_task", {"task_id": T1, "field": "branch", "value": "fix/mine"})
    assert "your default clone" in reply
    assert rows(world, T1) == [("ann", "", "branch", json.dumps("fix/mine"))]


def test_a_malformed_header_is_ignored_and_logged(serve, caplog):
    world = serve()
    with caplog.at_level(logging.WARNING, logger="taskmaster.serve"):
        call(world, "ann", "backlog_update_task", {"task_id": T1, "field": "branch", "value": "fix/mine"},
             clone="c-not-a-clone")
    assert rows(world, T1)[0][1] == ""
    assert any("malformed X-Taskmaster-Clone" in record.message for record in caplog.records)


def test_the_batch_forms_divert_the_same_way(serve):
    world = serve()
    before = (shared_branch(world, T2), events(world, T2))
    call(world, "bob", "backlog_batch_update", {"operations": f"update {T2} branch fix/line"}, CLONE_B)
    call(world, "bob", "backlog_batch_update", {"commands": [
        {"operation": "task.update", "arguments": {"id": T2, "field": "worktree", "value": ".worktrees/b"}}]}, CLONE_B)
    assert (shared_branch(world, T2), events(world, T2)) == before
    assert rows(world, T2) == [("bob", CLONE_B, "branch", json.dumps("fix/line")),
                               ("bob", CLONE_B, "worktree", json.dumps(".worktrees/b"))]


def test_an_update_of_branch_plus_priority_writes_the_priority_and_diverts_the_branch(serve):
    world = serve()
    before = (events(world, T1), revision(world, T1))
    call(world, "ann", "backlog_batch_update", {"commands": [
        {"operation": "task.update", "arguments": {"id": T1, "field": "branch", "value": "fix/x"}},
        {"operation": "task.update", "arguments": {"id": T1, "field": "priority", "value": "high"}}]}, CLONE_A)
    assert events(world, T1) == before[0] + 1 and revision(world, T1) == before[1] + 1
    assert shared_branch(world, T1) == json.dumps("shared")
    assert rows(world, T1) == [("ann", CLONE_A, "branch", json.dumps("fix/x"))]
    assert fields(world, T1)["priority"] == "high"


def test_a_fully_diverted_update_leaves_the_revision_and_last_referenced_alone(serve):
    world = serve()
    before = (revision(world, T1), events(world, T1), fields(world, T1).get("last_referenced"))
    call(world, "ann", "backlog_update_task", {"task_id": T1, "field": "branch", "value": "fix/x"}, CLONE_A)
    call(world, "ann", "backlog_update_task", {"task_id": T1, "field": "worktree", "value": ".worktrees/a"}, CLONE_A)
    after = (revision(world, T1), events(world, T1), fields(world, T1).get("last_referenced"))
    assert after == before


def test_the_65th_clone_goes_to_the_default_row_and_the_reply_says_so(serve):
    world = serve()
    with closing(protocol.connect(world.root)) as connection:
        for i in range(64):
            clone_fields.put(connection, "ann", f"c-{i:016x}", "task", T3, "worktree", "w", 1)
        connection.commit()
    reply = call(world, "ann", "backlog_update_task", {"task_id": T1, "field": "branch", "value": "fix/z"}, CLONE_B)
    assert "64-clone limit" in reply
    assert ("ann", "", "branch", json.dumps("fix/z")) in rows(world, T1)


def test_a_member_cannot_write_another_persons_rows_by_naming_their_clone(serve):
    world = serve()
    call(world, "ann", "backlog_update_task", {"task_id": T1, "field": "branch", "value": "fix/ann"}, CLONE_A)
    call(world, "bob", "backlog_update_task", {"task_id": T1, "field": "branch", "value": "fix/bob"}, CLONE_A)
    assert sorted(rows(world, T1)) == [("ann", CLONE_A, "branch", json.dumps("fix/ann")),
                                       ("bob", CLONE_A, "branch", json.dumps("fix/bob"))]


def test_a_members_bundle_pick_writes_rows_for_every_member_and_leaves_the_shared_fields(serve):
    world = serve()
    ids = []
    for title in ("Alpha one", "Alpha two"):
        reply = call(world, "ann", "backlog_add_task", {"title": title, "epic": "test-epic", "phase": "dev",
                                                         "bundle": "alpha"}, CLONE_A)
        ids.append(reply.split("`")[1])
    reply = call(world, "ann", "backlog_pick_task", {"task_id": ids[0]}, CLONE_A)
    assert "bundle `alpha`" in reply
    for ident in ids:
        assert shared_branch(world, ident) in (json.dumps(None), None, "null")
        assert [r[:3] for r in rows(world, ident)] == [("ann", CLONE_A, "branch"), ("ann", CLONE_A, "worktree")]


# ── Step 6: the overlay ─────────────────────────────────────────


def test_a_pick_names_only_the_callers_own_worktree(serve):
    world = serve()
    call(world, "ann", "backlog_update_task", {"task_id": T1, "field": "worktree", "value": ".worktrees/a"}, CLONE_A)
    bobs = call(world, "bob", "backlog_pick_task", {"task_id": T1}, CLONE_B)
    assert ".worktrees/a" not in bobs and "Already recorded" not in bobs and "Picked" in bobs
    call(world, "ann", "backlog_update_task", {"task_id": T2, "field": "worktree", "value": ".worktrees/a2"}, CLONE_A)
    anns = call(world, "ann", "backlog_pick_task", {"task_id": T2}, CLONE_A)
    assert "Already recorded at `.worktrees/a2`" in anns


def test_get_task_shows_each_caller_their_own_branch_and_a_names_only_line(serve):
    world = serve()
    call(world, "ann", "backlog_update_task", {"task_id": T1, "field": "branch", "value": "fix/ann"}, CLONE_A)
    call(world, "ann", "backlog_update_task", {"task_id": T1, "field": "worktree", "value": ".worktrees/secret-a"}, CLONE_A)
    call(world, "bob", "backlog_update_task", {"task_id": T1, "field": "branch", "value": "fix/bob"}, CLONE_B)
    for verbose in (False, True):
        ann = call(world, "ann", "backlog_get_task", {"task_id": T1, "verbose": verbose}, CLONE_A)
        bob = call(world, "bob", "backlog_get_task", {"task_id": T1, "verbose": verbose}, CLONE_B)
        assert "fix/ann" in ann and "**branch:** fix/bob" not in ann and "**Branch:** fix/bob" not in ann
        assert "fix/bob" in bob
        for text in (ann, bob):
            assert "Branches by person:" in text and "ann: fix/ann" in text and "bob: fix/bob" in text
            assert ".worktrees/secret-a" not in text.split("Branches by person:")[1]
    assert ".worktrees/secret-a" not in call(world, "bob", "backlog_get_task", {"task_id": T1, "verbose": True}, CLONE_B)


def test_a_member_with_no_rows_sees_the_shared_values_and_no_extra_line(serve):
    world = serve()
    text = call(world, "carol", "backlog_get_task", {"task_id": T1})
    assert "**branch:** shared" in text and "Branches by person" not in text


# ── Step 7: per-clone merge records ─────────────────────────────

SHA_A, SHA_B = "a" * 40, "b" * 40


def merge_rows(world, ident):
    return [r for r in rows(world, ident) if r[2].startswith("merge:")]


def stamp(world, who, branch, sha, clone, **more):
    import httpx
    body = {"branch": branch, "target": "qa-line", "sha": sha, "merged_at": "2026-11-07T10:15", "clone": clone, **more}
    reply = httpx.post(f"{world.base}/write/v1/merge-stamp", json=body, timeout=60,
                       headers={"Authorization": f"Bearer {world.keys[who]}"})
    assert reply.status_code == 200, reply.text
    return reply.json()["data"]


def test_a_hook_stamp_writes_the_record_under_the_bodys_clone_and_the_shared_stamp_is_unchanged(serve):
    world = serve()
    assert stamp(world, "bob", "solo-b", SHA_A, CLONE_B)["outcome"] == "recorded"
    found = merge_rows(world, T3)
    assert [(r[0], r[1], r[2]) for r in found] == [("bob", CLONE_B, "merge:qa")]
    assert json.loads(found[0][3])["merge_commit"] == SHA_A
    assert fields(world, T3)["merge_status"]["qa"]["merge_commit"] == SHA_A


def test_a_stamp_the_cas_refuses_writes_no_record(serve, monkeypatch):
    world = serve()
    stamp(world, "bob", "solo-b", SHA_A, CLONE_B)
    again = stamp(world, "bob", "solo-b", SHA_B, CLONE_B)          # a different commit with no check: not recorded
    assert again["outcome"] == "check"
    assert json.loads(merge_rows(world, T3)[0][3])["merge_commit"] == SHA_A


def test_with_the_bound_reached_the_record_is_skipped_and_the_shared_stamp_lands(serve, monkeypatch, caplog):
    world = serve()
    monkeypatch.setattr(clone_fields, "MAX_ROWS_PER_CLONE", 0)
    with caplog.at_level(logging.WARNING):
        assert stamp(world, "bob", "solo-b", SHA_A, CLONE_B)["outcome"] == "recorded"
    assert merge_rows(world, T3) == []
    assert fields(world, T3)["merge_status"]["qa"]["merge_commit"] == SHA_A
    assert any("per-clone merge record" in record.message for record in caplog.records)


def test_get_task_shows_merges_from_your_clone(serve):
    world = serve()
    stamp(world, "bob", "solo-b", SHA_A, CLONE_B)
    mine = call(world, "bob", "backlog_get_task", {"task_id": T3}, CLONE_B)
    assert "Merges from your clone:** qa aaaaaaa (" in mine
    assert "Merges from your clone" not in call(world, "ann", "backlog_get_task", {"task_id": T3}, CLONE_A)


def test_a_merge_recorded_over_mcp_with_the_header_lands_under_that_clone(serve):
    world = serve()
    call(world, "bob", "backlog_record_merge", {"task_id": T3, "rung": "qa", "sha": SHA_A}, CLONE_B)
    assert [(r[0], r[1]) for r in merge_rows(world, T3)] == [("bob", CLONE_B)]


# ── review I1: a write equal to the shared value, or an empty one, deletes the caller's own row ──


def test_writing_the_shared_value_back_deletes_the_callers_row(serve):
    world = serve()
    call(world, "ann", "backlog_update_task", {"task_id": T1, "field": "branch", "value": "fix/mine"}, CLONE_A)
    assert rows(world, T1)
    reply = call(world, "ann", "backlog_update_task", {"task_id": T1, "field": "branch", "value": "shared"}, CLONE_A)
    assert "No change" not in reply, reply
    assert rows(world, T1) == []
    assert "fix/mine" not in call(world, "ann", "backlog_get_task", {"task_id": T1}, CLONE_A)


def test_clearing_when_the_shared_branch_is_empty_deletes_the_callers_row(serve):
    world = serve()
    t4 = "test-epic-004"  # seeded with no branch
    call(world, "ann", "backlog_update_task", {"task_id": t4, "field": "branch", "value": "fix/line"}, CLONE_A)
    assert rows(world, t4)
    call(world, "ann", "backlog_update_task", {"task_id": t4, "field": "branch", "value": ""}, CLONE_A)
    assert rows(world, t4) == []


def test_a_member_with_no_row_writing_the_shared_value_changes_nothing(serve):
    world = serve()
    reply = call(world, "ann", "backlog_update_task", {"task_id": T1, "field": "branch", "value": "shared"}, CLONE_A)
    assert "No change" in reply and rows(world, T1) == []


def test_the_line_form_batch_deletes_the_callers_row_on_the_shared_value(serve):
    """Re-review I1: `operations="update T branch ..."` goes through the line reader, not task.update."""
    world = serve()
    # A line needs four words, so it cannot carry an empty value: "clear over an empty shared branch" is task.update only.
    call(world, "ann", "backlog_batch_update", {"operations": f"update {T1} branch fix/mine"}, CLONE_A)
    assert len(rows(world, T1)) == 1
    call(world, "ann", "backlog_batch_update", {"operations": f"update {T1} branch shared"}, CLONE_A)
    assert rows(world, T1) == []


def test_a_later_line_that_leaves_branch_alone_keeps_the_row(serve):
    world = serve()
    call(world, "ann", "backlog_batch_update", {"operations": f"update {T1} branch fix/mine"}, CLONE_A)
    call(world, "ann", "backlog_batch_update", {"operations": f"update {T1} priority high"}, CLONE_A)
    assert len(rows(world, T1)) == 1
