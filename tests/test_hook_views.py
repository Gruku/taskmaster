# User intent: the hook read views (team-server-007 D3) must give exactly the answers the hooks gave before the
# move, on a legacy store and its native twin, and pick a branch's task safely when teammates' branches collide (D3a).
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

import hook_views_scenario as sc
from native_twins import make_twins
from taskmaster import backlog_server as bs
from taskmaster.native_routing import hook_reads
from taskmaster.taskmaster_v3 import rung_for_branch

HOOKS = Path(__file__).resolve().parents[1] / "hooks"


def _module(name):
    spec = importlib.util.spec_from_file_location(f"views_{name}", HOOKS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _db(root):
    return root / ".taskmaster" / "local" / "store.db"


def _read(root, fn, *args, **kwargs):
    con = hook_reads.connect_ro(_db(root), 2.0)
    try:
        return fn(con, *args, **kwargs)
    finally:
        con.close()


@pytest.fixture
def twins(tmp_path, monkeypatch):
    twins = make_twins(tmp_path, monkeypatch, sc.seed)
    sc.after_activation(twins)
    return twins


def test_views_match_the_answers_captured_before_the_move(twins):
    golden = sc.load_golden()
    gate = _module("merge_gate_decide")
    for label in sc.checkpoints(twins):
        for name, root in (("legacy", twins.legacy), ("native", twins.native)):
            want = golden[label][name]
            where = f"{label}/{name}"
            for rel in sc.PATHS:
                got = _read(root, hook_reads.paths_view, rel)
                assert got["path"] == rel
                assert {k: got[k] for k in ("listed", "closed", "prose", "related")} == want["paths"][rel], (where, rel)
            for branch in sc.BRANCHES:
                got = _read(root, hook_reads.merge_gate_view, branch)
                old = want["gate"][branch]
                assert got["policy_required"] == old["policy_required"], (where, branch)
                if old["policy_required"]:
                    task = got["task"]
                    if old["task"] is None:
                        assert task is None, (where, branch)
                    else:
                        assert {k: v for k, v in task.items() if k != "mine"} == old["task"], (where, branch)
                        assert task["mine"] is False
                assert gate.decide_from_store(_db(root), branch, root) == want["decide"][branch], (where, branch)
            for branch in sc.BRANCHES:
                for target in sc.TARGETS:
                    if name == "native":
                        got = _read(root, hook_reads.merge_target_view, branch, target)
                        assert [got["task_id"], got["rung"]] == want["target"][branch][target], (where, branch, target)
                        assert got["ambiguous"] is False
                    else:
                        with twins.at(root):
                            ladder = bs._resolved_merge_targets()
                        chosen = _read(root, lambda con: hook_reads.choose_task(
                            hook_reads.tasks_for_branch(con, None, branch)))
                        task_id = chosen["task"][0] if chosen["task"] else None
                        assert [task_id, rung_for_branch(target, ladder) or f"branch:{target}"] == \
                            want["target"][branch][target], (where, branch, target)


def test_merge_target_view_refuses_a_legacy_store(twins):
    with pytest.raises(ValueError):
        _read(twins.legacy, hook_reads.merge_target_view, "feature/late", "qa-line")


def test_the_store_revision_is_the_one_the_edit_hook_dedupes_on(twins):
    edit = _module("edit_resurface")
    for root in (twins.legacy, twins.native):
        assert _read(root, hook_reads.store_revision) == edit.max_change_seq(_db(root))


class P:
    def __init__(self, handle):
        self.handle = handle


def _t(ident, **fields):
    return (ident, fields)


def test_without_a_principal_the_first_task_by_id_is_taken():
    chosen = hook_reads.choose_task([_t("a-1", owner="bob"), _t("a-2", owner="alice")])
    assert chosen["task"][0] == "a-1" and not chosen["ambiguous"]
    assert chosen["candidates"] == ["a-1", "a-2"]


def test_no_candidates():
    assert hook_reads.choose_task([], P("alice")) == {"task": None, "mine": False, "ambiguous": False,
                                                      "candidates": []}


def test_one_candidate_is_taken_and_says_whose_it_is():
    assert hook_reads.choose_task([_t("a-1", owner="alice")], P("alice"))["mine"] is True
    other = hook_reads.choose_task([_t("a-1", owner="bob")], P("alice"))
    assert other["task"][0] == "a-1" and other["mine"] is False and not other["ambiguous"]


@pytest.mark.parametrize("fields", [{"owner": "alice"}, {"assignee": "alice"},
                                    {"locked_by": "alice@0123456789abcdef"}])
def test_the_callers_task_wins_among_several(fields):
    chosen = hook_reads.choose_task([_t("a-1", owner="bob"), _t("a-2", **fields)], P("alice"))
    assert chosen["task"][0] == "a-2" and chosen["mine"] is True and not chosen["ambiguous"]


def test_a_session_of_a_handle_with_the_same_prefix_is_not_the_callers():
    chosen = hook_reads.choose_task([_t("a-1", locked_by="alicex@0123456789abcdef"), _t("a-2", owner="bob")],
                                    P("alice"))
    assert chosen["ambiguous"] is True


def test_several_that_are_not_the_callers_are_ambiguous_never_the_first():
    chosen = hook_reads.choose_task([_t("a-1", owner="bob"), _t("a-2", owner="carol")], P("alice"))
    assert chosen == {"task": None, "mine": False, "ambiguous": True, "candidates": ["a-1", "a-2"]}


def test_two_of_the_callers_are_ambiguous_too():
    chosen = hook_reads.choose_task([_t("a-1", owner="alice"), _t("a-2", assignee="alice")], P("alice"))
    assert chosen["ambiguous"] is True


def test_candidates_are_capped():
    many = [_t(f"a-{i:02d}") for i in range(25)]
    assert len(hook_reads.choose_task(many, P("alice"))["candidates"]) == hook_reads.MAX_CANDIDATES


# ── The glob matcher (review I1) ────────────────────────────────


def _old_regex(pattern):
    """The regex the hooks used before team-server-007 review I1: the reference the new matcher must equal."""
    import re
    out, i = [], 0
    while i < len(pattern):
        ch = pattern[i]
        if ch == "*":
            if pattern.startswith("**", i):
                out.append(".*")
                i += 2
                continue
            out.append("[^/]*")
        elif ch == "?":
            out.append("[^/]")
        else:
            out.append(re.escape(ch))
        i += 1
    return re.compile("".join(out) + r"\Z")


GLOBS = ["*", "**", "***", "****", "?", "a*", "*a", "a/*", "a/**", "**/b", "a/**/b", "a*/b", "a?c", "*.py", "**/*.py",
         "src/**/test_*.py", "a**b", "a***b", "**a**", "*/*", "?/?", "a.b", "a+b", "[x]", "", "a/", "*a*a*b", "docs/*.md"]
PATHS = ["", "a", "b", "ab", "abc", "a/b", "a/c/b", "a/b/c", "x.py", "src/x.py", "src/pkg/test_one.py", "src/test_one.py",
         "a.b", "axb", "a+b", "[x]", "x", "a/", "/a", "a\nb", "a/\n/b", "aab", "a/a/a/b", "docs/x.md", "docs/a/x.md"]


@pytest.mark.parametrize("pattern", GLOBS)
def test_the_matcher_gives_the_old_regex_answers(pattern):
    reference = _old_regex(pattern)
    for rel in PATHS:
        assert hook_reads.glob_match(pattern, rel) == (reference.match(rel) is not None), (pattern, rel)


def test_a_pathological_glob_is_linear():
    """The reviewer's case: `*a*a*a*a*a*a*b` took 4.3 s on 70 characters with the regex; at 1024 it never ends."""
    import time
    started = time.perf_counter()
    for size in (70, 120, 1024):
        assert hook_reads.glob_match("*a*a*a*a*a*a*b", "a" * size) is False
        assert hook_reads.glob_match("**a**a**a**a**a**a**b", "a/" * (size // 2)) is False
    assert time.perf_counter() - started < 1.0


def test_a_deadline_stops_a_match_and_the_view():
    with pytest.raises(hook_reads.ViewTimeout):
        hook_reads.glob_match("*a*b", "a" * 600, deadline=lambda: True)

def test_an_oversize_glob_never_matches_and_costs_nothing():
    """Review (round 2): a stored anchor of 240,000 `*` held /read/v1/paths for 11.7 s past its 5 s deadline."""
    import time
    started = time.perf_counter()
    assert hook_reads.glob_match("*" * 240_000, "a" * 255) is False
    assert hook_reads.glob_match("*" * 240_000, "a" * 255, deadline=lambda: False) is False
    assert time.perf_counter() - started < 0.2
    assert hook_reads.glob_match("*" * hook_reads.MAX_GLOB_CHARS, "a/b") is True  # at the cap the matcher still runs (`**` crosses `/`)


def test_a_deadline_is_checked_inside_the_per_step_work():
    """A cap-sized pattern with a few path characters must still honour an already-expired deadline."""
    with pytest.raises(hook_reads.ViewTimeout):
        hook_reads.glob_match("*a" * 500, "a" * 5, deadline=lambda: True)
    with pytest.raises(hook_reads.ViewTimeout):
        hook_reads.glob_match("*" * 1000, "a", deadline=lambda: True)


# ── team-server-008 step 1: ref_problem, check_merge_gate_view, merge_stamp_plan, the clone keyword ──


@pytest.mark.parametrize("value", ["feature/x", "a", "origin/feature/x", "fix@v2", "feat+x", "a.b_c-d", "x" * 255])
def test_ref_problem_accepts_what_valid_ref_accepts(value):
    assert hook_reads.ref_problem(value) is None


@pytest.mark.parametrize("value", ["", "a..b", "-x", "/x", "x/", "x" * 256, "a b", "release#1", "a\nb", None, 3])
def test_ref_problem_refuses_what_valid_ref_refused(value):
    assert isinstance(hook_reads.ref_problem(value), str)


def test_read_api_valid_ref_is_the_same_rule():
    from taskmaster.serve import read_api
    for value in ("feature/x", "a..b", "-x", "/x", "x/", "x" * 256, "", None, "a b"):
        refused = hook_reads.ref_problem(value) is not None
        if refused:
            with pytest.raises(read_api.Refused):
                read_api.valid_ref("branch", value)
        else:
            assert read_api.valid_ref("branch", value) == value


def _good_view(**task):
    base = {"id": "t-1", "mine": True, "skip_merge_gate": False, "merge_gate_freshness": "strict",
            "review_gate": {"verdict": "pass", "commit_sha": "a" * 40}}
    return {"policy_required": True, "ambiguous": False, "candidates": ["t-1"], "task": {**base, **task}}


def test_check_merge_gate_view_accepts_every_view_the_stores_produce(twins):
    for label in sc.checkpoints(twins):
        for branch in sc.BRANCHES:
            data = _read(twins.native, hook_reads.merge_gate_view, branch, P("alice"))
            assert hook_reads.check_merge_gate_view(data) is None, (label, branch, data)
    assert hook_reads.check_merge_gate_view(_good_view(review_gate=None)) is None
    assert hook_reads.check_merge_gate_view({"policy_required": False, "ambiguous": False, "candidates": [],
                                             "task": None}) is None


@pytest.mark.parametrize("mutate,field", [
    (lambda d: d.pop("policy_required"), "policy_required"),
    (lambda d: d.update(policy_required="yes"), "policy_required"),
    (lambda d: d.update(ambiguous=1), "ambiguous"),
    (lambda d: d.update(candidates="t-1"), "candidates"),
    (lambda d: d.update(candidates=[1]), "candidates"),
    (lambda d: d.update(task="t-1"), "task"),
    (lambda d: d["task"].update(id=5), "task.id"),
    (lambda d: d["task"].pop("mine"), "task.mine"),
    (lambda d: d["task"].update(skip_merge_gate="no"), "task.skip_merge_gate"),
    (lambda d: d["task"].update(merge_gate_freshness="loose"), "task.merge_gate_freshness"),
    (lambda d: d["task"].pop("merge_gate_freshness"), "task.merge_gate_freshness"),
    (lambda d: d["task"].update(review_gate="pass"), "task.review_gate"),
    (lambda d: d["task"].update(review_gate={"verdict": "pass"}), "task.review_gate"),
    (lambda d: d["task"].update(review_gate={"verdict": 1, "commit_sha": "x"}), "task.review_gate"),
])
def test_check_merge_gate_view_names_the_bad_field(mutate, field):
    data = _good_view()
    mutate(data)
    problem = hook_reads.check_merge_gate_view(data)
    assert problem is not None and field in problem


def test_check_merge_gate_view_refuses_a_non_object():
    assert hook_reads.check_merge_gate_view([]) is not None
    assert hook_reads.check_merge_gate_view(None) is not None


def test_merge_stamp_plan_follows_the_ladder_and_reports_the_current_record(twins):
    plan = _read(twins.native, hook_reads.merge_stamp_plan, "feature/late", "qa-line")
    assert plan == {"ambiguous": False, "candidates": ["test-epic-003"], "task_id": "test-epic-003",
                    "rung": "qa", "current": None}
    twins.same("backlog_record_merge", task_id="test-epic-003", rung="qa", sha="b" * 40)
    again = _read(twins.native, hook_reads.merge_stamp_plan, "feature/late", "qa-line")
    assert again["current"]["merge_commit"] == "b" * 40 and again["current"]["merged_at"]
    plan = _read(twins.native, hook_reads.merge_stamp_plan, "feature/late", "topic")
    assert plan["rung"] == "branch:topic" and plan["current"] is None


def test_merge_stamp_plan_with_no_task_and_with_several(twins):
    assert _read(twins.native, hook_reads.merge_stamp_plan, "feature/none", "master")["task_id"] is None
    twins.same("backlog_update_task", task_id="test-epic-001", field="branch", value="feature/late")
    plan = _read(twins.native, hook_reads.merge_stamp_plan, "feature/late", "master", P("zed"))
    assert plan["ambiguous"] is True and plan["task_id"] is None
    assert plan["candidates"] == ["test-epic-001", "test-epic-003"]


def test_merge_stamp_plan_refuses_a_legacy_store(twins):
    with pytest.raises(ValueError):
        _read(twins.legacy, hook_reads.merge_stamp_plan, "feature/late", "master")


def test_the_views_accept_a_clone_and_answer_the_same(twins):
    clone = "c-" + "0123456789abcdef"
    for branch in sc.BRANCHES:
        assert _read(twins.native, hook_reads.merge_gate_view, branch, P("a"), clone=clone) == \
            _read(twins.native, hook_reads.merge_gate_view, branch, P("a"))
        assert _read(twins.native, hook_reads.merge_target_view, branch, "master", P("a"), clone=clone) == \
            _read(twins.native, hook_reads.merge_target_view, branch, "master", P("a"))
        assert _read(twins.native, hook_reads.merge_stamp_plan, branch, "master", P("a"), clone=clone) == \
            _read(twins.native, hook_reads.merge_stamp_plan, branch, "master", P("a"))


# ── team-server-011 step 4: clone-aware lookup ──────────────────────────────────────────────────

CLONE_A, CLONE_B, CLONE_C = "c-" + "a" * 16, "c-" + "b" * 16, "c-" + "c" * 16


def _map(root, rows):
    """Write `(handle, clone, task, branch)` rows straight into the store's clone_fields table."""
    from contextlib import closing
    from taskmaster.coordinator import protocol
    from taskmaster.native import clone_fields
    with closing(protocol.connect(root)) as connection:
        for handle, clone, task, branch in rows:
            clone_fields.put(connection, handle, clone, "task", task, "branch", branch, 1)
        connection.commit()


def test_two_people_mapping_one_name_each_get_their_own_task_and_a_third_gets_ambiguous(twins):
    _map(twins.native, [("ann", CLONE_A, "test-epic-001", "fix/login"), ("bob", CLONE_B, "test-epic-002", "fix/login")])
    ann = _read(twins.native, hook_reads.merge_gate_view, "fix/login", P("ann"), clone=CLONE_A)
    bob = _read(twins.native, hook_reads.merge_gate_view, "fix/login", P("bob"), clone=CLONE_B)
    carol = _read(twins.native, hook_reads.merge_gate_view, "fix/login", P("carol"), clone=CLONE_C)
    assert ann["task"]["id"] == "test-epic-001" and ann["task"]["mine"] is True and not ann["ambiguous"]
    assert bob["task"]["id"] == "test-epic-002" and bob["task"]["mine"] is True
    assert carol["ambiguous"] is True and carol["task"] is None
    assert carol["candidates"] == ["test-epic-001", "test-epic-002"]


def test_a_branch_only_anns_row_maps_is_still_found_and_gated_for_bob(twins):
    _map(twins.native, [("ann", CLONE_A, "test-epic-001", "ann/only")])
    bob = _read(twins.native, hook_reads.merge_gate_view, "ann/only", P("bob"), clone=CLONE_B)
    assert bob["task"]["id"] == "test-epic-001" and bob["task"]["mine"] is False and not bob["ambiguous"]
    assert _read(twins.native, hook_reads.merge_gate_view, "ann/only")["task"] is None      # solo shape: shared only


def test_the_default_row_stands_in_for_a_clone_with_no_row(twins):
    _map(twins.native, [("ann", "", "test-epic-001", "fix/login"), ("bob", CLONE_B, "test-epic-002", "fix/login")])
    ann = _read(twins.native, hook_reads.merge_gate_view, "fix/login", P("ann"), clone=CLONE_C)
    assert ann["task"]["id"] == "test-epic-001" and ann["task"]["mine"] is True
    no_clone = _read(twins.native, hook_reads.merge_gate_view, "fix/login", P("ann"))
    assert no_clone["task"]["id"] == "test-epic-001"


def test_shared_field_tasks_are_still_found_and_a_row_does_not_hide_them(twins):
    plain = _read(twins.native, hook_reads.merge_gate_view, "feature/late", P("zed"), clone=CLONE_A)
    assert plain["task"]["id"] == "test-epic-003"
    _map(twins.native, [("ann", CLONE_A, "test-epic-001", "feature/late")])
    both = _read(twins.native, hook_reads.merge_gate_view, "feature/late", P("zed"), clone=CLONE_C)
    assert both["ambiguous"] is True and both["candidates"] == ["test-epic-001", "test-epic-003"]
    mine = _read(twins.native, hook_reads.merge_gate_view, "feature/late", P("ann"), clone=CLONE_A)
    assert mine["task"]["id"] == "test-epic-001" and mine["task"]["mine"] is True


def test_target_and_stamp_views_use_the_same_lookup(twins):
    _map(twins.native, [("ann", CLONE_A, "test-epic-001", "fix/login"), ("bob", CLONE_B, "test-epic-002", "fix/login")])
    target = _read(twins.native, hook_reads.merge_target_view, "fix/login", "master", P("bob"), clone=CLONE_B)
    plan = _read(twins.native, hook_reads.merge_stamp_plan, "fix/login", "master", P("ann"), clone=CLONE_A)
    assert target["task_id"] == "test-epic-002" and plan["task_id"] == "test-epic-001"
    assert _read(twins.native, hook_reads.merge_stamp_plan, "fix/login", "master", P("carol"))["ambiguous"] is True


def test_an_archived_task_with_a_row_is_not_a_candidate(twins):
    _map(twins.native, [("ann", CLONE_A, "test-epic-001", "fix/login")])
    from contextlib import closing
    from taskmaster.coordinator import protocol
    with closing(protocol.connect(twins.native)) as connection:
        connection.execute("UPDATE entity_core SET archived=1 WHERE kind='task' AND public_id='test-epic-001'")
        connection.commit()
    assert _read(twins.native, hook_reads.merge_gate_view, "fix/login", P("ann"), clone=CLONE_A)["task"] is None
