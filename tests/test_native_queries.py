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
            assert len(first["items"]) == 3 and first.get("cursor", "")
            second = query.list(limit=3, include_archived=True, include_deleted=True, cursor=first.get("cursor", ""))
            assert not ({(i["kind"], i["id"]) for i in first["items"]} & {(i["kind"], i["id"]) for i in second["items"]})
            with pytest.raises(CursorInvalid):
                query.list("task", cursor=first.get("cursor", ""))
        connection.execute("UPDATE entities SET doc=json_set(doc,'$.custom','changed') WHERE kind='task'")
        backfill(connection)
        with Repository(connection).snapshot() as query:
            with pytest.raises(CursorInvalid):
                query.list(limit=3, include_archived=True, include_deleted=True, cursor=first.get("cursor", ""))


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


@pytest.fixture
def store_db(native):
    """The activated fixture store, with a history that reaches its high-water mark.

    `test_native_migration`'s legacy fixture writes change 100 and deletes it, to
    prove AUTOINCREMENT never recycles a sequence, so the backfill imports history
    ending at 13 under an `event_high_water` of 100 — a state no real store
    reaches, since nothing deletes a change row. Restoring that row makes the two
    agree, so every feed and context answer here is checked against a store whose
    reported sequence is the last event it holds.
    """
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        connection.execute("INSERT INTO domain_events(seq,ts,session,tool,kind,id,op) "
                           "VALUES(100,'date','s','t','task','gone','delete')")
        assert connection.execute("SELECT MAX(seq) FROM domain_events").fetchone()[0] == 100
    return native


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


def test_a_call_without_a_cursor_starts_from_now_and_answers_a_cursor_only(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        answer = _feed(connection)
        assert answer["commits"] == [] and answer["more"] is False
        assert answer["resync_required"] is False and answer["reason"] is None
        assert answer["sequence"] == 100 and answer.get("cursor", "")
        _patch(connection, "one", next_step="one")
        assert _seqs(_feed(connection, cursor=answer.get("cursor", ""))) == [101]


def test_commits_are_reported_in_sequence_order_and_then_the_tail_is_empty(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        start = _feed(connection)["cursor"]
        for n in range(3):
            _patch(connection, f"k{n}", next_step=f"step {n}")
        answer = _feed(connection, cursor=start)
        assert [commit["commit_seq"] for commit in answer["commits"]] == [101, 102, 103]
        assert [commit["operation"] for commit in answer["commits"]] == ["task.patch"] * 3
        assert answer["more"] is False
        tail = _feed(connection, cursor=answer.get("cursor", ""))
        assert tail["commits"] == [] and tail["more"] is False and tail["resync_required"] is False


def test_a_multi_entity_commit_is_one_commit_carrying_every_event_it_wrote(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
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


def test_group_commits_false_flattens_to_events_in_sequence_order(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        start = _feed(connection, group_commits=False)["cursor"]
        _batch(connection, "batch", "one", "batched")
        answer = _feed(connection, cursor=start, group_commits=False)
        assert "commits" not in answer
        assert [(c["seq"], c["kind"], c["operation"]) for c in answer["changes"]] == [
            (101, "task", "batch"), (102, "note", "batch")]


def test_a_limit_pages_whole_commits_and_the_continuation_loses_nothing(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        start = _feed(connection)["cursor"]
        _batch(connection, "batch", "one", "batched")
        _patch(connection, "after", next_step="two")
        first = _feed(connection, cursor=start, limit=1)
        assert len(first["commits"]) == 1 and first["more"] is True
        assert _seqs(first) == [101, 102]
        second = _feed(connection, cursor=first.get("cursor", ""), limit=1)
        assert _seqs(second) == [103] and second["more"] is False


def test_a_scope_filter_reports_only_the_kinds_and_ids_it_names(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
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


def test_an_epic_scope_reports_the_epic_row_its_tasks_and_a_task_leaving_it(store_db):
    """Filtered removal is the dangerous case: scoping by current membership
    alone would silently never report the change that moved a task out."""
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
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


def test_backfilled_history_without_a_commit_row_is_one_commit_each(store_db):
    """The backfill copies the legacy `changes` table straight in, so its rows
    carry no `commit_key`; each must still be a commit of its own."""
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        answer = _feed(connection, since_seq=0, limit=500)
        commits = answer["commits"]
        assert len(commits) == 14 and _seqs(answer) == [*range(1, 14), 100]
        assert commits[0]["first_seq"] == commits[0]["final_seq"] == commits[0]["commit_seq"] == 1
        assert commits[0]["operation"] == "fixture"


def test_since_seq_resumes_from_an_explicit_sequence_and_never_shares_a_call(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        assert _seqs(_feed(connection, since_seq=10, limit=500)) == [11, 12, 13, 100]
        # Past the end of history is "from now", not a cursor into the future.
        assert _feed(connection, since_seq=9999)["sequence"] == 100
        _patch(connection, "one", next_step="one")
        assert _seqs(_feed(connection, cursor=_feed(connection, since_seq=9999)["cursor"])) == []
        with pytest.raises(ValueError, match="since_seq"):
            _feed(connection, cursor="x", since_seq=1)
        with pytest.raises(ValueError, match="since_seq"):
            _feed(connection, since_seq=-1)


def test_a_cursor_from_a_rebuilt_store_or_a_changed_scope_answers_resync_not_an_error(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
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
        assert _feed(connection, cursor=rebuilt.get("cursor", ""))["commits"] == []


def test_a_write_never_invalidates_a_change_cursor(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        cursor = _feed(connection)["cursor"]
        for n in range(3):
            _patch(connection, f"k{n}", next_step=f"step {n}")
            assert _feed(connection, cursor=cursor)["resync_required"] is False


def test_no_change_is_lost_or_duplicated_across_a_cursor_chain(store_db):
    """Writes interleaved with one-commit pages: the union of every page equals
    the events in `(start, end]` exactly, with no repeats."""
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        cursor, seen = _feed(connection)["cursor"], []
        for n in range(5):
            _patch(connection, f"k{n}", next_step=f"step {n}")
            if n % 2:
                _batch(connection, f"b{n}", f"t{n}", f"note {n}")
            while True:
                answer = _feed(connection, cursor=cursor, limit=1)
                cursor, seen = answer.get("cursor", ""), seen + _seqs(answer)
                assert answer["resync_required"] is False
                if not answer["more"]:
                    break
        expected = [row[0] for row in connection.execute("SELECT seq FROM domain_events WHERE seq>100 ORDER BY seq")]
        assert seen == expected and len(set(seen)) == len(seen)


def test_the_feed_refuses_an_unknown_kind_and_an_out_of_range_limit(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        with pytest.raises(ValueError, match="unknown entity kind"):
            _feed(connection, kinds=["nonsense"])
        with pytest.raises(ValueError, match="limit"):
            _feed(connection, limit=0)
        with pytest.raises(ValueError, match="limit"):
            _feed(connection, limit=501)


def _set_floor(connection, value):
    connection.execute("INSERT INTO sync_state(key,value_json) VALUES('change_history_floor',?) "
                       "ON CONFLICT(key) DO UPDATE SET value_json=excluded.value_json", (json.dumps(value),))


def test_a_cursor_below_the_retention_floor_answers_history_expired(store_db):
    """No pruner ships (D4) — pruning `domain_events` would break the migration
    oracle that compares it row-for-row against the legacy `changes` table — so
    expiry is expressed, and tested, by raising the floor."""
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        cursor = _feed(connection)["cursor"]
        _patch(connection, "one", next_step="one")
        _set_floor(connection, 100)
        assert _seqs(_feed(connection, cursor=cursor)) == [101]
        _set_floor(connection, 101)
        expired = _feed(connection, cursor=cursor)
        assert expired["resync_required"] is True and expired["reason"] == "history_expired"
        assert expired["commits"] == []
        # The fresh cursor is above the floor, so recovery takes exactly one call.
        assert _feed(connection, cursor=expired.get("cursor", ""))["resync_required"] is False


def test_the_floor_is_read_on_every_call_not_cached_for_the_snapshot(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        cursor = _feed(connection)["cursor"]
        assert _feed(connection, cursor=cursor)["resync_required"] is False
        _set_floor(connection, 101)
        assert _feed(connection, cursor=cursor)["reason"] == "history_expired"
        _set_floor(connection, 0)
        assert _feed(connection, cursor=cursor)["resync_required"] is False


def test_an_explicit_since_seq_below_the_floor_expires_the_same_way(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        _set_floor(connection, 50)
        assert _feed(connection, since_seq=50)["resync_required"] is False
        expired = _feed(connection, since_seq=49)
        assert expired["resync_required"] is True and expired["reason"] == "history_expired"


def test_an_unreadable_floor_refuses_loudly_rather_than_admitting_expired_history(store_db):
    """A floor that cannot be read cannot prove history is retained, and a feed
    that silently assumed zero would replay work an agent already acted on."""
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        _set_floor(connection, "not a sequence")
        with pytest.raises(ValueError, match="change_history_floor"):
            _feed(connection)


def test_a_floor_above_the_high_water_mark_never_loops_on_resync(store_db):
    """Review B, item 6: with the floor past every event, a cursor issued at the
    current sequence was itself expired, so each call answered another resync."""
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        before = _feed(connection)
        _set_floor(connection, before["sequence"] + 5)
        started = _feed(connection)
        assert _feed(connection, cursor=started.get("cursor", ""))["resync_required"] is False
        expired = _feed(connection, cursor=before.get("cursor", ""))
        assert expired["resync_required"] is True and expired["reason"] == "history_expired"
        assert _feed(connection, cursor=expired.get("cursor", ""))["resync_required"] is False


def test_a_cursor_from_before_a_restore_answers_history_rewound(store_db, tmp_path):
    """A store restored from a backup keeps its identity, so the rebuild fence
    passes; only the sequence shows the history went backwards. The next write
    reuses a sequence the cursor already covers, and resuming would skip it."""
    backup = tmp_path / "backup.db"
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection, \
            closing(sqlite3.connect(backup)) as copy:
        connection.backup(copy)
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        _patch(connection, "before-restore", next_step="lost")
        cursor = _feed(connection)["cursor"]
    with closing(sqlite3.connect(backup)) as copy, closing(sqlite3.connect(store_db)) as live:
        copy.backup(live)
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        rewound = _feed(connection, cursor=cursor)
        assert rewound["resync_required"] is True and rewound["reason"] == "history_rewound", rewound
        _patch(connection, "after-restore", next_step="kept")
        assert _seqs(_feed(connection, cursor=rewound.get("cursor", ""))) == [101]


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


def test_context_answers_mandatory_blockers_selected_context_and_a_budget(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        answer = _context(connection, focus="same", scope="task",
                          include=["dependencies", "links"], budget_bytes=8000)
        assert set(answer) == {"sequence", "focus", "mandatory", "selected", "budget"}
        assert answer["focus"] == "same"
        assert answer["sequence"] == 100
        # The fixture task depends on itself (todo) and on an id that resolves to
        # nothing; both block, and the unresolvable one says so.
        blockers = {(b["kind"], b["id"], b["state"]) for b in answer["mandatory"]["blockers"]}
        assert ("dependency", "same", "todo") in blockers
        assert ("dependency", "missing", "missing") in blockers
        assert answer["mandatory"]["clear"] is False
        assert [row["id"] for row in answer["selected"]["dependencies"]] == ["missing", "same"]
        assert answer["selected"]["links"]
        assert answer["budget"] == {"used_bytes": answer["budget"]["used_bytes"]}


def test_context_used_bytes_is_the_bytes_of_the_answer_actually_returned(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        with Repository(connection).snapshot() as query:
            text = query.context(focus="same", include=["dependencies", "links", "body", "notes"])
        assert json.loads(text)["budget"]["used_bytes"] == len(text.encode("utf-8"))


def test_context_returns_mandatory_complete_and_flags_over_budget_rather_than_trimming(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        whole = _context(connection, focus="same", include=["dependencies"])
        squeezed = _context(connection, focus="same", include=["dependencies"], budget_bytes=64)
        assert squeezed["budget"]["over_budget"] is True
        assert squeezed["selected"] == {}
        assert squeezed["mandatory"] == whole["mandatory"]
        assert squeezed["budget"].get("omitted", {}).get("dependencies", 0) == 2
        assert squeezed["budget"].get("omitted_total", 0) == 2
        # Nothing was delivered, so there is nothing to continue from: a cursor
        # here would loop forever on the same page.
        assert squeezed.get("cursor", "") == ""


def test_context_reports_clear_only_when_every_mandatory_producer_answered(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
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


def test_context_without_a_focus_task_is_never_reported_as_clear(store_db):
    """No focus means no producer answered. Saying `clear` there would tell an
    agent it may proceed on a question nobody asked."""
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        answer = _context(connection, scope="project", include=["notes"])
        assert answer.get("focus") is None
        assert answer["mandatory"]["clear"] is False
        assert [(b["kind"], b["id"], b["reason"]) for b in answer["mandatory"]["blockers"]] == [
            ("unknown", "focus", "no_focus_task")]
        assert answer["selected"]["notes"]


def test_context_takes_its_session_focus_from_the_task_that_session_holds(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        _command(connection, "task.update", {"id": "same", "field": "status", "value": "in-progress"}, "st")
        _lock(connection, "lock", "alpha")
        assert _context(connection, scope="session", session="alpha")["focus"] == "same"
        # Another session holds it, so that session has no focus of its own.
        assert _context(connection, scope="session", session="beta").get("focus") is None


def test_a_peer_claim_blocks_whether_or_not_its_holder_can_be_judged(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        _lock(connection, "lock", "peer")
        # No sessions row and not a session id: unjudgeable, and an unjudgeable
        # holder keeps its claim until the TTL says otherwise (`native.claims`).
        unjudged = _context(connection, focus="same", session="alpha")["mandatory"]["blockers"]
        assert [(b["kind"], b["id"], b["live"]) for b in unjudged if b["kind"] == "claim"] == [
            ("claim", "same", None)]
        _session_row(connection, "peer", _now())
        held = _context(connection, focus="same", session="alpha")["mandatory"]["blockers"]
        assert [(b["kind"], b["id"], b["by"]) for b in held if b["kind"] == "claim"] == [
            ("claim", "same", "peer")]
        # The holder is me, so it is not a blocker.
        assert not [b for b in _context(connection, focus="same", session="peer")["mandatory"]["blockers"]
                    if b["kind"] == "claim"]


def test_omitted_counts_are_exact_and_the_cursor_resumes_where_the_page_stopped(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        for n in range(6):
            _command(connection, "note.create", {"text": f"Note number {n}", "author": "claude",
                                                 "pinned": True}, f"note{n}")
        whole = _context(connection, scope="project", include=["notes"])
        every = [row["id"] for row in whole["selected"]["notes"]]
        assert len(every) == 7 and whole.get("cursor", "") == "" and whole["budget"].get("omitted", {}).get("notes", 0) == 0
        limit = whole["budget"]["used_bytes"] - 40
        first = _context(connection, scope="project", include=["notes"], budget_bytes=limit)
        page = [row["id"] for row in first["selected"]["notes"]]
        assert 0 < len(page) < 7
        assert first["budget"].get("omitted", {}).get("notes", 0) == 7 - len(page)
        assert first.get("cursor", "")
        seen, cursor = list(page), first.get("cursor", "")
        while cursor:
            nxt = _context(connection, scope="project", include=["notes"],
                           budget_bytes=limit, cursor=cursor)
            seen += [row["id"] for row in nxt["selected"].get("notes", [])]
            cursor = nxt.get("cursor", "")
        assert seen == every


def test_a_context_cursor_is_refused_when_the_question_or_the_store_moved_on(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
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


def test_context_validates_its_scope_include_vocabulary_and_budget(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        with Repository(connection).snapshot() as query:
            with pytest.raises(ValueError, match="scope"):
                query.context(scope="everything")
            with pytest.raises(ValueError, match="include"):
                query.context(focus="same", include=["spec", "nonsense"])
            with pytest.raises(ValueError, match="include"):
                query.context(focus="same", include="dependencies")
            with pytest.raises(ValueError, match="budget_bytes"):
                query.context(focus="same", budget_bytes=0)


def test_provenance_names_where_each_selected_section_came_from(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        answer = _context(connection, focus="same", include=["dependencies", "handovers", "spec"])
        # A section's predicate is fixed per section name (the tool's docstring), so a
        # section delivered whole carries no provenance entry at all.
        assert "dependencies" not in answer["provenance"] and "handovers" not in answer["provenance"]
        assert answer.get("provenance", {}).get("dependencies", {}).get("truncated", False) is False
        # `spec` is a document section: the fixture task declares no path for it,
        # so provenance says it was never imported rather than leaving it absent.
        assert answer["provenance"]["spec"]["imported"] is False


def test_a_focus_only_section_is_empty_rather_than_wrong_when_there_is_no_focus(store_db):
    with closing(sqlite3.connect(store_db, isolation_level=None)) as connection:
        answer = _context(connection, scope="project", include=["dependencies", "siblings", "notes"])
        assert answer["selected"].get("dependencies") is None
        assert answer["budget"].get("omitted", {}).get("dependencies", 0) == 0
        assert answer["provenance"]["dependencies"]["query"] == "no_focus"


def test_neighbourhood_is_distinct_aggregated_and_paged(tmp_path):
    """Path neighbours weigh by matching claim pairs; handover multiplicity is summed."""
    from types import SimpleNamespace
    from taskmaster import store
    database = tmp_path / "graph.db"
    with closing(sqlite3.connect(database, isolation_level=None)) as connection:
        connection.row_factory = sqlite3.Row
        connection.executescript(store.SCHEMA_SQL)
        connection.executemany("INSERT INTO meta VALUES(?,?)", [("schema_version", "1"), ("creation_token", "test")])
        docs = {("task", "T-1"): {"anchors": ["src/a.py", "src/*"]}, ("task", "T-2"): {"anchors": ["src/a.py"]},
                ("task", "T-3"): {}, ("bug", "B-1"): {"location": ["src/b.py"]},
                ("handover", "H-1"): {"task_ids": ["T-1", "T-2", "T-2"]}, ("handover", "H-2"): {"task_ids": ["T-1", "T-3"]}}
        fake_store = store.Store.__new__(store.Store)
        for (kind, ident), doc in docs.items():
            connection.execute("INSERT INTO entities VALUES(?,?,NULL,NULL,0,0,?,NULL,1,1)", (kind, ident, json.dumps(dict(doc, id=ident))))
        fake_store._refresh_derived(SimpleNamespace(connection=connection, _derived_keys=set(docs)))
        connection.row_factory = None
        backfill(connection)
        with Repository(connection).snapshot() as query:
            whole = query.neighbourhood("task", "T-1", limit=10)
            assert whole["items"] == [
                {"kind": "bug", "id": "B-1", "via": "path", "weight": 1},
                {"kind": "task", "id": "T-2", "via": "handover", "weight": 2},
                {"kind": "task", "id": "T-2", "via": "path", "weight": 2},
                {"kind": "task", "id": "T-3", "via": "handover", "weight": 1}]
            assert whole["truncated"] is False
            page = query.neighbourhood("task", "T-1", limit=1)
            assert page["items"] == whole["items"][:1] and page["truncated"] is True
            assert query.neighbourhood("task", "T-3", limit=5)["items"] == [{"kind": "task", "id": "T-1", "via": "handover", "weight": 1}]
            with pytest.raises(KeyError):
                query.neighbourhood("task", "absent")
            with pytest.raises(ValueError, match="limit"):
                query.neighbourhood("task", "T-1", limit=0)
