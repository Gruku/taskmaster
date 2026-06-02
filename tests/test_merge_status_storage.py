import taskmaster_v3 as tv


def test_merge_flags_are_slim():
    for f in ("skip_merge_gate", "merge_gate_freshness", "merge_gate_state"):
        assert f in tv.SLIM_FIELDS["task"]


def test_merge_status_is_heavy():
    assert "merge_status" in tv.HEAVY_FIELDS


def test_merge_status_splits_to_per_task_file():
    task = {
        "id": "e1-001", "title": "X", "status": "done", "lane": "express",
        "skip_merge_gate": False, "merge_gate_freshness": "strict",
        "merge_gate_state": "stage",
        "merge_status": {"develop": {"merged_at": "t", "merge_commit": "abc"},
                         "stage": {"merged_at": "t", "merge_commit": "def"}},
    }
    slim, heavy, _body = tv._split_task_for_v3(task)
    assert slim["skip_merge_gate"] is False
    assert slim["merge_gate_freshness"] == "strict"
    assert slim["merge_gate_state"] == "stage"
    assert "merge_status" not in slim
    assert heavy["merge_status"]["stage"]["merge_commit"] == "def"
