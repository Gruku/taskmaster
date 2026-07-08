from taskmaster.taskmaster_v3 import expand_link_ids


def test_expand_known_ids():
    tldr_index = {
        "T-001": "Refactor auth",
        "ISS-007": "Auth crashes on Friday",
    }
    ids = ["T-001", "ISS-007"]
    out = expand_link_ids(ids, tldr_index)
    assert out == [
        {"id": "T-001", "tldr": "Refactor auth"},
        {"id": "ISS-007", "tldr": "Auth crashes on Friday"},
    ]


def test_expand_unknown_id_returns_none_tldr():
    out = expand_link_ids(["T-999"], {})
    assert out == [{"id": "T-999", "tldr": None}]


def test_expand_handles_grouped_dict():
    grouped = {"depends_on": ["T-002"], "fixes": ["ISS-007"]}
    tldr_index = {"T-002": "Auth helper", "ISS-007": "Auth crashes"}
    out = expand_link_ids(grouped, tldr_index)
    assert out["depends_on"] == [{"id": "T-002", "tldr": "Auth helper"}]
    assert out["fixes"] == [{"id": "ISS-007", "tldr": "Auth crashes"}]


def test_build_tldr_index_indexes_all_entity_types(tmp_path):
    from taskmaster.taskmaster_v3 import build_tldr_index, write_task_file

    tm_dir = tmp_path / ".taskmaster"
    for subdir in ("tasks", "issues", "handovers", "ideas"):
        (tm_dir / subdir).mkdir(parents=True)

    for subdir, eid, tldr in [
        ("issues",    "ISS-001", "An issue tldr"),
        ("handovers", "HND-001", "A handover tldr"),
        ("ideas",     "IDEA-001","An idea tldr"),
    ]:
        write_task_file(tm_dir / subdir / f"{eid}.md", {"id": eid, "tldr": tldr}, "")

    data = {"epics": [{"tasks": [{"id": "T-001", "tldr": "A task tldr"}]}]}
    idx = build_tldr_index(data, project_root=tmp_path)
    assert idx["T-001"] == "A task tldr"
    assert idx["ISS-001"] == "An issue tldr"
    assert idx["HND-001"] == "A handover tldr"
    assert idx["IDEA-001"] == "An idea tldr"
