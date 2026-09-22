"""N10 task concurrency and live claim read boundary, both authorities."""
import json

import pytest

from taskmaster import backlog_server as bs
from native_twins import make_twins
from test_native_routing_viewer import _seed, _serve


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


@pytest.mark.parametrize("authority", ["legacy", "native"])
def test_detail_and_first_edit_precondition(twins, authority):
    root = getattr(twins, authority)
    status, raw, etag = _serve(twins, root, "GET", "/api/task/test-epic-001/detail")
    assert status == 200
    detail = json.loads(raw)
    assert detail["task"]["id"] == "test-epic-001"
    assert detail["etag"] == etag.strip('"') and etag.startswith('"t1:')
    assert "locked_by" not in detail["task"] and "claim_expires" not in detail["task"]
    assert detail["claim"]["state"] == "released"
    with twins.at(root):
        bs.backlog_update_task(task_id="test-epic-002", field="title", value="Unrelated")
    status, raw, new_etag = _serve(twins, root, "PATCH", "/api/tasks/test-epic-001", {"title": "Saved"},
                                  headers={"If-Match": etag})
    assert status == 200, raw
    assert new_etag.startswith('"t1:') and new_etag != etag
    status, raw, _ = _serve(twins, root, "PATCH", "/api/tasks/test-epic-001", {"title": "Stale"},
                            headers={"If-Match": etag})
    assert status == 409 and json.loads(raw)["current"]["title"] == "Saved"


def test_native_detail_and_patch_do_not_materialize_tree(twins, monkeypatch):
    from taskmaster.native_routing import reads
    def forbidden(*a, **kw):
        raise AssertionError("targeted viewer path built the full tree")
    monkeypatch.setattr(reads, "tree", forbidden)
    status, _, etag = _serve(twins, twins.native, "GET", "/api/task/test-epic-001/detail")
    assert status == 200
    status, raw, _ = _serve(twins, twins.native, "PATCH", "/api/tasks/test-epic-001", {"title": "Targeted"},
                            headers={"If-Match": etag})
    assert status == 200, raw


def test_combined_detail_twins(twins):
    from native_twins import normalize
    values = []
    for root in (twins.legacy, twins.native):
        status, raw, _ = _serve(twins, root, 'GET', '/api/task/test-epic-001/detail')
        assert status == 200
        value = json.loads(raw)
        value.pop('etag')
        text = json.dumps(value).replace(str(root).replace('\\', '\\\\'), '<root>')
        values.append(normalize(json.loads(text)))
    assert values[0] == values[1]
