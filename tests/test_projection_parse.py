"""N13: both stores interpret authored projection bytes identically, without I/O."""
from copy import deepcopy

import pytest

from taskmaster import projection_parse as parse


def test_task_prose_unknown_fields_and_private_keys():
    content = (b"---\r\nid: app-001\r\ntitle: Authored\r\ncustom:\r\n"
               b"  nested: [1, false]\r\n  _cache: hidden\r\n---\r\n## Notes\r\nText\r\n")
    assert parse.projected_file("task", "app-001", content) == {
        ("task", "app-001"): ({"id": "app-001", "title": "Authored",
                                "custom": {"nested": [1, False]}}, "## Notes\nText")}


@pytest.mark.parametrize("content", [b"not frontmatter", b"---\nid: other\n---\n",
                                      b"---\nid: app-001\n---\n<<<<<<< ours\n",
                                      b"\xff"])
def test_invalid_entity_bytes_are_not_adopted(content):
    with pytest.raises((ValueError, UnicodeError)):
        parse.projected_file("task", "app-001", content)


@pytest.mark.parametrize("kind,heavy", [("epic", "components"), ("phase", "docs")])
def test_entity_file_only_owns_heavy_fields_and_body(kind, heavy):
    current = ({"id": "a", "name": "Index name", "unknown": 7,
                "description": "old", heavy: {"old": True}}, "old body")
    saved = deepcopy(current)
    rows = parse.projected_file(kind, "a", b"---\nid: a\ntitle: Display mirror\n"
                               b"description: new\n---\nnew body\n", lambda *_: current)
    assert rows[(kind, "a")] == ({"id": "a", "name": "Index name", "unknown": 7,
                                   "description": "new"}, "new body")
    assert current == saved, "parsing must not mutate the caller's snapshot"


def test_backlog_owns_slim_fields_and_preserves_entity_prose():
    rows = parse.projected_file("backlog", None, b"meta:\n  schema_version: 4\n"
                               b"  updated: yesterday\ncustom: retained\n"
                               b"epics:\n- id: a\n  name: New name\n"
                               b"phases:\n- id: p\n  name: New phase\n",
                               lambda kind, ident: ({"description": "authored"}, "body"))
    assert rows[("backlog", "__backlog__")] == (
        {"meta": {"schema_version": 4}, "custom": "retained"}, None)
    assert rows[("epic", "a")] == ({"id": "a", "name": "New name", "description": "authored"}, "body")
    assert rows[("phase", "p")][1] == "body"


@pytest.mark.parametrize("kind,heavy", [("epic", "description"), ("epic", "docs"),
                                       ("epic", "components"), ("phase", "description"), ("phase", "docs")])
def test_backlog_cannot_resurrect_absent_document_owned_heavy_field(kind, heavy):
    content = f"{kind}s:\n- id: a\n  name: Slim name\n  {heavy}: injected\n".encode()
    rows = parse.projected_file("backlog", None, content, lambda *_: ({"id": "a"}, None))
    assert heavy not in rows[(kind, "a")][0]


@pytest.mark.parametrize("content", [b"[]", b"meta: []", b"meta:\n  schema_version: true\n",
                                      b"meta:\n  projection_schema: 4.0\n"])
def test_invalid_backlog_shape_is_rejected(content):
    with pytest.raises(ValueError):
        parse.projected_file("backlog", None, content)


def test_flatten_rejects_duplicate_ids_and_keeps_legacy_inline_tasks():
    data = {"epics": [{"id": "a", "tasks": [{"id": "a-001", "_body": "prose\n"}]}]}
    assert parse.flatten_backlog(data)[("task", "a-001")] == ({"id": "a-001", "epic": "a"}, "prose")
    data["epics"][0]["tasks"].append({"id": "a-001"})
    with pytest.raises(ValueError, match="appears twice"):
        parse.flatten_backlog(data)


def test_project_preserves_unknown_fields_and_requires_mapping():
    assert parse.projected_file("project", None, b"custom: value\n") == {
        ("project", "__project__"): ({"custom": "value"}, None)}
    with pytest.raises(ValueError, match="mapping"):
        parse.projected_file("project", None, b"[bad]")


def test_legacy_parser_delegates_to_the_same_pure_boundary(monkeypatch):
    from taskmaster import store
    sentinel = {("epic", "a"): ({"id": "a"}, "body")}
    monkeypatch.setattr(parse, "projected_file", lambda *args, **kwargs: sentinel)
    # No Store constructor, database connection or filesystem access is needed.
    instance = object.__new__(store.Store)
    assert instance._parse_projected_file(None, "epic", "a", b"unused") is sentinel
