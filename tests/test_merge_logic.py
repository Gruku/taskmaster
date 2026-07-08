from taskmaster import taskmaster_v3 as tv

LADDER = [
    {"label": "develop", "branches": ["develop", "dev"]},
    {"label": "stage", "branches": ["stage", "staging"]},
    {"label": "master", "branches": ["master", "main"]},
]


def test_merge_rungs_ordered():
    assert tv.merge_rungs(LADDER) == ("develop", "stage", "master")
    assert tv.merge_rungs([]) == ()


def test_rung_for_branch_matches_alias():
    assert tv.rung_for_branch("dev", LADDER) == "develop"
    assert tv.rung_for_branch("main", LADDER) == "master"
    assert tv.rung_for_branch("feature/foo", LADDER) is None


def test_merge_gate_state_highest_contiguous_rung():
    task = {"merge_status": {"develop": {"merge_commit": "a"}, "stage": {"merge_commit": "b"}}}
    assert tv.compute_merge_gate_state(task, LADDER) == "stage"


def test_merge_gate_state_empty_when_no_merges():
    assert tv.compute_merge_gate_state({"merge_status": {}}, LADDER) == ""
    assert tv.compute_merge_gate_state({}, LADDER) == ""


def test_merge_gate_state_terminal_rung():
    task = {"merge_status": {"develop": {}, "stage": {}, "master": {}}}
    assert tv.compute_merge_gate_state(task, LADDER) == "master"


def test_merge_gate_state_non_contiguous_reports_highest_reached():
    task = {"merge_status": {"stage": {"merge_commit": "b"}}}
    assert tv.compute_merge_gate_state(task, LADDER) == "stage"
