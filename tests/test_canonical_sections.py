from taskmaster.taskmaster_v3 import CANONICAL_SECTIONS


def test_canonical_sections_per_entity_type():
    assert "spec" in CANONICAL_SECTIONS["task"]
    assert "plan" in CANONICAL_SECTIONS["task"]
    assert "notes" in CANONICAL_SECTIONS["task"]
    assert "review_instructions" in CANONICAL_SECTIONS["task"]

    assert set(CANONICAL_SECTIONS["handover"]) == {"decisions", "notes", "blockers", "where_id_start"}
    assert set(CANONICAL_SECTIONS["issue"]) == {"repro", "investigation", "notes"}


def test_task_sections_include_docs_keys():
    expected_doc_keys = {"plan", "spec", "design", "analysis", "roadmap"}
    assert expected_doc_keys.issubset(set(CANONICAL_SECTIONS["task"]))


# ---------------------------------------------------------------------------
# B-042: sections=[] silently falls through (falsy) instead of raising an error
# ---------------------------------------------------------------------------


def test_get_task_sections_empty_list_returns_error(tm_epic_phase):
    """B-042: backlog_get_task(sections=[]) must return an Error string."""
    from taskmaster.backlog_server import backlog_add_task, backlog_get_task
    backlog_add_task(epic="test-epic", title="Sections test", tldr="A tldr.", phase="dev", options={"task_id": "T-B042"})
    result = backlog_get_task("T-B042", sections=[])
    assert isinstance(result, str)
    assert result.startswith("Error: sections=[]")


def test_get_task_sections_none_returns_slim(tm_epic_phase):
    """B-042: backlog_get_task(sections=None) must return the normal slim view."""
    from taskmaster.backlog_server import backlog_add_task, backlog_get_task
    backlog_add_task(epic="test-epic", title="Slim test", tldr="Slim tldr.", phase="dev", options={"task_id": "T-B042b"})
    result = backlog_get_task("T-B042b", sections=None)
    assert "Slim tldr." in result
    assert not result.startswith("Error: sections=[]")


def test_get_task_sections_valid_returns_content(tm_epic_phase):
    """B-042: backlog_get_task with a real section name still works."""
    from taskmaster.backlog_server import backlog_add_task, backlog_get_task
    backlog_add_task(epic="test-epic", title="Section test", tldr="T.", notes="My notes here.", phase="dev", options={"task_id": "T-B042c"})
    result = backlog_get_task("T-B042c", sections=["notes"])
    assert "My notes here" in result


def test_issue_get_sections_empty_list_returns_error(tmp_taskmaster):
    """B-042: backlog_issue_get(sections=[]) must return an Error string."""
    from taskmaster.backlog_server import backlog_issue_create, backlog_issue_get
    backlog_issue_create(
        title="Test issue",
        severity="P2",
        tldr="Issue tldr.",
        impact="Some impact.",
    )
    result = backlog_issue_get("ISS-001", sections=[])
    assert isinstance(result, str)
    assert result.startswith("Error: sections=[]")


def test_issue_get_sections_none_returns_slim(tmp_taskmaster):
    """B-042: backlog_issue_get(sections=None) must return the normal slim view."""
    from taskmaster.backlog_server import backlog_issue_create, backlog_issue_get
    backlog_issue_create(
        title="Test issue slim",
        severity="P3",
        tldr="Slim issue tldr.",
        impact="Some impact.",
    )
    result = backlog_issue_get("ISS-001", sections=None)
    assert "Slim issue tldr." in result
    assert not result.startswith("Error: sections=[]")
