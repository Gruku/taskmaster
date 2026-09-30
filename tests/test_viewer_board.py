"""Approved N10 wire contract: compact, coherent, conditional, row-based."""
import json
from urllib.parse import quote

import pytest

from taskmaster import backlog_server as bs
from native_twins import make_twins
from test_native_routing_viewer import _seed, _serve


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


def board(twins, root, since=None, **headers):
    path = "/api/board" + ("?since=" + quote(since) if since else "")
    status, raw, etag = _serve(twins, root, "GET", path, headers=headers)
    assert status in (200, 304), (status, raw)
    return status, json.loads(raw) if raw else None, etag


@pytest.mark.parametrize("authority", ["legacy", "native"])
def test_compact_and_conditional_without_build(twins, authority, monkeypatch):
    root = getattr(twins, authority)
    status, full, etag = board(twins, root)
    assert status == 200
    assert full["revision"] == full["cursor"] == etag.strip('"')
    assert set(full) == {"tasks", "epics", "phases", "revision", "cursor"}
    assert len({t["id"] for t in full["tasks"]}) == len(full["tasks"])
    assert all("_body" not in t and "locked_by" not in t and "description" not in t for t in full["tasks"])
    assert all("tasks" not in e and "_body" not in e for e in full["epics"])
    from taskmaster import viewer_dto
    def forbidden(*a, **kw):
        pytest.fail("304 constructed the DTO")
    monkeypatch.setattr(viewer_dto, "build_board", forbidden)
    assert board(twins, root, **{"If-None-Match": etag}) == (304, None, etag)
    with twins.at(root):
        bs.backlog_bug_create(title="Unrelated bug")
    assert board(twins, root, full["cursor"])[0] == 304


@pytest.mark.parametrize("authority", ["legacy", "native"])
def test_delta_and_resync(twins, authority):
    root = getattr(twins, authority)
    _, before, _ = board(twins, root)
    with twins.at(root):
        bs.backlog_update_task(task_id="test-epic-001", field="title", value="Peer edit")
    status, delta, _ = board(twins, root, before["cursor"])
    assert status == 200 and delta["since"] == before["cursor"]
    assert [t["id"] for t in delta["tasks_upsert"]] == ["test-epic-001"]
    assert delta["tasks_remove"] == []
    _, after, _ = board(twins, root)
    patched = {t["id"]: t for t in before["tasks"]}
    patched.update({t["id"]: t for t in delta["tasks_upsert"]})
    assert list(patched.values()) == after["tasks"]
    _, resync, _ = board(twins, root, "broken")
    assert resync["resync"] == "bad_token" and resync["tasks"] == after["tasks"]


def test_board_twins(twins):
    legacy = board(twins, twins.legacy)[1]
    native = board(twins, twins.native)[1]
    for key in ("tasks", "epics", "phases"):
        assert legacy[key] == native[key]
