"""Area entity — file helper + MCP tool tests (epic B task 1).

An Area is a long-lived subsystem/workstream with NO status lifecycle:
create/get/list/update only, no archive.
"""
from __future__ import annotations

import pytest

from taskmaster.taskmaster_v3 import (
    area_path,
    list_area_ids,
    list_areas,
    read_area,
    update_area,
    write_area,
)


@pytest.fixture()
def bp(tmp_path):
    tm = tmp_path / ".taskmaster"
    tm.mkdir()
    p = tm / "backlog.yaml"
    p.write_text("meta:\n  project: test\nepics: []\nphases: []\n", encoding="utf-8")
    return p


def _fm(area_id="desktop-app", name="Desktop App", **extra):
    fm = {
        "id": area_id,
        "name": name,
        "description": extra.pop("description", ""),
        "anchors": extra.pop("anchors", []),
        "created": "2026-07-08T00:00:00Z",
    }
    fm.update(extra)
    return fm


# ── v3 module: write / read / update / list ─────────────────────────────────


def test_write_area_creates_file(bp):
    target = write_area(bp, _fm())
    assert target == area_path(bp, "desktop-app")
    assert target.exists()


def test_write_area_frontmatter_roundtrip(bp):
    write_area(bp, _fm(description="the desktop client", anchors=["desktop/**"]))
    fm, body = read_area(bp, "desktop-app")
    assert fm["id"] == "desktop-app"
    assert fm["name"] == "Desktop App"
    assert fm["description"] == "the desktop client"
    assert fm["anchors"] == ["desktop/**"]
    assert fm["created"] == "2026-07-08T00:00:00Z"
    assert body == ""


def test_write_area_rejects_non_kebab_id(bp):
    with pytest.raises(ValueError):
        write_area(bp, _fm(area_id="Desktop App"))


def test_write_area_rejects_empty_name(bp):
    with pytest.raises(ValueError):
        write_area(bp, _fm(name=""))


def test_write_area_rejects_duplicate_id(bp):
    write_area(bp, _fm())
    with pytest.raises(ValueError):
        write_area(bp, _fm())


def test_write_area_rejects_status_field(bp):
    with pytest.raises(ValueError):
        write_area(bp, _fm(status="active"))


def test_read_area_missing_raises(bp):
    with pytest.raises(FileNotFoundError):
        read_area(bp, "nope")


def test_update_area_field(bp):
    write_area(bp, _fm())
    fm, _ = update_area(bp, "desktop-app", {"description": "renamed desc"})
    assert fm["description"] == "renamed desc"
    fm2, _ = read_area(bp, "desktop-app")
    assert fm2["description"] == "renamed desc"


def test_update_area_anchors_roundtrip(bp):
    write_area(bp, _fm())
    fm, _ = update_area(bp, "desktop-app", {"anchors": ["desktop/**", "docs/desktop/**"]})
    assert fm["anchors"] == ["desktop/**", "docs/desktop/**"]


def test_update_area_rejects_status_field(bp):
    write_area(bp, _fm())
    with pytest.raises(ValueError):
        update_area(bp, "desktop-app", {"status": "done"})


def test_update_area_missing_raises(bp):
    with pytest.raises(FileNotFoundError):
        update_area(bp, "nope", {"description": "x"})


def test_list_area_ids_empty_on_fresh_backlog(bp):
    assert list_area_ids(bp) == []


def test_list_area_ids_sorted(bp):
    write_area(bp, _fm(area_id="viewer", name="Viewer"))
    write_area(bp, _fm(area_id="desktop-app", name="Desktop App"))
    assert list_area_ids(bp) == ["desktop-app", "viewer"]


def test_list_areas_includes_bodies(bp):
    write_area(bp, _fm())
    areas = list_areas(bp)
    assert len(areas) == 1
    assert areas[0]["id"] == "desktop-app"
    assert "body" in areas[0]


# ── MCP tool wrappers ────────────────────────────────────────────────────────


def _tool(t):
    """Call a FastMCP tool object or plain function uniformly."""
    return getattr(t, "fn", t)


def test_mcp_area_create_and_get(bp, monkeypatch):
    from taskmaster import backlog_server
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    out = _tool(backlog_server.backlog_area_create)(
        area_id="desktop-app", name="Desktop App", description="the client",
        anchors=["desktop/**"],
    )
    assert "desktop-app" in out
    fm, _ = read_area(bp, "desktop-app")
    assert fm["name"] == "Desktop App"
    assert fm["anchors"] == ["desktop/**"]

    got = _tool(backlog_server.backlog_area_get)("desktop-app")
    assert "desktop-app" in got
    assert "Desktop App" in got


def test_mcp_area_create_rejects_non_kebab_id(bp, monkeypatch):
    from taskmaster import backlog_server
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    out = _tool(backlog_server.backlog_area_create)(area_id="Desktop App", name="Desktop App")
    assert out.startswith("Error:")


def test_mcp_area_create_rejects_duplicate(bp, monkeypatch):
    from taskmaster import backlog_server
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    _tool(backlog_server.backlog_area_create)(area_id="desktop-app", name="Desktop App")
    out = _tool(backlog_server.backlog_area_create)(area_id="desktop-app", name="Desktop App Again")
    assert out.startswith("Error:")


def test_mcp_area_list(bp, monkeypatch):
    from taskmaster import backlog_server
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    _tool(backlog_server.backlog_area_create)(area_id="desktop-app", name="Desktop App")
    _tool(backlog_server.backlog_area_create)(area_id="viewer", name="Viewer")
    listing = _tool(backlog_server.backlog_area_list)()
    assert "desktop-app" in listing
    assert "viewer" in listing


def test_mcp_area_list_empty(bp, monkeypatch):
    from taskmaster import backlog_server
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    listing = _tool(backlog_server.backlog_area_list)()
    assert "no areas" in listing.lower()


def test_mcp_area_update_field(bp, monkeypatch):
    from taskmaster import backlog_server
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    _tool(backlog_server.backlog_area_create)(area_id="desktop-app", name="Desktop App")
    out = _tool(backlog_server.backlog_area_update)("desktop-app", "description", "updated desc")
    assert "updated" in out.lower() or "desktop-app" in out
    fm, _ = read_area(bp, "desktop-app")
    assert fm["description"] == "updated desc"


def test_mcp_area_update_anchors_json(bp, monkeypatch):
    from taskmaster import backlog_server
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    _tool(backlog_server.backlog_area_create)(area_id="desktop-app", name="Desktop App")
    _tool(backlog_server.backlog_area_update)("desktop-app", "anchors", '["desktop/**", "docs/desktop/**"]')
    fm, _ = read_area(bp, "desktop-app")
    assert fm["anchors"] == ["desktop/**", "docs/desktop/**"]


def test_mcp_area_update_rejects_bad_field(bp, monkeypatch):
    from taskmaster import backlog_server
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    _tool(backlog_server.backlog_area_create)(area_id="desktop-app", name="Desktop App")
    out = _tool(backlog_server.backlog_area_update)("desktop-app", "status", "active")
    assert out.startswith("Error:")


def test_mcp_area_update_missing(bp, monkeypatch):
    from taskmaster import backlog_server
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    out = _tool(backlog_server.backlog_area_update)("nope", "name", "x")
    assert "not found" in out.lower()
