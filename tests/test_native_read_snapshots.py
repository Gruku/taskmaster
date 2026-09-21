from contextlib import closing
import sqlite3

from taskmaster.native.migrate import backfill
from taskmaster.native.queries import Repository
from test_native_migration import legacy  # noqa: F401


def test_composite_reads_share_old_committed_snapshot_during_peer_write(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        backfill(connection)
        with Repository(connection).snapshot() as query:
            first = query.get("task", "same")
            with closing(sqlite3.connect(legacy, isolation_level=None)) as peer:
                peer.execute("UPDATE entities SET doc=json_set(doc,'$.title','newer'),rev=rev+1 WHERE kind='task'")
            assert query.get("task", "same") == first
            assert query.summary("task")["total"] == 1


def test_bounded_metadata_query_work_does_not_scale_with_unrelated_entities(legacy):
    with closing(sqlite3.connect(legacy, isolation_level=None)) as connection:
        def work():
            steps = [0]
            def progress():
                steps[0] += 1
                return 0
            connection.set_progress_handler(progress, 1)
            try:
                with Repository(connection).snapshot() as query:
                    assert query.get("task", "same", fields=["id", "title"])["id"] == "same"
            finally:
                connection.set_progress_handler(None, 0)
            return steps[0]
        backfill(connection)
        small = work()
        connection.execute("WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<2000) "
                           "INSERT INTO entities SELECT 'note','extra-'||x,NULL,NULL,0,0,json_object('id','extra-'||x,'custom',printf('%01000d',x)),NULL,1,1 FROM n")
        backfill(connection)
        large = work()
        assert large < small * 1.5
        # The count includes parsing the schema; 6.0.3's projection_conflict table
        # and index took the flat cost from 980 to 1000 steps.
        assert large < 1020
