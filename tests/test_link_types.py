from taskmaster_v3 import (
    LINK_TYPES,
    REVERSE_TYPE,
    ENTITY_KIND_BY_PREFIX,
    LINK_TYPE_DOMAIN,
    entity_kind_of,
    is_valid_link,
)


def test_canonical_link_types_present():
    expected = {
        "depends_on", "blocks", "fixes", "fixed_in_task",
        "relates_to", "informed_by", "informs",
        "supersedes", "superseded_by",
        "duplicate_of", "duplicates",
        "references", "referenced_by",
    }
    assert set(LINK_TYPES) == expected


def test_reverse_type_is_symmetric_pair():
    # Each type's reverse-of-reverse must round-trip back to itself.
    for t in LINK_TYPES:
        assert REVERSE_TYPE[REVERSE_TYPE[t]] == t


def test_reverse_type_specific_pairs():
    assert REVERSE_TYPE["depends_on"] == "blocks"
    assert REVERSE_TYPE["blocks"] == "depends_on"
    assert REVERSE_TYPE["fixes"] == "fixed_in_task"
    assert REVERSE_TYPE["fixed_in_task"] == "fixes"
    assert REVERSE_TYPE["informed_by"] == "informs"
    assert REVERSE_TYPE["informs"] == "informed_by"
    assert REVERSE_TYPE["supersedes"] == "superseded_by"
    assert REVERSE_TYPE["superseded_by"] == "supersedes"
    assert REVERSE_TYPE["duplicate_of"] == "duplicates"
    assert REVERSE_TYPE["duplicates"] == "duplicate_of"
    assert REVERSE_TYPE["references"] == "referenced_by"
    assert REVERSE_TYPE["referenced_by"] == "references"
    # relates_to is its own inverse
    assert REVERSE_TYPE["relates_to"] == "relates_to"


def test_entity_kind_by_prefix():
    assert ENTITY_KIND_BY_PREFIX["T"] == "task"
    assert ENTITY_KIND_BY_PREFIX["ISS"] == "issue"
    assert ENTITY_KIND_BY_PREFIX["L"] == "lesson"
    assert ENTITY_KIND_BY_PREFIX["HND"] == "handover"
    assert ENTITY_KIND_BY_PREFIX["IDEA"] == "idea"


def test_entity_kind_of_dispatches_by_prefix():
    assert entity_kind_of("T-001") == "task"
    assert entity_kind_of("ISS-007") == "issue"
    assert entity_kind_of("L-003") == "lesson"
    assert entity_kind_of("HND-012") == "handover"
    assert entity_kind_of("IDEA-005") == "idea"


def test_entity_kind_of_unknown_returns_none():
    assert entity_kind_of("FOO-001") is None
    assert entity_kind_of("") is None
    assert entity_kind_of(None) is None


def test_link_type_domain_enforces_source_target_kinds():
    # depends_on / blocks are task→task
    assert is_valid_link("depends_on", "task", "task") is True
    assert is_valid_link("depends_on", "task", "issue") is False
    # fixes is task→issue
    assert is_valid_link("fixes", "task", "issue") is True
    assert is_valid_link("fixes", "task", "task") is False
    # fixed_in_task is issue→task
    assert is_valid_link("fixed_in_task", "issue", "task") is True
    # informed_by is task→lesson
    assert is_valid_link("informed_by", "task", "lesson") is True
    # informs is lesson→task
    assert is_valid_link("informs", "lesson", "task") is True
    # supersedes / superseded_by are handover→handover
    assert is_valid_link("supersedes", "handover", "handover") is True
    assert is_valid_link("supersedes", "task", "handover") is False
    # duplicate_of / duplicates are issue→issue
    assert is_valid_link("duplicate_of", "issue", "issue") is True
    # relates_to and references are any→any
    for src in ("task", "issue", "lesson", "handover", "idea"):
        for dst in ("task", "issue", "lesson", "handover", "idea"):
            assert is_valid_link("relates_to", src, dst) is True
            assert is_valid_link("references", src, dst) is True
            assert is_valid_link("referenced_by", src, dst) is True
