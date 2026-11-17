from contextlib import closing
import sqlite3

import pytest

from taskmaster.native.migrate import backfill
from taskmaster.native.queries import Repository
from test_native_migration import legacy  # noqa: F401


def test_document_sections_use_stored_prose_and_report_unimported_refs(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        connection.execute("UPDATE entities SET body='## Notes\nExact notes\n\n## Blockers\nDo this\n' WHERE kind='handover'")
        connection.execute("UPDATE entities SET doc=json_set(doc,'$.notes','Inline notes','$.docs',json('{\"spec\":\"docs/spec.md\"}')) WHERE kind='task'")
        backfill(connection)
        with Repository(connection).snapshot() as query:
            handover = query.document("handover", "H-1", sections=["notes"])
            assert handover["sections"] == {"notes": "Exact notes"}
            task = query.document("task", "same", sections=["notes", "spec"])
            assert task["sections"] == {"notes": "Inline notes"}
            assert task["unresolved"] == [{"section": "spec", "path": "docs/spec.md", "reason": "not_imported"}]
            assert task["incomplete"] is True
            assert query.document("task", "same")["body"] == "Exact prose\r\n\n雪\n"
            with pytest.raises(ValueError, match="canonical"):
                query.document("handover", "H-1", sections=["invented"])


def test_imported_external_section_is_snapshot_data_and_path_changes_do_not_serve_stale_body(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        connection.execute("UPDATE entities SET doc=json_set(doc,'$.docs',json('{\"spec\":\"docs/spec.md\"}')) WHERE kind='task'")
        backfill(connection)
        key = connection.execute("SELECT entity_key FROM entity_core WHERE kind='task'").fetchone()[0]
        connection.execute("INSERT INTO external_documents VALUES(?,'spec','docs/spec.md','Exact imported section','hash',13)", (key,))
        with Repository(connection).snapshot() as query:
            result = query.document("task", "same", sections=["spec"])
            assert result["sections"] == {"spec": "Exact imported section"}
            assert not result["incomplete"]
            assert result["provenance"]["spec"]["imported_seq"] == 13
        connection.execute("UPDATE entities SET doc=json_set(doc,'$.docs.spec','docs/other.md') WHERE kind='task'")
        backfill(connection)
        with Repository(connection).snapshot() as query:
            result = query.document("task", "same", sections=["spec"])
            assert result["sections"] == {}
            assert result["incomplete"]


# -- The narrow, caller-initiated importer (N09 S11, decision D7) -------------


def _activate(connection):
    """Test-only activation; no runtime entry point makes a project native."""
    connection.execute("BEGIN IMMEDIATE")
    connection.execute("UPDATE meta SET value='2' WHERE key='schema_version'")
    connection.execute("INSERT INTO meta VALUES('minimum_client_protocol','2')")
    connection.execute("UPDATE native_manifest SET value='native' WHERE key='authority'")
    connection.execute("UPDATE native_manifest SET value='ready' WHERE key='state'")
    connection.execute("INSERT INTO native_manifest VALUES('local_state_imported','1')")
    connection.commit()


def _documented(connection):
    connection.execute("UPDATE entities SET doc=json_set(doc,'$.docs',"
                       "json('{\"spec\":\"docs/spec.md\"}')) WHERE kind='task'")
    backfill(connection)
    _activate(connection)


def _import(connection, key, **overrides):
    from taskmaster.native.commands import execute
    arguments = {"kind": "task", "id": "same", "section": "spec", "path": "docs/spec.md",
                 "body": "Imported prose.\n"}
    arguments.update(overrides)
    return execute(connection, {"protocol": 2, "store_id": "fixture-store", "caller_scope": "tests",
                                "request_id": key, "operation": "document.import",
                                "arguments": arguments, "expected_revisions": []})


def test_import_stores_prose_the_retriever_serves_with_an_honest_hash_and_sequence(legacy):
    import hashlib
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        _documented(connection)
        with Repository(connection).snapshot() as query:
            before = query.get("task", "same", fields=["id"])["revision"]
        receipt = _import(connection, "first")
        assert receipt["affected"] == [{"kind": "task", "id": "same", "revision": before,
                                        "last_seq": receipt["commit_seq"], "fields": {}}]
        assert connection.execute("SELECT COUNT(*) FROM domain_events WHERE op='document.import'"
                                  ).fetchone()[0] == 1
        assert connection.execute("SELECT value FROM native_manifest WHERE key='event_high_water'"
                                  ).fetchone()[0] == str(receipt["commit_seq"])
        with Repository(connection).snapshot() as query:
            result = query.document("task", "same", sections=["spec"])
            assert result["sections"] == {"spec": "Imported prose.\n"}
            assert not result["incomplete"]
            assert result["provenance"]["spec"] == {
                "path": "docs/spec.md",
                "content_hash": hashlib.sha256("Imported prose.\n".encode("utf-8")).hexdigest(),
                "imported_seq": receipt["commit_seq"]}
            # An import is prose, not an authored field: the entity does not revise.
            assert query.get("task", "same", fields=["id"])["revision"] == before


def test_reimporting_identical_prose_is_a_no_op_and_changed_prose_advances_the_sequence(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        _documented(connection)
        first = _import(connection, "first")
        again = _import(connection, "again")
        assert again["affected"] == [] and again["projection_state"] == "unchanged"
        assert connection.execute("SELECT imported_seq FROM external_documents").fetchone()[0] == first["commit_seq"]
        changed = _import(connection, "changed", body="Edited prose.\n")
        assert changed["commit_seq"] > first["commit_seq"]
        with Repository(connection).snapshot() as query:
            result = query.document("task", "same", sections=["spec"])
            assert result["sections"] == {"spec": "Edited prose.\n"}
            assert result["provenance"]["spec"]["imported_seq"] == changed["commit_seq"]


@pytest.mark.parametrize("overrides,message", [
    ({"path": "docs/other.md"}, "does not declare"),
    ({"section": "notes"}, "section must be"),
    ({"kind": "handover", "id": "H-1"}, "only task documents"),
    ({"id": "ghost"}, "ghost"),
    ({"body": 7}, "body must be text"),
])
def test_import_refuses_what_the_retriever_could_never_serve(legacy, overrides, message):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        _documented(connection)
        with pytest.raises((ValueError, KeyError), match=message):
            _import(connection, "refused", **overrides)
        assert connection.execute("SELECT COUNT(*) FROM external_documents").fetchone()[0] == 0


# -- team-server-012: uploads, the expected_hash precondition and the restore form ---------


def _events(connection):
    import json
    return [json.loads(row[0]) for row in connection.execute(
        "SELECT after FROM domain_events WHERE op='document.import' ORDER BY seq")]


def _provenance(connection):
    with Repository(connection).snapshot() as query:
        return query.document("task", "same", sections=["spec"])["provenance"]["spec"]


def _sha(text):
    import hashlib
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


@pytest.mark.parametrize("extra", [
    {"source": "upload"}, {"expected_hash": ""}, {"expected_hash": "a" * 64},
    {"source": "upload", "expected_hash": "b" * 64},
    {"restore": True, "source": "upload", "imported_by": "ann"},
    {"restore": True, "source": "file", "imported_by": "", "original_seq": 812},
])
def test_validate_accepts_the_012_forms(extra):
    from taskmaster.native.documents import validate
    validate("document.import", {"kind": "task", "id": "same", "section": "spec", "path": "docs/spec.md",
                                 "body": "x", **extra})


@pytest.mark.parametrize("extra,message", [
    ({"source": "file"}, "may only be"), ({"source": "restore"}, "may only be"),
    ({"expected_hash": "A" * 64}, "sha256"), ({"expected_hash": "abc"}, "sha256"), ({"expected_hash": None}, "sha256"),
    ({"imported_by": "ann"}, "optionally"), ({"original_seq": 3}, "optionally"), ({"bogus": 1}, "optionally"),
    ({"restore": False, "source": "upload", "imported_by": "ann"}, "restore must be true"),
    ({"restore": 1, "source": "upload", "imported_by": "ann"}, "restore must be true"),
    ({"restore": True, "imported_by": "ann"}, "needs source"),
    ({"restore": True, "source": "restore", "imported_by": "ann"}, "needs source"),
    ({"restore": True, "source": "upload"}, "imported_by"),
    ({"restore": True, "source": "upload", "imported_by": "Not A Handle"}, "imported_by"),
    ({"restore": True, "source": "upload", "imported_by": "ann", "expected_hash": ""}, "never expected_hash"),
    ({"restore": True, "source": "upload", "imported_by": "ann", "original_seq": 0}, "positive"),
    ({"restore": True, "source": "upload", "imported_by": "ann", "original_seq": True}, "positive"),
])
def test_validate_refuses_the_wrong_012_forms(extra, message):
    from taskmaster.native.documents import validate
    with pytest.raises(ValueError, match=message):
        validate("document.import", {"kind": "task", "id": "same", "section": "spec", "path": "docs/spec.md",
                                     "body": "x", **extra})


def test_an_upload_records_its_source_and_a_file_import_is_unchanged(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        _documented(connection)
        _import(connection, "file-1")
        _import(connection, "upload-1", body="Uploaded.\n", source="upload")
        file_after, upload_after = _events(connection)
        assert file_after == {"section": "spec", "path": "docs/spec.md", "content_hash": _sha("Imported prose.\n")}
        assert upload_after == {"section": "spec", "path": "docs/spec.md", "content_hash": _sha("Uploaded.\n"),
                                "source": "upload"}
        assert _provenance(connection)["origin"] == "upload"


def test_a_file_imports_provenance_is_exactly_todays(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        _documented(connection)
        receipt = _import(connection, "file-1")
        assert _provenance(connection) == {"path": "docs/spec.md", "content_hash": _sha("Imported prose.\n"),
                                           "imported_seq": receipt["commit_seq"]}


def test_expected_hash_guards_the_replace(legacy):
    from taskmaster.native.contracts import Conflict
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        _documented(connection)
        with pytest.raises(Conflict, match="nothing is imported under docs/spec.md"):
            _import(connection, "stale-0", expected_hash="c" * 64)
        first = _import(connection, "first", expected_hash="")
        events = len(_events(connection))
        with pytest.raises(Conflict, match="the stored copy changed") as caught:
            _import(connection, "stale-1", body="Other.\n", expected_hash="")
        assert _sha("Imported prose.\n") in str(caught.value)
        assert 'backlog_document(kind="task", entity_id="same", sections=["spec"]' in str(caught.value)
        with pytest.raises(Conflict, match="the stored copy changed"):
            _import(connection, "stale-2", body="Other.\n", expected_hash="d" * 64)
        assert len(_events(connection)) == events
        assert connection.execute("SELECT body FROM external_documents").fetchone()[0] == "Imported prose.\n"
        replaced = _import(connection, "fresh", body="Other.\n", expected_hash=_sha("Imported prose.\n"))
        assert replaced["commit_seq"] > first["commit_seq"]


def test_identical_text_wins_over_a_stale_hash(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        _documented(connection)
        _import(connection, "first")
        again = _import(connection, "again", expected_hash="e" * 64)
        assert again["affected"] == [] and again["projection_state"] == "unchanged"


def test_a_copy_under_an_older_path_counts_as_nothing_stored(legacy):
    from taskmaster.native.contracts import Conflict
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        _documented(connection)
        _import(connection, "first")
        from taskmaster.native.commands import _write_field   # test-only: the task now declares another path
        key = connection.execute("SELECT entity_key FROM entity_core WHERE kind='task'").fetchone()[0]
        _write_field(connection, key, "task", "docs", {"spec": "docs/moved.md"})
        with pytest.raises(Conflict, match="nothing is imported under docs/moved.md"):
            _import(connection, "moved-1", path="docs/moved.md", expected_hash=_sha("Imported prose.\n"))
        assert _import(connection, "moved-2", path="docs/moved.md", expected_hash="")["affected"]


def test_a_restore_keeps_the_manifests_source_and_importer(legacy):
    from taskmaster.native.documents import import_origin
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        _documented(connection)
        receipt = _import(connection, "restore-1", restore=True, source="upload", imported_by="ann", original_seq=812)
        assert _events(connection)[-1] == {"section": "spec", "path": "docs/spec.md",
                                           "content_hash": _sha("Imported prose.\n"), "source": "restore",
                                           "restored_source": "upload", "imported_by": "ann", "original_seq": 812}
        facts = _provenance(connection)
        assert facts["origin"] == "restore" and facts["restored_origin"] == "upload"
        assert facts["imported_by"] == "ann" and facts["original_seq"] == 812
        # What 013 backs up from a restored copy is what it was restored with.
        origin = import_origin(connection, receipt["commit_seq"])
        assert (origin["source"], origin["imported_by"]) == ("upload", "ann")
        assert _import(connection, "restore-2", restore=True, source="upload", imported_by="ann")["affected"] == []
