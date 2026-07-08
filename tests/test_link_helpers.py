from taskmaster.taskmaster_v3 import (
    LINK_FIELD,
    entity_links,
    set_entity_links,
    add_link,
    remove_link,
    links_grouped_by_type,
)


def test_link_field_constant():
    assert LINK_FIELD == "links"


def test_entity_links_returns_empty_list_when_absent():
    assert entity_links({"id": "T-001"}) == []


def test_entity_links_returns_list_copy():
    entity = {"id": "T-001", "links": [{"type": "depends_on", "target": "T-002"}]}
    result = entity_links(entity)
    assert result == [{"type": "depends_on", "target": "T-002"}]
    # Mutating the copy must not affect the entity.
    result.append({"type": "blocks", "target": "T-003"})
    assert entity["links"] == [{"type": "depends_on", "target": "T-002"}]


def test_set_entity_links_replaces_array():
    entity: dict = {"id": "T-001"}
    set_entity_links(entity, [{"type": "depends_on", "target": "T-002"}])
    assert entity["links"] == [{"type": "depends_on", "target": "T-002"}]


def test_set_entity_links_drops_field_when_empty():
    entity = {"id": "T-001", "links": [{"type": "depends_on", "target": "T-002"}]}
    set_entity_links(entity, [])
    assert "links" not in entity


def test_add_link_appends():
    entity: dict = {"id": "T-001"}
    add_link(entity, "depends_on", "T-002")
    assert entity["links"] == [{"type": "depends_on", "target": "T-002"}]


def test_add_link_is_idempotent():
    entity: dict = {"id": "T-001"}
    add_link(entity, "depends_on", "T-002")
    add_link(entity, "depends_on", "T-002")
    assert entity["links"] == [{"type": "depends_on", "target": "T-002"}]


def test_add_link_preserves_other_types_to_same_target():
    entity: dict = {"id": "T-001"}
    add_link(entity, "depends_on", "T-002")
    add_link(entity, "relates_to", "T-002")
    assert len(entity["links"]) == 2


def test_remove_link_drops_one_entry():
    entity = {"id": "T-001", "links": [
        {"type": "depends_on", "target": "T-002"},
        {"type": "relates_to", "target": "T-002"},
    ]}
    removed = remove_link(entity, "depends_on", "T-002")
    assert removed is True
    assert entity["links"] == [{"type": "relates_to", "target": "T-002"}]


def test_remove_link_missing_returns_false():
    entity = {"id": "T-001", "links": [{"type": "relates_to", "target": "T-002"}]}
    removed = remove_link(entity, "depends_on", "T-002")
    assert removed is False
    assert entity["links"] == [{"type": "relates_to", "target": "T-002"}]


def test_remove_link_drops_links_field_when_empty():
    entity = {"id": "T-001", "links": [{"type": "depends_on", "target": "T-002"}]}
    remove_link(entity, "depends_on", "T-002")
    assert "links" not in entity


def test_links_grouped_by_type():
    entity = {"id": "T-001", "links": [
        {"type": "depends_on", "target": "T-002"},
        {"type": "depends_on", "target": "T-003"},
        {"type": "fixes",      "target": "ISS-007"},
        {"type": "references", "target": "HND-012"},
    ]}
    grouped = links_grouped_by_type(entity)
    assert grouped == {
        "depends_on": ["T-002", "T-003"],
        "fixes":      ["ISS-007"],
        "references": ["HND-012"],
    }


def test_links_grouped_by_type_empty():
    assert links_grouped_by_type({"id": "T-001"}) == {}
