"""N10's before-state, retained as a characterization of the compatibility route."""
import json

import pytest

from native_twins import make_twins
from test_native_routing_viewer import _seed, _serve


@pytest.fixture
def twins(tmp_path, monkeypatch):
    return make_twins(tmp_path, monkeypatch, _seed)


@pytest.mark.parametrize("authority", ["legacy", "native"])
def test_compatibility_payload_characterization(twins, authority):
    root = getattr(twins, authority)
    status, raw, etag = _serve(twins, root, "GET", "/api/backlog")
    body = json.loads(raw)
    assert status == 200 and etag
    assert body["tasks"] == [dict(t, epic=e["id"]) for e in body["epics"] for t in e["tasks"]]
    assert any(t.get("notes") for t in body["tasks"])
    print(f"N10 baseline {authority}: backlog={len(raw.encode('utf-8'))} bytes")
