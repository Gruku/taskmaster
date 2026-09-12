"""Lifecycle slices share native transactions and existing pure domain rules."""
from contextlib import closing
import sqlite3

import pytest

from taskmaster.native.commands import execute
from taskmaster.native.queries import Repository
from test_native_commands import native, envelope  # noqa: F401
from test_native_migration import legacy  # noqa: F401


def create(connection, operation, arguments, key):
    receipt = execute(connection, envelope(operation, arguments, key=key))
    return receipt["affected"][0]["id"]


def test_note_body_update_search_and_archive_are_atomic(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        ident = create(connection, "note.create", {"text": "Original text", "author": "claude"}, "create")
        updated = execute(connection, envelope("note.update", {"id": ident, "text": "Replacement searchable", "pinned": True}, key="update"))
        assert updated["affected"][0]["revision"] == 2
        with Repository(connection).snapshot() as query:
            assert query.get("note", ident, include_body=True)["body"] == "Replacement searchable"
            assert query.search("replacement")["items"][0]["id"] == ident
        execute(connection, envelope("note.archive", {"id": ident}, key="archive"))
        with Repository(connection).snapshot() as query:
            assert query.get("note", ident)["archived"]
            assert not query.list("note")["items"] or all(v["id"] != ident for v in query.list("note")["items"])
        jobs = connection.execute("SELECT effect,file FROM projection_jobs WHERE commit_seq=(SELECT MAX(seq) FROM domain_events) ORDER BY job_key").fetchall()
        assert ("delete", f"notes/{ident}.md") in jobs
        assert ("write", f"notes/_archive/{ident}.md") in jobs


def test_decision_create_resolve_and_invalid_choice_preserve_domain_contract(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        connection.execute("INSERT INTO id_counters VALUES('decision','DEC-',0)")
        ident = create(connection, "decision.create", {"title": "Choose storage", "options": ["A", "B"], "task_id": "same"}, "create")
        with pytest.raises(ValueError, match="1..2"):
            execute(connection, envelope("decision.resolve", {"id": ident, "resolved_with": 3}, key="bad"))
        result = execute(connection, envelope("decision.resolve", {"id": ident, "resolved_with": 2, "rationale": "Reviewed"}, key="resolve"))
        assert result["affected"][0]["fields"]["status"] == "resolved"
        with Repository(connection).snapshot() as query:
            assert query.get("decision", ident)["fields"]["resolved_with"] == 2
            assert query.relations("decision", ident, field="task_id")["items"][0]["target_id"] == "same"


def test_issue_update_keeps_pure_validation_and_resolved_stamp(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        connection.execute("INSERT INTO id_counters VALUES('issue','ISS-',0)")
        ident = create(connection, "issue.create", {"title": "Recurring defect", "severity": "P1", "evidence": "Repeated"}, "create")
        execute(connection, envelope("issue.update", {"id": ident, "patch": {"status": "fixed", "fixed_in_task": "same"}}, key="fixed"))
        with Repository(connection).snapshot() as query:
            fields = query.get("issue", ident)["fields"]
            assert fields["status"] == "fixed" and fields["resolved"]
            assert query.relations("issue", ident, field="fixed_in_task")["items"][0]["resolved"]


def test_bug_archive_refuses_unresolved_state(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        connection.execute("INSERT INTO id_counters VALUES('bug','B-',1)")
        ident = create(connection, "bug.create", {"title": "Broken", "found_in": "same"}, "create")
        with pytest.raises(ValueError):
            execute(connection, envelope("bug.archive", {"id": ident}, key="blocked"))
        execute(connection, envelope("bug.update", {"id": ident, "patch": {"status": "fixed", "fix_commit": "abc123"}}, key="fixed"))
        execute(connection, envelope("bug.archive", {"id": ident}, key="archive"))
        with Repository(connection).snapshot() as query:
            assert query.get("bug", ident)["archived"]


def test_handover_create_with_supersession_is_one_atomic_command(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        first = create(connection, "handover.create", {"tldr": "Initial context", "body": "Original authored body", "task_ids": ["same"]}, "first")
        second = execute(connection, envelope("handover.create", {"tldr": "New context", "supersedes": first, "task_ids": ["same"]}, key="second"))
        assert len(second["affected"]) == 2
        new_id = next(r["id"] for r in second["affected"] if r["id"] != first)
        with Repository(connection).snapshot() as query:
            old = query.get("handover", first, include_body=True)
            assert old["fields"]["superseded_by"] == new_id
            assert "Original authored body" in old["body"]
        before = connection.execute("SELECT COUNT(*) FROM entity_core").fetchone()[0]
        with pytest.raises(KeyError):
            execute(connection, envelope("handover.create", {"tldr": "Must roll back", "supersedes": "missing"}, key="bad"))
        assert connection.execute("SELECT COUNT(*) FROM entity_core").fetchone()[0] == before


def test_generic_lifecycle_patch_cannot_rename_an_entity(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        with pytest.raises(ValueError, match="immutable"):
            execute(connection, envelope("issue.update", {"id": "I-1", "patch": {"id": "other"}}, key="rename"))


def test_creation_atomically_resolves_existing_typed_memberships(native):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        connection.execute("INSERT INTO id_counters VALUES('bug','B-',1)")
        connection.execute("INSERT INTO id_counters VALUES('issue','ISS-',0)")
        bug = create(connection, "bug.create", {"title": "Source"}, "bug")
        execute(connection, envelope("bug.update", {"id": bug, "patch": {"promoted_to": "ISS-001"}}, key="reference"))
        with Repository(connection).snapshot() as query:
            assert not query.relations("bug", bug, field="promoted_to")["items"][0]["resolved"]
        issue = create(connection, "issue.create", {"title": "Target", "severity": "P1", "evidence": "Repeated"}, "issue")
        assert issue == "ISS-001"
        with Repository(connection).snapshot() as query:
            assert query.relations("bug", bug, field="promoted_to")["items"][0]["resolved"]
            assert query.get("bug", bug)["fields"]["promoted_to"] == "ISS-001"


@pytest.mark.parametrize("operation,arguments", [
    ("issue.create", {"title": [], "severity": "P1", "evidence": "text"}),
    ("decision.create", {"title": "Choose", "options": ["one", 2]}),
    ("handover.create", {"tldr": "Context", "task_ids": "same"}),
    ("bug.update", {"id": "B-1", "patch": {"location": "single-string"}}),
    ("idea.update", {"id": "IDEA-1", "patch": {"archived": "yes"}}),
])
def test_lifecycle_shape_errors_are_rejected_before_database_admission(native, operation, arguments):
    with closing(sqlite3.connect(native, isolation_level=None)) as connection:
        statements = []
        connection.set_trace_callback(statements.append)
        with pytest.raises(ValueError):
            execute(connection, envelope(operation, arguments))
        assert statements == []
