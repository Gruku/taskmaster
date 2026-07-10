from pathlib import Path
import json
import pytest
import yaml

from taskmaster import backlog_server as bs


@pytest.fixture
def tm_dir(tmp_path: Path, monkeypatch) -> Path:
    d = tmp_path / ".taskmaster"
    d.mkdir()
    (d / "backlog.yaml").write_text(yaml.safe_dump({
        "meta": {"schema_version": 3},
        "epics": [{"id": "e1", "title": "E", "tasks": [
            {"id": "T-001", "title": "First", "status": "todo"},
            {"id": "T-002", "title": "Second", "status": "todo"},
            {"id": "T-003", "title": "Third", "status": "todo"},
        ]}],
    }))
    for sub in ("handovers", "issues", "ideas", "tasks"):
        (d / sub).mkdir()
    monkeypatch.setattr(bs, "_backlog_path", lambda: d / "backlog.yaml")
    bs.backlog_link_create(source="T-001", target="T-002", type="depends_on")
    bs.backlog_link_create(source="T-002", target="T-003", type="depends_on")
    return d


def test_query_by_source(tm_dir):
    out = bs.backlog_link_query(source="T-001")
    data = json.loads(out)
    assert {"source": "T-001", "target": "T-002", "type": "depends_on"} in data


def test_query_by_target(tm_dir):
    out = bs.backlog_link_query(target="T-002")
    data = json.loads(out)
    # T-001 depends_on T-002 (forward); T-002 has 'blocks' to T-001 (inverse, source-side).
    sources = {entry["source"] for entry in data}
    assert "T-001" in sources


def test_query_by_type(tm_dir):
    out = bs.backlog_link_query(type="depends_on")
    data = json.loads(out)
    pairs = {(entry["source"], entry["target"]) for entry in data}
    assert ("T-001", "T-002") in pairs
    assert ("T-002", "T-003") in pairs


def test_query_depth_two_traverses(tm_dir):
    out = bs.backlog_link_query(source="T-001", type="depends_on", depth=2)
    data = json.loads(out)
    pairs = {(entry["source"], entry["target"]) for entry in data}
    # depth=2 surfaces T-001 -> T-002 AND T-002 -> T-003.
    assert ("T-001", "T-002") in pairs
    assert ("T-002", "T-003") in pairs


def test_query_depth_one_does_not_traverse(tm_dir):
    out = bs.backlog_link_query(source="T-001", type="depends_on", depth=1)
    data = json.loads(out)
    pairs = {(entry["source"], entry["target"]) for entry in data}
    assert ("T-001", "T-002") in pairs
    assert ("T-002", "T-003") not in pairs


# ── B-036: nonexistent source must return an error, not empty list ──

def test_query_nonexistent_source_returns_error(tm_dir):
    """A well-formed but nonexistent source ID must return an error, not '[]'."""
    out = bs.backlog_link_query(source="T-999")
    assert out.startswith("Error: source"), (
        f"Expected 'Error: source ...' string, got: {out!r}"
    )
    assert "not found" in out, f"Expected 'not found' in error, got: {out!r}"


def test_query_existing_source_no_links_returns_empty_list(tm_dir):
    """An existing source with no outgoing links still returns '[]' (not an error).

    T-004 is added to the epic but has no link_create calls, so edges_from
    returns an empty list rather than an error.
    """
    import yaml
    bp = tm_dir / "backlog.yaml"
    data = yaml.safe_load(bp.read_text())
    data["epics"][0]["tasks"].append({"id": "T-004", "title": "Fourth", "status": "todo"})
    bp.write_text(yaml.safe_dump(data))

    out = bs.backlog_link_query(source="T-004")
    assert out == "[]", f"Expected '[]' for source with no links, got: {out!r}"
