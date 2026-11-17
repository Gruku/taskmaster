# User intent: pin every row of spec §6.5 (the team resolver) with a unit test, so what lands when
# two teammates edit the same backlog entity is decided by tested rules, not by accident.
import copy
import subprocess

import pytest

from taskmaster.native import team_merge
from taskmaster.native.migrate import encode
from taskmaster.native.team_merge import (
    EMPTY_BASE, BodyConflict, ClaimMoved, IdCollision, LocalIdentity, SyncNote, resolve)

ME = LocalIdentity(handle="gruku", clone_id="c-ours")
ABSENT = object()


def task(**fields):
    return dict({"id": "t-1", "title": "T", "status": "todo"}, **fields)


def note(field, side, lost, kept, code="field_lost", detail=None):
    return SyncNote("task", "t-1", code, field, side,
                    None if lost is ABSENT else encode(lost),
                    None if kept is ABSENT else encode(kept), detail)


def run(base, ours, theirs, **kw):
    return resolve("task", "t-1", base, ours, theirs, **kw)


@pytest.fixture
def no_git(monkeypatch):
    def fail(*args, **kwargs):
        pytest.fail("run_bounded must not be called")
    monkeypatch.setattr(team_merge, "run_bounded", fail)


# ---- Step 0: skeleton and guards

def test_identical_sides_are_unchanged():
    B = (task(priority="low"), "body")
    r = run(B, B, B)
    assert r.state == B
    assert r.action == "unchanged"
    assert r.notes == ()
    assert r.conflict is None
    assert r.claim_moved is None


@pytest.mark.parametrize("field", ["locked_by", "claim_expires", "claim_expires_for", "worktree", "branch"])
def test_local_only_fields_are_refused(field):
    B = (task(), None)
    with pytest.raises(ValueError, match=field):
        run(B, (task(**{field: "x"}), None), B)


def test_backlog_derived_fields_are_refused():
    B = ({"name": "p"}, None)
    with pytest.raises(ValueError, match="bugs"):
        resolve("backlog", "backlog", B, ({"name": "p", "bugs": []}, None), B)


@pytest.mark.parametrize("kind", ["tracker", "widget"])
def test_tracker_and_unknown_kinds_are_refused(kind):
    B = ({"id": "x"}, None)
    with pytest.raises(ValueError):
        resolve(kind, "x", B, B, B)


def test_declared_id_must_match():
    B = (task(), None)
    with pytest.raises(ValueError):
        run(B, B, (task(id="t-2"), None))


# ---- Step 1: disjoint fields

def test_disjoint_fields_both_land():
    base = (task(priority="low", tldr="x"), None)
    ours = (task(priority="low", title="A"), None)
    theirs = (task(priority="high", tldr="x", estimate="M"), None)
    r = run(base, ours, theirs)
    assert r.state[0] == {"id": "t-1", "title": "A", "status": "todo", "priority": "high", "estimate": "M"}
    assert r.notes == ()
    assert r.action == "update"


def test_field_edit_and_body_edit_on_different_sides_both_land(no_git):
    base = (task(priority="low"), "body")
    ours = (task(priority="high"), "body")
    theirs = (task(priority="low"), "body two")
    r = run(base, ours, theirs)
    assert r.state == (task(priority="high"), "body two")
    assert r.notes == ()


# ---- Step 2: same scalar field

def test_same_scalar_theirs_wins_with_note():
    base, ours, theirs = (task(priority="low"), None), (task(priority="high"), None), (task(priority="critical"), None)
    r = run(base, ours, theirs)
    assert r.state[0]["priority"] == "critical"
    assert r.notes == (note("priority", "ours", "high", "critical"),)


def test_same_new_value_on_both_sides_has_no_note():
    base = (task(priority="low"), None)
    both = (task(priority="high"), None)
    r = run(base, both, both)
    assert r.state == both
    assert r.notes == ()
    assert r.action == "unchanged"


def test_false_and_zero_are_different_values():
    base = (task(x=None), None)
    r = run(base, (task(x=False), None), (task(x=0), None))
    assert r.state[0]["x"] == 0 and r.state[0]["x"] is not False
    assert r.notes == (note("x", "ours", False, 0),)
    assert r.notes[0].lost_json == "false" and r.notes[0].kept_json == "0"


def test_removed_versus_changed_is_an_overlap():
    base = (task(tldr="x"), None)
    r = run(base, (task(), None), (task(tldr="y"), None))
    assert r.state[0]["tldr"] == "y"
    assert r.notes == (note("tldr", "ours", ABSENT, "y"),)
    assert r.notes[0].lost_json is None


def test_whole_record_dicts_are_atomic():
    base = (task(spec_review={"verdict": "warn", "at": "1"}), None)
    ours = (task(spec_review={"verdict": "pass", "at": "2", "extra": 1}), None)
    theirs = (task(spec_review={"verdict": "fail", "at": "3"}), None)
    r = run(base, ours, theirs)
    assert r.state[0]["spec_review"] == {"verdict": "fail", "at": "3"}
    assert r.notes == (note("spec_review", "ours", ours[0]["spec_review"], theirs[0]["spec_review"]),)


# ---- Step 3: status, gate records and merge records

PASS = {"verdict": "pass", "at": "2026-10-31T08:00:00Z"}


def test_status_theirs_wins_and_gates_union():
    base = (task(lane="full", status="in-progress", gates={"spec-review": PASS},
                 gate_state="plan-review:pending"), None)
    ours = (task(lane="full", status="in-review", gates={"spec-review": PASS, "plan-review": PASS},
                 gate_state="review-gate:pending"), None)
    tests_rec = {"at": "2026-10-31T09:00:00Z", "status": "done"}
    theirs = (task(lane="full", status="blocked", gates={"spec-review": PASS, "tests": tests_rec},
                   gate_state="plan-review:pending"), None)
    r = run(base, ours, theirs)
    fields = r.state[0]
    assert fields["status"] == "blocked"
    assert fields["gates"] == {"spec-review": PASS, "plan-review": PASS, "tests": tests_rec}
    assert fields["gate_state"] == "review-gate:pending"
    assert r.notes == (note("status", "ours", "in-review", "blocked"),)


def test_gate_state_is_recomputed_not_merged():
    skip = {"skipped": True, "reason": "r", "by": "jd", "at": "2026-10-31T09:00:00Z"}
    base = (task(lane="full", gates={}, gate_state="spec-review:pending"), None)
    ours = (task(lane="full", gates={"spec-review": PASS}, gate_state="plan-review:pending"), None)
    theirs = (task(lane="full", gates={"plan-review": skip}, gate_state="spec-review:pending"), None)
    r = run(base, ours, theirs)
    assert r.state[0]["gates"] == {"spec-review": PASS, "plan-review": skip}
    assert r.state[0]["gate_state"] == "review-gate:pending"
    assert r.notes == ()


def test_same_gate_on_both_sides_keeps_theirs_record_and_notes_ours():
    OURS_REC = {"verdict": "pass", "at": "1"}
    THEIRS_REC = {"verdict": "fail", "at": "2"}
    base = (task(lane="full", gates={}), None)
    ours = (task(lane="full", gates={"plan-review": OURS_REC}), None)
    theirs = (task(lane="full", gates={"plan-review": THEIRS_REC}), None)
    r = run(base, ours, theirs)
    assert r.state[0]["gates"] == {"plan-review": THEIRS_REC}
    assert r.state[0]["gate_state"] == "blocked@plan-review"
    assert r.notes == (note("gates.plan-review", "ours", OURS_REC, THEIRS_REC),)


def test_gate_cleared_on_one_side_and_changed_on_other_keeps_the_record():
    NEW = {"verdict": "warn", "at": "3"}
    base = (task(lane="full", gates={"spec-review": PASS}), None)
    ours = (task(lane="full", gates={}), None)
    theirs = (task(lane="full", gates={"spec-review": NEW}), None)
    r = run(base, ours, theirs)
    assert r.state[0]["gates"] == {"spec-review": NEW}
    (n,) = r.notes
    assert (n.field, n.side, n.lost_json, n.kept_json) == ("gates.spec-review", "ours", None, encode(NEW))


TARGETS = [{"label": "develop"}, {"label": "master"}]


def _merge_pair():
    dev = {"merged_at": "1", "merge_commit": "aaa"}
    mas = {"merged_at": "2", "merge_commit": "bbb"}
    base = (task(merge_status={}, merge_gate_state=""), None)
    ours = (task(merge_status={"develop": dev}, merge_gate_state="develop"), None)
    theirs = (task(merge_status={"master": mas}, merge_gate_state="master"), None)
    return base, ours, theirs, dev, mas


def test_merge_records_union_and_merge_gate_state_recomputed():
    base, ours, theirs, dev, mas = _merge_pair()
    r = run(base, ours, theirs, merge_targets=TARGETS)
    assert r.state[0]["merge_status"] == {"develop": dev, "master": mas}
    assert r.state[0]["merge_gate_state"] == "master"
    assert r.notes == ()


def test_merge_gate_state_kept_without_targets():
    base, ours, theirs, dev, mas = _merge_pair()
    r = run(base, ours, theirs, merge_targets=None)
    assert r.state[0]["merge_status"] == {"develop": dev, "master": mas}
    assert r.state[0]["merge_gate_state"] == "master"  # the baseline (theirs-wins) value
    assert r.notes == ()


def test_docs_map_merges_key_wise():
    base = (task(docs={}), None)
    r = run(base, (task(docs={"plan": "a.md"}), None), (task(docs={"review": "r.md"}), None))
    assert r.state[0]["docs"] == {"plan": "a.md", "review": "r.md"}
    assert r.notes == ()
    r = run(base, (task(docs={"plan": "a.md"}), None), (task(docs={"plan": "b.md"}), None))
    assert r.state[0]["docs"] == {"plan": "b.md"}
    assert r.notes == (note("docs.plan", "ours", "a.md", "b.md"),)


# ---- Step 4: list fields

def _lists(field, base, ours, theirs):
    return run((task(**{field: base}), None), (task(**{field: ours}), None), (task(**{field: theirs}), None))


def test_depends_on_merges_element_wise():
    r = _lists("depends_on", ["a", "b"], ["a", "b", "c"], ["b", "d"])
    assert r.state[0]["depends_on"] == ["b", "d", "c"]
    assert r.notes == ()


def test_removal_on_ours_applies():
    r = _lists("depends_on", ["a", "b"], ["b"], ["a", "b", "e"])
    assert r.state[0]["depends_on"] == ["b", "e"]
    assert r.notes == ()


def test_links_identity_is_type_and_target():
    r1 = {"type": "references", "target": "ISS-1"}
    r2 = {"type": "references", "target": "ISS-2"}
    bl = {"type": "blocks", "target": "t-9"}
    r = _lists("links", [r1], [r1, bl], [r1, r2])
    assert r.state[0]["links"] == [r1, r2, bl]
    assert r.notes == ()


@pytest.mark.parametrize("field", ["anchors", "components", "tags", "task_ids"])
def test_identity_lists_merge_element_wise(field):
    r = _lists(field, ["x"], ["x", "o"], ["x", "t"])
    assert r.state[0][field] == ["x", "t", "o"]
    assert r.notes == ()


def test_list_without_identity_theirs_wins_with_note():
    r = _lists("checklist", ["x"], ["x", "y"], ["x", "z"])
    assert r.state[0]["checklist"] == ["x", "z"]
    assert r.notes == (note("checklist", "ours", ["x", "y"], ["x", "z"]),)


def test_identity_field_with_scalar_value_uses_scalar_rule():
    r = _lists("bundle", "base", "a", "b")
    assert r.state[0]["bundle"] == "b"
    assert r.notes == (note("bundle", "ours", "a", "b"),)


def test_one_sided_list_change_keeps_its_order():
    r = _lists("depends_on", ["a", "b"], ["b", "a"], ["a", "b"])
    assert r.state[0]["depends_on"] == ["b", "a"]


# ---- Step 5: body merge through git merge-file

def _bodies(base, ours, theirs, **kw):
    return run((task(), base), (task(), ours), (task(), theirs), **kw)


@pytest.mark.slow
def test_clean_body_merge_lands_without_note():
    r = _bodies("one\ntwo\nthree", "ONE\ntwo\nthree", "one\ntwo\nTHREE")
    assert r.state[1] == "ONE\ntwo\nTHREE"
    assert r.notes == ()
    assert r.conflict is None


@pytest.mark.slow
def test_conflicted_body_takes_theirs_and_preserves_ours():
    r = _bodies("a\nb\nc", "a\nX\nc", "a\nY\nc")
    assert r.state[1] == "a\nY\nc"
    assert r.conflict == BodyConflict("task", "t-1", "conflict", "a\nb\nc", "a\nX\nc", "a\nY\nc")
    assert r.notes == (note("body", "ours", ABSENT, ABSENT, code="body_conflict", detail="conflict"),)


@pytest.mark.slow
def test_body_merge_runs_through_run_bounded(monkeypatch):
    calls = []
    real = team_merge.run_bounded

    def spy(*args, **kwargs):
        calls.append((args, kwargs))
        return real(*args, **kwargs)

    monkeypatch.setattr(team_merge, "run_bounded", spy)
    _bodies("one\ntwo\nthree", "ONE\ntwo\nthree", "one\ntwo\nTHREE", timeout=7)
    assert len(calls) == 1
    args, kwargs = calls[0]
    assert args[0][1] == "merge-file"
    assert kwargs["timeout"] == 7


def test_git_missing_falls_back_to_conflict(monkeypatch, no_git):
    monkeypatch.setattr(team_merge.shutil, "which", lambda name: None)
    r = _bodies("a\nb\nc", "a\nX\nc", "a\nY\nc")
    assert r.state[1] == "a\nY\nc"
    assert r.conflict.reason == "git_unavailable"
    assert r.notes[0].detail == "git_unavailable"


def test_merge_timeout_falls_back_to_conflict(monkeypatch):
    def boom(args, **kwargs):
        raise subprocess.TimeoutExpired(args, 1)
    monkeypatch.setattr(team_merge, "run_bounded", boom)
    r = _bodies("a\nb\nc", "a\nX\nc", "a\nY\nc")
    assert r.conflict.reason == "timeout"
    assert r.state[1] == "a\nY\nc"


def test_git_error_exit_falls_back_to_conflict(monkeypatch):
    monkeypatch.setattr(team_merge, "run_bounded",
                        lambda args, **kw: subprocess.CompletedProcess(args, 255, b"", b"err"))
    r = _bodies("a\nb\nc", "a\nX\nc", "a\nY\nc")
    assert r.conflict.reason == "git_error"
    assert r.state[1] == "a\nY\nc"


@pytest.mark.slow
def test_clean_merge_keeps_unicode_and_framing():
    r = _bodies("é1\nmid\nend", "é1 ours\nmid\nend", "é1\nmid\nend théirs")
    assert r.state[1] == "é1 ours\nmid\nend théirs"
    assert not r.state[1].endswith("\n")


# ---- Step 6: archive vs edit

STAMP = "2026-10-31T10:00"


@pytest.mark.parametrize("archiver", ["ours", "theirs"])
def test_archive_vs_edit_keeps_both(archiver):
    base = (task(priority="low"), "body")
    archived = (task(priority="low", status="archived", archived=STAMP, archive_reason="wont-fix"), "body")
    edited = (task(priority="high", title="T2"), "body edited")
    ours, theirs = (archived, edited) if archiver == "ours" else (edited, archived)
    r = run(base, ours, theirs)
    assert r.state == (task(priority="high", title="T2", status="archived", archived=STAMP,
                            archive_reason="wont-fix"), "body edited")
    assert r.notes == (note("archived", archiver, ABSENT, STAMP, code="archived_with_edits"),)


def test_archive_beats_a_status_edit_with_note():
    base = (task(), None)
    ours = (task(status="archived", archived=STAMP), None)
    theirs = (task(status="in-progress"), None)
    r = run(base, ours, theirs)
    assert r.state[0]["status"] == "archived"
    assert r.notes == (note("archived", "ours", ABSENT, STAMP, code="archived_with_edits"),
                       note("status", "theirs", "in-progress", "archived"))


def test_archive_vs_archive_has_no_note():
    base = (task(), None)
    ours = (task(status="archived", archived="2026-10-31T10:00", archive_reason="r"), None)
    theirs = (task(status="archived", archived="2026-10-31T11:00", archive_reason="r"), None)
    r = run(base, ours, theirs)
    assert r.state == theirs
    assert r.notes == ()


def test_bug_archive_flag_counts_as_archive():
    base = ({"id": "B-1", "title": "b", "priority": "low"}, None)
    ours = ({"id": "B-1", "title": "b", "priority": "low", "archived": True}, None)
    theirs = ({"id": "B-1", "title": "b", "priority": "high"}, None)
    r = resolve("bug", "B-1", base, ours, theirs)
    assert r.state[0] == {"id": "B-1", "title": "b", "priority": "high", "archived": True}
    assert r.notes == (SyncNote("bug", "B-1", "archived_with_edits", "archived", "ours", None, "true"),)


# ---- Step 7: tombstone vs edit

def test_theirs_tombstone_vs_our_edit():
    B = (task(), "b")
    ours = (task(priority="high"), "b")
    r = run(B, ours, None)
    assert r.state == ours
    assert r.action == "unchanged"
    assert r.notes == (SyncNote("task", "t-1", "tombstone_lifted", None, "theirs", None, None),)


def test_our_tombstone_vs_their_edit():
    B = (task(), "b")
    theirs = (task(priority="high"), "b")
    r = run(B, None, theirs)
    assert r.state == theirs
    assert r.action == "insert"
    assert r.notes == (SyncNote("task", "t-1", "tombstone_lifted", None, "ours", None, None),)


def test_tombstone_vs_untouched_side_applies():
    B = (task(), "b")
    r = run(B, B, None)
    assert (r.state, r.action, r.notes) == (None, "delete", ())
    r = run(B, None, B)
    assert (r.state, r.action, r.notes) == (None, "unchanged", ())


def test_tombstone_on_both_sides():
    r = run((task(), None), None, None)
    assert (r.state, r.action, r.notes) == (None, "unchanged", ())


# ---- Step 8: updated_by / updated_at, last_referenced

def _stamped(by, at, **extra):
    return (task(updated_by=by, updated_at=at, **extra), None)


def test_newest_stamp_pair_wins_without_note():
    base = _stamped("amy", "2026-10-30T10:00:00Z")
    ours = _stamped("gruku", "2026-10-31T10:00:00Z")
    theirs = _stamped("jd", "2026-10-31T09:00:00Z")
    r = run(base, ours, theirs)
    assert (r.state[0]["updated_by"], r.state[0]["updated_at"]) == ("gruku", "2026-10-31T10:00:00Z")
    assert r.notes == ()
    r = run(base, theirs, ours)
    assert (r.state[0]["updated_by"], r.state[0]["updated_at"]) == ("gruku", "2026-10-31T10:00:00Z")


def test_stamp_pair_is_never_mixed():
    base = _stamped("amy", "2026-10-30T10:00:00Z")
    ours = _stamped("amy", "2026-10-31T10:00:00Z")
    theirs = _stamped("jd", "2026-10-31T09:00:00Z")
    r = run(base, ours, theirs)
    assert (r.state[0]["updated_by"], r.state[0]["updated_at"]) == ("amy", "2026-10-31T10:00:00Z")
    assert r.notes == ()


def test_one_sided_stamp_change_wins_even_if_older():
    base = _stamped("amy", "2026-10-30T10:00:00Z")
    theirs = _stamped("jd", "2026-10-29T10:00:00Z")
    r = run(base, base, theirs)
    assert (r.state[0]["updated_by"], r.state[0]["updated_at"]) == ("jd", "2026-10-29T10:00:00Z")


def test_last_referenced_newest_wins_without_note():
    base = (task(last_referenced="2026-10-30T10:00:00Z"), None)
    ours = (task(last_referenced="2026-10-31T10:00:00Z"), None)
    theirs = (task(last_referenced="2026-10-31T09:00:00Z"), None)
    assert run(base, ours, theirs).state[0]["last_referenced"] == "2026-10-31T10:00:00Z"
    r = run(base, theirs, ours)
    assert r.state[0]["last_referenced"] == "2026-10-31T10:00:00Z"
    assert r.notes == ()


def test_unparseable_stamp_prefers_theirs():
    base = _stamped("amy", "2026-10-30T10:00:00Z")
    ours = _stamped("gruku", "not a time")
    theirs = _stamped("jd", "2026-10-31T09:00:00Z")
    r = run(base, ours, theirs)
    assert (r.state[0]["updated_by"], r.state[0]["updated_at"]) == ("jd", "2026-10-31T09:00:00Z")
    assert r.notes == ()


# ---- Step 9: assignment

def _claim(handle, clone, **extra):
    return task(assignee=handle, assignee_source="claim", claimed_on=clone, **extra)


def test_their_claim_wins_and_reports_claim_moved():
    base = (task(), None)
    ours = (_claim("gruku", "c-ours", status="in-progress"), None)
    theirs = (_claim("jd", "c-jd", status="in-progress"), None)
    r = run(base, ours, theirs, me=ME)
    assert r.state == theirs
    assert r.claim_moved == ClaimMoved("task", "t-1", "gruku", "jd", "c-ours", "c-jd")
    og = {"assignee": "gruku", "assignee_source": "claim", "claimed_on": "c-ours"}
    tg = {"assignee": "jd", "assignee_source": "claim", "claimed_on": "c-jd"}
    assert r.notes == (note("assignment", "ours", og, tg, code="assignment_lost"),)


def test_assignment_group_is_atomic():
    base = (_claim("amy", "c-amy"), None)
    ours = (task(assignee="amy", assignee_source="manual", claimed_on="c-amy"), None)
    theirs = (task(assignee="amy", assignee_source="claim", claimed_on="c-jd"), None)
    r = run(base, ours, theirs)
    assert (r.state[0]["assignee_source"], r.state[0]["claimed_on"]) == ("claim", "c-jd")
    (n,) = r.notes
    assert n.code == "assignment_lost" and n.field == "assignment" and n.side == "ours"


def test_same_person_other_clone_reports_claim_moved():
    base = (task(), None)
    ours = (_claim("gruku", "c-ours"), None)
    theirs = (_claim("gruku", "c-laptop"), None)
    r = run(base, ours, theirs, me=ME)
    assert r.claim_moved == ClaimMoved("task", "t-1", "gruku", "gruku", "c-ours", "c-laptop")


def test_teammate_force_pick_over_idle_claim():
    mine = (_claim("gruku", "c-ours"), None)
    theirs = (_claim("jd", "c-jd"), None)
    r = run(mine, mine, theirs, me=ME)
    assert r.state == theirs
    assert r.notes == ()
    assert r.claim_moved == ClaimMoved("task", "t-1", "gruku", "jd", "c-ours", "c-jd")


def test_no_claim_moved_without_me_or_when_not_mine():
    base = (task(), None)
    ours = (_claim("gruku", "c-ours"), None)
    theirs = (_claim("jd", "c-jd"), None)
    assert run(base, ours, theirs).claim_moved is None
    held_by_jd = (_claim("jd", "c-jd"), None)
    other = (_claim("amy", "c-amy"), None)
    assert run(base, held_by_jd, other, me=ME).claim_moved is None


def test_bug_assignment_uses_the_same_rule():
    base = ({"id": "B-1", "title": "b"}, None)
    ours = ({"id": "B-1", "title": "b", "assignee": "gruku", "claimed_on": "c-ours"}, None)
    theirs = ({"id": "B-1", "title": "b", "assignee": "jd", "claimed_on": "c-jd"}, None)
    r = resolve("bug", "B-1", base, ours, theirs, me=ME)
    assert r.state == theirs
    assert r.claim_moved == ClaimMoved("bug", "B-1", "gruku", "jd", "c-ours", "c-jd")
    assert [n.code for n in r.notes] == ["assignment_lost"]


# ---- Step 10: new entity on one side

def test_new_on_theirs_is_inserted():
    theirs = (task(priority="high"), "b")
    r = run(None, None, theirs)
    assert (r.state, r.action, r.notes) == (theirs, "insert", ())


def test_new_on_ours_is_kept():
    ours = (task(priority="high"), "b")
    r = run(None, ours, None)
    assert (r.state, r.action, r.notes) == (ours, "unchanged", ())


def test_all_absent_is_an_error():
    with pytest.raises(ValueError):
        run(None, None, None)


# ---- Step 11: plan entries list-merge

def _plan(entries):
    return ({"id": "p-1", "owner": "gruku", "entries": entries}, None)


def _ids(entries):
    return [e["id"] for e in entries]


def _e(ident, added="2026-10-01"):
    return {"id": ident, "added": added}


def test_plan_two_clones_list_merge():
    base = _plan([_e("a"), _e("b"), _e("c")])
    ours = _plan([_e("a"), _e("c"), _e("x")])
    theirs = _plan([_e("c"), _e("a"), _e("b"), _e("y")])
    r = resolve("plan", "p-1", base, ours, theirs)
    assert _ids(r.state[0]["entries"]) == ["c", "a", "y", "x"]
    assert r.notes == ()


def test_plan_shared_entry_keeps_theirs_added():
    base = _plan([_e("a")])
    ours = _plan([_e("a", "2026-10-02"), _e("x")])
    theirs = _plan([_e("a", "2026-10-03"), _e("y")])
    r = resolve("plan", "p-1", base, ours, theirs)
    assert r.state[0]["entries"] == [_e("a", "2026-10-03"), _e("y"), _e("x")]
    assert r.notes == ()


def test_plan_without_base_unions():
    ours = _plan([_e("a"), _e("x")])
    theirs = _plan([_e("a"), _e("y")])
    r = resolve("plan", "p-1", EMPTY_BASE, ours, theirs)
    assert _ids(r.state[0]["entries"]) == ["a", "y", "x"]
    assert r.notes == ()


# ---- Step 12: collision

def test_same_new_id_on_both_sides_raises_id_collision():
    both = (task(), None)
    with pytest.raises(IdCollision, match="team-ids") as caught:
        run(None, both, both)
    assert (caught.value.kind, caught.value.id) == ("task", "t-1")


# ---- Step 13: the named-kind path

def test_named_kind_merge_with_empty_base():
    ours = ({"name": "auth", "order": 1, "area": "core"}, None)
    theirs = ({"name": "auth", "order": 2}, None)
    r = resolve("epic", "auth", EMPTY_BASE, ours, theirs)
    assert r.state[0] == {"name": "auth", "order": 2, "area": "core"}
    assert r.notes == (SyncNote("epic", "auth", "field_lost", "order", "ours", "1", "2"),)
    assert r.action == "update"


# ---- Step 14: determinism

def test_notes_are_sorted_and_stable():
    base = (task(priority="low", estimate="S", tldr="x"), None)
    ours = (task(priority="high", estimate="M", tldr="y"), None)
    theirs = (task(priority="critical", estimate="L", tldr="z"), None)
    first = run(base, ours, theirs)
    assert [n.field for n in first.notes] == ["estimate", "priority", "tldr"]
    assert run(base, ours, theirs) == first
    assert run(copy.deepcopy(base), copy.deepcopy(ours), copy.deepcopy(theirs)) == first


# ---- Review-gate fixes

def test_archive_vs_archive_reason_follows_the_scalar_rule():
    base = ({"id": "B-1", "title": "b"}, None)
    ours = ({"id": "B-1", "title": "b", "archived": True, "archive_reason": "dup of B-9"}, None)
    theirs = ({"id": "B-1", "title": "b", "archived": True, "archive_reason": "fixed"}, None)
    r = resolve("bug", "B-1", base, ours, theirs)
    assert r.state[0]["archive_reason"] == "fixed"
    assert r.notes == (SyncNote("bug", "B-1", "field_lost", "archive_reason", "ours",
                                '"dup of B-9"', '"fixed"'),)


def test_their_delete_of_my_claimed_task_reports_claim_moved():
    B = (_claim("gruku", "c-ours"), None)
    r = run(B, B, None, me=ME)
    assert r.state is None and r.action == "delete"
    assert r.claim_moved == ClaimMoved("task", "t-1", "gruku", None, "c-ours", None)
    assert run(B, B, None).claim_moved is None


def test_map_dropped_by_ours_and_untouched_by_theirs_stays_absent():
    base = (task(docs={"plan": "a.md"}, gates={"spec-review": PASS}), None)
    ours = (task(), None)
    r = run(base, ours, base)
    assert "docs" not in r.state[0] and "gates" not in r.state[0]
    base = (task(docs={}), None)
    assert "docs" not in run(base, (task(), None), base).state[0]


def test_parseable_stamp_beats_unparseable_stamp():
    base = _stamped("amy", "2026-10-30T10:00:00Z")
    good = _stamped("gruku", "2026-10-31T10:00:00Z")
    bad = _stamped("jd", "not a time")
    r = run(base, good, bad)
    assert (r.state[0]["updated_by"], r.state[0]["updated_at"]) == ("gruku", "2026-10-31T10:00:00Z")
    r = run(base, _stamped("a", "junk"), _stamped("b", "worse"))
    assert r.state[0]["updated_by"] == "b"


@pytest.mark.slow
def test_crlf_bodies_are_normalised_before_merging():
    r = _bodies("one\r\ntwo\r\nthree", "ONE\r\ntwo\r\nthree", "one\r\ntwo\r\nTHREE")
    assert r.state[1] == "ONE\ntwo\nTHREE"
    assert r.conflict is None
    r = _bodies("a\r\nb", "a\nb", "a\r\nb c")
    assert r.state[1] == "a\nb c"


def test_unencodable_body_takes_the_conflict_path():
    r = _bodies("a\nb\nc", "a\n\ud800\nc", "a\nY\nc")
    assert r.conflict.reason == "git_error"
    assert r.state[1] == "a\nY\nc"


@pytest.mark.parametrize("gates", [{"spec-review": "pass"}, ["x"]])
def test_non_dict_gate_record_is_refused(gates):
    B = (task(lane="full"), None)
    with pytest.raises(ValueError, match="gates"):
        run(B, B, (task(lane="full", gates=gates), None))


def test_element_wise_merge_has_no_duplicates():
    r = _lists("depends_on", ["a"], ["a", "x", "x"], ["a", "y", "y", "x"])
    assert r.state[0]["depends_on"] == ["a", "y", "x"]
