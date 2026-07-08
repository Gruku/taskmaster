from taskmaster import taskmaster_v3 as v3
from taskmaster.backlog_server import (
    backlog_add_epic, backlog_update_epic, backlog_validate, _load,
)


def _epic(data, eid):
    return next(e for e in data["epics"] if e["id"] == eid)


def test_add_epic_without_done_when_rejected(tmp_taskmaster):
    out = backlog_add_epic(epic_id="asset-engine", name="Asset Engine", done_when="")
    assert out == (
        "Error: Epics are finite: 'done_when' is required. "
        "An epic that can't say when it's done is an area."
    )
    data = _load()
    assert not any(e["id"] == "asset-engine" for e in data["epics"])


def test_add_epic_missing_done_when_kwarg_rejected(tmp_taskmaster):
    # done_when omitted entirely (no default) -> TypeError from the call itself
    import pytest
    with pytest.raises(TypeError):
        backlog_add_epic(epic_id="asset-engine", name="Asset Engine")


def test_add_epic_with_done_when_succeeds_and_survives_reload(tmp_taskmaster):
    out = backlog_add_epic(
        epic_id="asset-engine", name="Asset Engine",
        done_when="ingest + thumbnail + CDN pipeline ships",
    )
    assert "Error" not in out
    data = _load()
    e = _epic(data, "asset-engine")
    assert e["done_when"] == "ingest + thumbnail + CDN pipeline ships"

    # Round-trip through save_v3/load_v3 — slim survival.
    bp = tmp_taskmaster / ".taskmaster" / "backlog.yaml"
    v3.save_v3(bp, v3.load_v3(bp))
    reloaded = v3.load_v3(bp)
    assert _epic(reloaded, "asset-engine")["done_when"] == (
        "ingest + thumbnail + CDN pipeline ships"
    )


def test_update_epic_done_when_to_empty_rejected(tmp_taskmaster):
    backlog_add_epic(epic_id="asset-engine", name="Asset Engine", done_when="ships v1")
    out = backlog_update_epic("asset-engine", "done_when", "")
    assert out == (
        "Error: Epics are finite: 'done_when' is required. "
        "An epic that can't say when it's done is an area."
    )
    e = _epic(_load(), "asset-engine")
    assert e["done_when"] == "ships v1"


def test_update_epic_done_when_roundtrip(tmp_taskmaster):
    backlog_add_epic(epic_id="asset-engine", name="Asset Engine", done_when="ships v1")
    out = backlog_update_epic("asset-engine", "done_when", "ships v2")
    assert "Error" not in out
    e = _epic(_load(), "asset-engine")
    assert e["done_when"] == "ships v2"


def test_add_epic_area_unknown_rejected(tmp_taskmaster):
    out = backlog_add_epic(
        epic_id="asset-engine", name="Asset Engine", done_when="ships v1",
        area="ghost-area",
    )
    assert "Error" in out and "ghost-area" in out
    data = _load()
    assert not any(e["id"] == "asset-engine" for e in data["epics"])


def test_add_epic_area_known_accepted(tmp_taskmaster):
    from taskmaster.backlog_server import backlog_area_create
    backlog_area_create(area_id="desktop-app", name="Desktop App")
    out = backlog_add_epic(
        epic_id="asset-engine", name="Asset Engine", done_when="ships v1",
        area="desktop-app",
    )
    assert "Error" not in out
    e = _epic(_load(), "asset-engine")
    assert e["area"] == "desktop-app"


def test_add_epic_area_empty_string_allowed(tmp_taskmaster):
    out = backlog_add_epic(
        epic_id="asset-engine", name="Asset Engine", done_when="ships v1", area="",
    )
    assert "Error" not in out
    e = _epic(_load(), "asset-engine")
    assert e.get("area", "") == ""


def test_update_epic_area_unknown_rejected(tmp_taskmaster):
    backlog_add_epic(epic_id="asset-engine", name="Asset Engine", done_when="ships v1")
    out = backlog_update_epic("asset-engine", "area", "ghost-area")
    assert "Error" in out and "ghost-area" in out


def test_update_epic_area_known_accepted(tmp_taskmaster):
    from taskmaster.backlog_server import backlog_area_create
    backlog_area_create(area_id="viewer", name="Viewer")
    backlog_add_epic(epic_id="asset-engine", name="Asset Engine", done_when="ships v1")
    out = backlog_update_epic("asset-engine", "area", "viewer")
    assert "Error" not in out
    e = _epic(_load(), "asset-engine")
    assert e["area"] == "viewer"


def test_validate_warns_on_legacy_epic_without_done_when(tmp_taskmaster):
    data = _load()
    data["epics"].append({
        "id": "legacy-epic", "name": "Legacy Epic", "status": "active",
        "created": "2026-01-01", "tasks": [],
    })
    from taskmaster.backlog_server import _mutate_and_save
    _mutate_and_save(data)
    out = backlog_validate()
    assert "legacy-epic" in out
    assert "done_when" in out.lower()


def test_epic_heavy_fields_unchanged():
    assert v3.EPIC_HEAVY_FIELDS == ("description", "docs", "components")
