"""Native queries are bounded by requested rows/fields and share snapshots."""
from contextlib import closing
import json
import sqlite3

import pytest

from taskmaster.native.migrate import backfill, legacy_entities
from taskmaster.native.queries import Repository, CursorInvalid
from test_native_commands import native  # noqa: F401
from test_native_migration import legacy  # noqa: F401


def test_targeted_details_match_every_kind_without_legacy_reads(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        expected = legacy_entities(connection)
        backfill(connection)
        statements = []
        connection.set_trace_callback(statements.append)
        with Repository(connection).snapshot() as query:
            for row in expected:
                actual = query.get(row["kind"], row["id"], include_body=True, include_deleted=True)
                assert actual["fields"] == row["doc"]
                assert actual["revision"] == row["rev"]
                assert actual["body"] == row["body"]
        assert not any("FROM entities" in sql or "FROM changes" in sql for sql in statements)


def test_bounded_list_does_not_read_documents_or_unrequested_extensions(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        statements = []
        connection.set_trace_callback(statements.append)
        with Repository(connection).snapshot() as query:
            result = query.list("task", fields=["id", "title", "status", "epic"], limit=1)
        assert result["items"][0]["fields"] == {"id": "same", "title": None, "status": "todo", "epic": "missing"}
        assert not any("entity_documents" in sql or "entity_extensions" in sql for sql in statements)
        assert any("LIMIT 2" in sql for sql in statements)


def test_cursor_scope_and_generation_invalidation(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        with Repository(connection).snapshot() as query:
            first = query.list(limit=3, include_archived=True, include_deleted=True)
            assert len(first["items"]) == 3 and first["cursor"]
            second = query.list(limit=3, include_archived=True, include_deleted=True, cursor=first["cursor"])
            assert not ({(i["kind"], i["id"]) for i in first["items"]} & {(i["kind"], i["id"]) for i in second["items"]})
            with pytest.raises(CursorInvalid):
                query.list("task", cursor=first["cursor"])
        connection.execute("UPDATE entities SET doc=json_set(doc,'$.custom','changed') WHERE kind='task'")
        backfill(connection)
        with Repository(connection).snapshot() as query:
            with pytest.raises(CursorInvalid):
                query.list(limit=3, include_archived=True, include_deleted=True, cursor=first["cursor"])


def test_search_matches_legacy_ranking_and_limits(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        expected = connection.execute("SELECT kind,id,bm25(entity_fts) FROM entity_fts WHERE entity_fts MATCH 'run' ORDER BY bm25(entity_fts)").fetchall()
        backfill(connection)
        with Repository(connection).snapshot() as query:
            found = query.search("run", limit=1)
        assert [(r["kind"], r["id"], r["rank"]) for r in found["items"]] == expected


def test_relations_preserve_order_duplicates_and_unresolved_refs(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        with Repository(connection).snapshot() as query:
            refs = query.relations("task", "same", field="depends_on", limit=10)["items"]
            assert [r["value"] for r in refs] == ["same", "missing", "same"]
            assert [r["resolved"] for r in refs] == [True, False, True]
            inverse = query.references_to("task", "same", limit=10)["items"]
            assert any(r["kind"] == "handover" for r in inverse)


def test_summaries_apply_lifecycle_and_scope_in_sql(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        with Repository(connection).snapshot() as query:
            assert query.summary("task", epic="missing")["total"] == 1
            assert query.summary("task", epic="other")["total"] == 0
            assert query.summary("area")["total"] == 0
            assert query.summary("bug")["total"] == 0


@pytest.mark.parametrize("bad", [-1, 0, 501, True, "1"])
def test_native_page_bounds_are_explicit(legacy, bad):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        with Repository(connection).snapshot() as query:
            with pytest.raises(ValueError, match="limit"):
                query.list(limit=bad)


def test_repository_cannot_escape_snapshot_lifetime(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        with Repository(connection).snapshot() as query:
            assert query.get("task", "same")
        with pytest.raises(RuntimeError, match="snapshot"):
            query.get("task", "same")


def test_requested_metadata_columns_do_not_load_unrequested_core_fields(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        statements = []
        connection.set_trace_callback(statements.append)
        with Repository(connection).snapshot() as query:
            query.get("task", "same", fields=["id"])
            query.list("task", fields=["id"], limit=1)
        selects = [sql for sql in statements if "FROM entity_core" in sql]
        assert not any("SELECT *" in sql or "SELECT c.*" in sql or "title_json" in sql for sql in selects)


# ── The change feed ─────────────────────────────────────────────────────────
# `changes_since` is the query a resuming agent asks instead of re-reading the
# whole backlog, so what is pinned here is the promise it makes: sequence order,
# whole commits, an honest scope, and a continuation that loses nothing.

def _command(connection, operation, arguments, key):
    from taskmaster.native.commands import execute
    return execute(connection, {"protocol": 2, "store_id": "fixture-store", "caller_scope": "tests",
                                "request_id": key, "operation": operation, "arguments": arguments,
                                "expected_revisions": []})


def _patch(connection, key, **values):
    return _command(connection, "task.patch", {"id": "same", "set": values}, key)


def _batch(connection, key, step, text):
    return _command(connection, "batch", {"commands": [
        {"operation": "task.patch", "arguments": {"id": "same", "set": {"next_step": step}}},
        {"operation": "note.create", "arguments": {"text": text, "author": "claude", "pinned": False}}]}, key)


def _feed(connection, **kwargs):
    with Repository(connection).snapshot() as query:
        return query.changes_since(**kwargs)


def _seqs(answer):
    if "changes" in answer:
        return [change["seq"] for change in answer["changes"]]
    return [change["seq"] for commit in answer["commits"] for change in commit["changes"]]


def test_a_call_without_a_cursor_starts_from_now_and_answers_a_cursor_only(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        answer = _feed(connection)
        assert answer["commits"] == [] and answer["more"] is False
        assert answer["resync_required"] is False and answer["reason"] is None
        assert answer["sequence"] == 100 and answer["cursor"]
        _patch(connection, "one", next_step="one")
        assert _seqs(_feed(connection, cursor=answer["cursor"])) == [101]


def test_commits_are_reported_in_sequence_order_and_then_the_tail_is_empty(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        start = _feed(connection)["cursor"]
        for n in range(3):
            _patch(connection, f"k{n}", next_step=f"step {n}")
        answer = _feed(connection, cursor=start)
        assert [commit["commit_seq"] for commit in answer["commits"]] == [101, 102, 103]
        assert [commit["operation"] for commit in answer["commits"]] == ["task.patch"] * 3
        assert answer["more"] is False
        tail = _feed(connection, cursor=answer["cursor"])
        assert tail["commits"] == [] and tail["more"] is False and tail["resync_required"] is False


def test_a_multi_entity_commit_is_one_commit_carrying_every_event_it_wrote(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        start = _feed(connection)["cursor"]
        _batch(connection, "batch", "one", "batched")
        commits = _feed(connection, cursor=start)["commits"]
        assert len(commits) == 1
        commit = commits[0]
        assert (commit["first_seq"], commit["final_seq"], commit["commit_seq"]) == (101, 102, 102)
        assert commit["operation"] == "batch" and commit["session"] == "tests" and commit["ts"]
        assert [(c["kind"], c["id"], c["op"]) for c in commit["changes"]] == [
            ("task", "same", "update"), ("note", "NOTE-1000", "create")]
        assert commit["changes"][0]["fields"] == ["next_step"]


def test_group_commits_false_flattens_to_events_in_sequence_order(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        start = _feed(connection, group_commits=False)["cursor"]
        _batch(connection, "batch", "one", "batched")
        answer = _feed(connection, cursor=start, group_commits=False)
        assert "commits" not in answer
        assert [(c["seq"], c["kind"], c["operation"]) for c in answer["changes"]] == [
            (101, "task", "batch"), (102, "note", "batch")]


def test_a_limit_pages_whole_commits_and_the_continuation_loses_nothing(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        start = _feed(connection)["cursor"]
        _batch(connection, "batch", "one", "batched")
        _patch(connection, "after", next_step="two")
        first = _feed(connection, cursor=start, limit=1)
        assert len(first["commits"]) == 1 and first["more"] is True
        assert _seqs(first) == [101, 102]
        second = _feed(connection, cursor=first["cursor"], limit=1)
        assert _seqs(second) == [103] and second["more"] is False


def test_a_scope_filter_reports_only_the_kinds_and_ids_it_names(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        tasks_only = _feed(connection, kinds=["task"])["cursor"]
        notes_only = _feed(connection, kinds=["note"])["cursor"]
        by_id = _feed(connection, ids=["NOTE-1000"])["cursor"]
        _batch(connection, "batch", "one", "batched")
        assert _seqs(_feed(connection, cursor=tasks_only, kinds=["task"])) == [101]
        assert _seqs(_feed(connection, cursor=notes_only, kinds=["note"])) == [102]
        assert _seqs(_feed(connection, cursor=by_id, ids=["NOTE-1000"])) == [102]
        # A filtered commit still reports its own extent, so a caller can tell it
        # saw part of a larger commit rather than a single-entity write.
        commit = _feed(connection, cursor=tasks_only, kinds=["task"])["commits"][0]
        assert (commit["first_seq"], commit["final_seq"]) == (101, 102)


def test_an_epic_scope_reports_the_epic_row_its_tasks_and_a_task_leaving_it(native):
    """Filtered removal is the dangerous case: scoping by current membership
    alone would silently never report the change that moved a task out."""
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        _patch(connection, "join", epic="same")
        start = _feed(connection, epic="same")["cursor"]
        _command(connection, "epic.update", {"id": "same", "field": "name", "value": "Renamed"}, "epic")
        _patch(connection, "leave", epic="missing")
        _patch(connection, "elsewhere", next_step="not in the epic")
        answer = _feed(connection, cursor=start, epic="same")
        assert [(c["kind"], c["id"]) for c in answer["commits"][0]["changes"]] == [("epic", "same")]
        assert [c["fields"] for c in answer["commits"][1]["changes"]] == [["epic"]]
        assert len(answer["commits"]) == 2


def test_backfilled_history_without_a_commit_row_is_one_commit_each(native):
    """The backfill copies the legacy `changes` table straight in, so its rows
    carry no `commit_key`; each must still be a commit of its own."""
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        answer = _feed(connection, since_seq=0, limit=500)
        commits = answer["commits"]
        assert len(commits) == 13 and _seqs(answer) == list(range(1, 14))
        assert commits[0]["first_seq"] == commits[0]["final_seq"] == commits[0]["commit_seq"] == 1
        assert commits[0]["operation"] == "fixture"


def test_since_seq_resumes_from_an_explicit_sequence_and_never_shares_a_call(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        assert _seqs(_feed(connection, since_seq=10, limit=500)) == [11, 12, 13]
        # Past the end of history is "from now", not a cursor into the future.
        assert _feed(connection, since_seq=9999)["sequence"] == 100
        _patch(connection, "one", next_step="one")
        assert _seqs(_feed(connection, cursor=_feed(connection, since_seq=9999)["cursor"])) == []
        with pytest.raises(ValueError, match="since_seq"):
            _feed(connection, cursor="x", since_seq=1)
        with pytest.raises(ValueError, match="since_seq"):
            _feed(connection, since_seq=-1)


def test_a_cursor_from_a_rebuilt_store_or_a_changed_scope_answers_resync_not_an_error(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        cursor = _feed(connection)["cursor"]
        _patch(connection, "one", next_step="one")
        narrowed = _feed(connection, cursor=cursor, kinds=["task"])
        assert narrowed["resync_required"] is True and narrowed["reason"] == "scope_changed"
        assert narrowed["commits"] == [] and narrowed["more"] is False
        unreadable = _feed(connection, cursor="tampered")
        assert unreadable["resync_required"] is True and unreadable["reason"] == "cursor_unreadable"
        connection.execute("UPDATE native_manifest SET value='rebuilt' WHERE key='source_digest'")
        rebuilt = _feed(connection, cursor=cursor)
        assert rebuilt["resync_required"] is True and rebuilt["reason"] == "store_rebuilt"
        # The fresh cursor starts from now, so nothing already acted on replays.
        assert _feed(connection, cursor=rebuilt["cursor"])["commits"] == []


def test_a_write_never_invalidates_a_change_cursor(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        cursor = _feed(connection)["cursor"]
        for n in range(3):
            _patch(connection, f"k{n}", next_step=f"step {n}")
            assert _feed(connection, cursor=cursor)["resync_required"] is False


def test_no_change_is_lost_or_duplicated_across_a_cursor_chain(native):
    """Writes interleaved with one-commit pages: the union of every page equals
    the events in `(start, end]` exactly, with no repeats."""
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        cursor, seen = _feed(connection)["cursor"], []
        for n in range(5):
            _patch(connection, f"k{n}", next_step=f"step {n}")
            if n % 2:
                _batch(connection, f"b{n}", f"t{n}", f"note {n}")
            while True:
                answer = _feed(connection, cursor=cursor, limit=1)
                cursor, seen = answer["cursor"], seen + _seqs(answer)
                assert answer["resync_required"] is False
                if not answer["more"]:
                    break
        expected = [row[0] for row in connection.execute("SELECT seq FROM domain_events WHERE seq>100 ORDER BY seq")]
        assert seen == expected and len(set(seen)) == len(seen)


def test_the_feed_refuses_an_unknown_kind_and_an_out_of_range_limit(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        with pytest.raises(ValueError, match="unknown entity kind"):
            _feed(connection, kinds=["nonsense"])
        with pytest.raises(ValueError, match="limit"):
            _feed(connection, limit=0)
        with pytest.raises(ValueError, match="limit"):
            _feed(connection, limit=501)
