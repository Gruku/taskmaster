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
