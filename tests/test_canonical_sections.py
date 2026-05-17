from plugins.taskmaster.taskmaster_v3 import CANONICAL_SECTIONS


def test_canonical_sections_per_entity_type():
    assert "spec" in CANONICAL_SECTIONS["task"]
    assert "plan" in CANONICAL_SECTIONS["task"]
    assert "notes" in CANONICAL_SECTIONS["task"]
    assert "review_instructions" in CANONICAL_SECTIONS["task"]

    assert set(CANONICAL_SECTIONS["handover"]) == {"decisions", "notes", "blockers", "where_id_start"}
    assert set(CANONICAL_SECTIONS["issue"]) == {"repro", "investigation", "notes"}
    assert set(CANONICAL_SECTIONS["lesson"]) == {"why", "what_to_do", "examples"}


def test_task_sections_include_docs_keys():
    expected_doc_keys = {"plan", "spec", "design", "analysis", "roadmap"}
    assert expected_doc_keys.issubset(set(CANONICAL_SECTIONS["task"]))
