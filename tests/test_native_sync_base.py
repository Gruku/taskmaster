"""N13 three-way bases are the bytes actually published, never a later render."""
import hashlib

import pytest

from taskmaster import backlog_server as bs
from taskmaster.native import projection
from native_twins import commit_only, make_twins, native_connection


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch,
                      lambda: bs.backlog_add_task(title="Original", epic="test-epic", phase="dev"),
                      engine_oracle=True)


def test_ack_preserves_exact_published_bytes_and_replaces_prior_base(twins):
    with native_connection(twins.native) as connection:
        rel = "tasks/test-epic-001.md"
        for title in ("first", "second"):
            commit_only(connection, "task.patch", {"id": "test-epic-001", "set": {"title": title}})
            exporter = projection.Exporter(connection, twins.native / ".taskmaster", owner=title, session=title)
            (job,) = exporter.claim()
            content = f"---\r\nid: test-epic-001\r\ntitle: {title}\r\n---\r\n".encode()
            exporter.intend([(job, content)])
            assert exporter.publish(job, content) == "exported"
            exporter.finish()
            digest, base = connection.execute(
                "SELECT p.content_hash,b.content FROM projection p JOIN projection_base b USING(file) WHERE file=?",
                (rel,)).fetchone()
            assert base == content == (twins.native / ".taskmaster" / rel).read_bytes()
            assert digest == hashlib.sha1(base).hexdigest()


def test_agreeing_file_base_keeps_observed_line_endings(twins):
    with native_connection(twins.native) as connection:
        rel = "tasks/test-epic-001.md"
        commit_only(connection, "task.patch", {"id": "test-epic-001", "set": {"title": "Changed"}})
        content = b"---\nid: test-epic-001\ntitle: Changed\n---\n"
        observed = content.replace(b"\n", b"\r\n")
        (twins.native / ".taskmaster" / rel).write_bytes(observed)
        exporter = projection.Exporter(connection, twins.native / ".taskmaster", owner="same", session="same")
        (job,) = exporter.claim()
        exporter.intend([(job, content)])
        assert exporter.publish(job, content) == "exported"
        exporter.finish()
        assert connection.execute("SELECT content FROM projection_base WHERE file=?", (rel,)).fetchone()[0] == observed
