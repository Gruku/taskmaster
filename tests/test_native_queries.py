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


def _move(connection, key, epic):
    """The one path that reassigns a task's epic: the viewer's document patch."""
    return _command(connection, "task.viewer_update",
                    {"id": "same", "patch": {"epic": epic}, "if_match": ""}, key)


def test_an_epic_scope_reports_the_epic_row_its_tasks_and_a_task_leaving_it(native):
    """Filtered removal is the dangerous case: scoping by current membership
    alone would silently never report the change that moved a task out."""
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        # The fixture task sits in an epic that does not exist; give it one so the
        # viewer patch — the only operation that reassigns an epic — will run.
        _command(connection, "epic.create", {"epic_id": "missing", "name": "Missing", "done_when": "never"}, "mk")
        start = _feed(connection, epic="same")["cursor"]
        _move(connection, "join", "same")
        _command(connection, "epic.update", {"id": "same", "field": "name", "value": "Renamed"}, "epic")
        _move(connection, "leave", "missing")
        _patch(connection, "elsewhere", next_step="not in the epic")
        answer = _feed(connection, cursor=start, epic="same")
        assert [[(c["kind"], c["id"]) for c in commit["changes"]] for commit in answer["commits"]] == [
            [("task", "same")], [("epic", "same")], [("task", "same")]]
        assert "epic" in answer["commits"][0]["changes"][0]["fields"]
        assert "epic" in answer["commits"][2]["changes"][0]["fields"]


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


def _set_floor(connection, value):
    connection.execute("INSERT INTO sync_state(key,value_json) VALUES('change_history_floor',?) "
                       "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (json.dumps(value),))


def test_a_cursor_below_the_retention_floor_answers_history_expired(native):
    """No pruner ships (D4) — pruning `domain_events` would break the migration
    oracle that compares it row-for-row against the legacy `changes` table — so
    expiry is expressed, and tested, by raising the floor."""
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        cursor = _feed(connection)["cursor"]
        _patch(connection, "one", next_step="one")
        _set_floor(connection, 100)
        assert _seqs(_feed(connection, cursor=cursor)) == [101]
        _set_floor(connection, 101)
        expired = _feed(connection, cursor=cursor)
        assert expired["resync_required"] is True and expired["reason"] == "history_expired"
        assert expired["commits"] == []
        # The fresh cursor is above the floor, so recovery takes exactly one call.
        assert _feed(connection, cursor=expired["cursor"])["resync_required"] is False


def test_the_floor_is_read_on_every_call_not_cached_for_the_snapshot(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        cursor = _feed(connection)["cursor"]
        assert _feed(connection, cursor=cursor)["resync_required"] is False
        _set_floor(connection, 101)
        assert _feed(connection, cursor=cursor)["reason"] == "history_expired"
        _set_floor(connection, 0)
        assert _feed(connection, cursor=cursor)["resync_required"] is False


def test_an_explicit_since_seq_below_the_floor_expires_the_same_way(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        _set_floor(connection, 50)
        assert _feed(connection, since_seq=50)["resync_required"] is False
        expired = _feed(connection, since_seq=49)
        assert expired["resync_required"] is True and expired["reason"] == "history_expired"


def test_an_unreadable_floor_refuses_loudly_rather_than_admitting_expired_history(native):
    """A floor that cannot be read cannot prove history is retained, and a feed
    that silently assumed zero would replay work an agent already acted on."""
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        _set_floor(connection, "not a sequence")
        with pytest.raises(ValueError, match="change_history_floor"):
            _feed(connection)


# ── Bounded agent context ───────────────────────────────────────────────────
# `context` is the one call that answers "what do I need to know to work on X".
# What is pinned here is the promise it makes: mandatory blockers complete and
# never trimmed, selected context bounded by real bytes, omission counts that
# are exact, and a continuation that resumes where the page stopped.

def _context(connection, **kwargs):
    with Repository(connection).snapshot() as query:
        return json.loads(query.context(**kwargs))


def _lock(connection, key, holder):
    return _command(connection, "task.update", {"id": "same", "field": "locked_by", "value": holder}, key)


def _session_row(connection, name, last_seen):
    connection.execute("INSERT INTO sessions(session,pid,host,started,last_seen,cwd,current_tool) "
                       "VALUES(?,?,?,?,?,?,?) ON CONFLICT(session) DO UPDATE SET last_seen=excluded.last_seen",
                       (name, 999999, "nowhere", last_seen, last_seen, "/tmp", None))


def _now(offset_seconds=0):
    from datetime import datetime, timedelta, timezone
    return (datetime.now(timezone.utc) + timedelta(seconds=offset_seconds)).isoformat()


def test_context_answers_mandatory_blockers_selected_context_and_a_budget(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        answer = _context(connection, focus="same", scope="task",
                          include=["dependencies", "links"], budget_bytes=8000)
        assert set(answer) == {"store_id", "sequence", "scope", "focus", "mandatory",
                               "selected", "budget", "provenance", "cursor"}
        assert answer["focus"] == "same" and answer["scope"] == "task"
        assert answer["sequence"] == 100
        # The fixture task depends on itself (todo) and on an id that resolves to
        # nothing; both block, and the unresolvable one says so.
        blockers = {(b["kind"], b["id"], b["state"]) for b in answer["mandatory"]["blockers"]}
        assert ("dependency", "same", "todo") in blockers
        assert ("dependency", "missing", "missing") in blockers
        assert answer["mandatory"]["clear"] is False
        assert [row["id"] for row in answer["selected"]["dependencies"]] == ["missing", "same"]
        assert answer["selected"]["links"]
        assert answer["budget"]["applies_to"] == "selected"
        assert answer["budget"]["omitted"] == {"dependencies": 0, "links": 0}


def test_context_used_bytes_is_the_bytes_of_the_answer_actually_returned(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        with Repository(connection).snapshot() as query:
            text = query.context(focus="same", include=["dependencies", "links", "body", "notes"])
        assert json.loads(text)["budget"]["used_bytes"] == len(text.encode("utf-8"))


def test_context_returns_mandatory_complete_and_flags_over_budget_rather_than_trimming(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        whole = _context(connection, focus="same", include=["dependencies"])
        squeezed = _context(connection, focus="same", include=["dependencies"], budget_bytes=64)
        assert squeezed["budget"]["over_budget"] is True
        assert squeezed["selected"] == {}
        assert squeezed["mandatory"] == whole["mandatory"]
        assert squeezed["budget"]["omitted"]["dependencies"] == 2
        assert squeezed["budget"]["omitted_total"] == 2
        # Nothing was delivered, so there is nothing to continue from: a cursor
        # here would loop forever on the same page.
        assert squeezed["cursor"] == ""


def test_context_reports_clear_only_when_every_mandatory_producer_answered(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        _command(connection, "task.create", {"task_id": "clean", "title": "Clean", "epic": "same",
                                             "phase": "P-1", "priority": "medium"}, "mk")
        # A created task lands on the standard lane, whose review gates are the
        # mandatory context a fresh task starts out missing.
        fresh = _context(connection, focus="clean")["mandatory"]
        assert fresh["clear"] is False
        assert [(b["kind"], b["id"]) for b in fresh["blockers"]] == [
            ("gate", "design-review"), ("gate", "review-gate")]
        for gate in ("design-review", "review-gate"):
            _command(connection, "task.gate", {"id": "clean", "gate": gate, "verdict": "pass"},
                     f"g-{gate}")
        assert _context(connection, focus="clean")["mandatory"]["clear"] is True
        absent = _context(connection, focus="no-such-task")
        assert absent["mandatory"]["clear"] is False
        unknowns = [b for b in absent["mandatory"]["blockers"] if b["kind"] == "unknown"]
        assert unknowns and unknowns[0]["id"] == "task"


def test_context_without_a_focus_task_is_never_reported_as_clear(native):
    """No focus means no producer answered. Saying `clear` there would tell an
    agent it may proceed on a question nobody asked."""
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        answer = _context(connection, scope="project", include=["notes"])
        assert answer["focus"] is None
        assert answer["mandatory"]["clear"] is False
        assert [(b["kind"], b["id"], b["reason"]) for b in answer["mandatory"]["blockers"]] == [
            ("unknown", "focus", "no_focus_task")]
        assert answer["selected"]["notes"]


def test_context_takes_its_session_focus_from_the_task_that_session_holds(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        _command(connection, "task.update", {"id": "same", "field": "status", "value": "in-progress"}, "st")
        _lock(connection, "lock", "alpha")
        assert _context(connection, scope="session", session="alpha")["focus"] == "same"
        # Another session holds it, so that session has no focus of its own.
        assert _context(connection, scope="session", session="beta")["focus"] is None


def test_a_live_peer_claim_blocks_and_a_holder_with_no_session_does_not(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        _lock(connection, "lock", "peer")
        stale = _context(connection, focus="same", session="alpha")["mandatory"]["blockers"]
        assert not [b for b in stale if b["kind"] == "claim"]
        _session_row(connection, "peer", _now())
        held = _context(connection, focus="same", session="alpha")["mandatory"]["blockers"]
        assert [(b["kind"], b["id"], b["by"]) for b in held if b["kind"] == "claim"] == [
            ("claim", "same", "peer")]
        # The holder is me, so it is not a blocker.
        assert not [b for b in _context(connection, focus="same", session="peer")["mandatory"]["blockers"]
                    if b["kind"] == "claim"]


def test_omitted_counts_are_exact_and_the_cursor_resumes_where_the_page_stopped(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        for n in range(6):
            _command(connection, "note.create", {"text": f"Note number {n}", "author": "claude",
                                                 "pinned": True}, f"note{n}")
        whole = _context(connection, scope="project", include=["notes"])
        every = [row["id"] for row in whole["selected"]["notes"]]
        assert len(every) == 7 and whole["cursor"] == "" and whole["budget"]["omitted"]["notes"] == 0
        limit = whole["budget"]["used_bytes"] - 40
        first = _context(connection, scope="project", include=["notes"], budget_bytes=limit)
        page = [row["id"] for row in first["selected"]["notes"]]
        assert 0 < len(page) < 7
        assert first["budget"]["omitted"]["notes"] == 7 - len(page)
        assert first["cursor"]
        seen, cursor = list(page), first["cursor"]
        while cursor:
            nxt = _context(connection, scope="project", include=["notes"],
                           budget_bytes=limit, cursor=cursor)
            seen += [row["id"] for row in nxt["selected"].get("notes", [])]
            cursor = nxt["cursor"]
        assert seen == every


def test_a_context_cursor_is_refused_when_the_question_or_the_store_moved_on(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        for n in range(6):
            _command(connection, "note.create", {"text": f"Note number {n}", "author": "claude",
                                                 "pinned": True}, f"note{n}")
        whole = _context(connection, scope="project", include=["notes"])
        limit = whole["budget"]["used_bytes"] - 40
        cursor = _context(connection, scope="project", include=["notes"], budget_bytes=limit)["cursor"]
        with Repository(connection).snapshot() as query:
            with pytest.raises(CursorInvalid):
                query.context(scope="project", include=["notes", "issues"],
                              budget_bytes=limit, cursor=cursor)
            with pytest.raises(CursorInvalid):
                query.context(scope="project", include=["notes"], budget_bytes=limit,
                              cursor="not-a-cursor-this-store-issued")
        # A write moves the snapshot on, and a half-finished page cannot span it.
        _command(connection, "note.create", {"text": "After the page", "author": "claude",
                                             "pinned": True}, "later")
        with Repository(connection).snapshot() as query:
            with pytest.raises(CursorInvalid):
                query.context(scope="project", include=["notes"], budget_bytes=limit, cursor=cursor)


def test_context_validates_its_scope_include_vocabulary_and_budget(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        with Repository(connection).snapshot() as query:
            with pytest.raises(ValueError, match="scope"):
                query.context(scope="everything")
            with pytest.raises(ValueError, match="include"):
                query.context(focus="same", include=["spec", "nonsense"])
            with pytest.raises(ValueError, match="include"):
                query.context(focus="same", include="dependencies")
            with pytest.raises(ValueError, match="budget_bytes"):
                query.context(focus="same", budget_bytes=0)


def test_provenance_names_where_each_selected_section_came_from(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        answer = _context(connection, focus="same", include=["dependencies", "handovers", "spec"])
        assert answer["provenance"]["dependencies"]["query"] == "dependencies.depends_on"
        assert answer["provenance"]["handovers"]["query"] == "memberships.task_ids"
        assert answer["provenance"]["dependencies"]["truncated"] is False
        # `spec` is a document section: the fixture task declares no path for it,
        # so provenance says it was never imported rather than leaving it absent.
        assert answer["provenance"]["spec"]["imported"] is False


def test_a_focus_only_section_is_empty_rather_than_wrong_when_there_is_no_focus(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        answer = _context(connection, scope="project", include=["dependencies", "siblings", "notes"])
        assert answer["selected"].get("dependencies") is None
        assert answer["budget"]["omitted"]["dependencies"] == 0
        assert answer["provenance"]["dependencies"]["query"] == "no_focus"
