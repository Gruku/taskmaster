"""User intent: `backlog_handover_list` must be dependable for agent callers
(tm-audit-028) — a JSON envelope with fixed keys, the thread/git/link fields, the
thread/until/latest_per_thread filters, and an explicit signal whenever handovers
exist that the call did not return (limit cut, or the archive beyond the index).
"""
import json
import sys
from pathlib import Path

import yaml

PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT))

from taskmaster import backlog_server  # noqa: E402
from taskmaster.taskmaster_v3 import HANDOVER_INDEX_CAP  # noqa: E402

from tests.entity_helpers import (apply_supersession, update_handover_status, write_handover)

HANDOVER_KEYS = {"id", "date", "created", "thread", "session_kind", "status", "tldr", "next_action",
                 "task_ids", "tip_commit", "branch", "links", "superseded_by"}
ENVELOPE_KEYS = {"handovers", "returned", "total", "truncated", "archived_omitted"}


def _setup(tmp_path, monkeypatch) -> Path:
    bp = tmp_path / ".taskmaster" / "backlog.yaml"
    bp.parent.mkdir(parents=True, exist_ok=True)
    bp.write_text(yaml.safe_dump({"meta": {"updated": "2026-01-01"}, "epics": []}))
    (bp.parent / "handovers").mkdir()
    monkeypatch.setattr(backlog_server, "ROOT", tmp_path)
    monkeypatch.setattr(backlog_server, "_backlog_path", lambda: bp)
    return bp


def _json(**kwargs) -> dict:
    return json.loads(backlog_server.backlog_handover_list(format="json", **kwargs))


def _ids(envelope) -> list[str]:
    return [h["id"] for h in envelope["handovers"]]


# ── JSON envelope ────────────────────────────────────────────────────────────


def test_json_envelope_and_handover_objects_have_fixed_keys(tmp_path, monkeypatch):
    bp = _setup(tmp_path, monkeypatch)
    write_handover(bp, tldr="bare", when="2026-04-01")
    full, _ = write_handover(bp, tldr="full", next_action="go", task_ids=["T-1"], thread="Line A",
                             branch="feature/x", tip_commit="abc1234", when="2026-04-02")

    out = _json()
    assert set(out) == ENVELOPE_KEYS
    assert (out["returned"], out["total"], out["truncated"], out["archived_omitted"]) == (2, 2, False, 0)
    for handover in out["handovers"]:
        assert set(handover) == HANDOVER_KEYS

    newest, oldest = out["handovers"]
    assert newest["id"] == full
    assert (newest["thread"], newest["branch"], newest["tip_commit"]) == ("line-a", "feature/x", "abc1234")
    assert (newest["next_action"], newest["task_ids"], newest["status"]) == ("go", ["T-1"], "open")
    assert newest["date"] == "2026-04-02" and newest["created"]
    # Absent fields are empty, never missing.
    assert (oldest["thread"], oldest["branch"], oldest["tip_commit"], oldest["superseded_by"]) == ("", "", "", "")
    assert oldest["links"] == [] and oldest["task_ids"] == []


def test_json_links_show_supersession_in_both_directions(tmp_path, monkeypatch):
    bp = _setup(tmp_path, monkeypatch)
    old, _ = write_handover(bp, tldr="old", when="2026-04-01")
    new, _ = write_handover(bp, tldr="new", when="2026-04-02", supersedes=old)

    by_id = {h["id"]: h for h in _json()["handovers"]}
    assert {"type": "supersedes", "target": old} in by_id[new]["links"]
    assert {"type": "superseded_by", "target": new} in by_id[old]["links"]
    assert by_id[old]["superseded_by"] == new
    assert by_id[old]["status"] == "superseded"


def test_json_empty_result_is_still_an_envelope(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert _json() == {"handovers": [], "returned": 0, "total": 0, "truncated": False, "archived_omitted": 0}


def test_json_errors_are_json(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    assert "YYYY-MM-DD" in _json(until="tomorrow")["error"]
    assert "YYYY-MM-DD" in _json(since="yesterday")["error"]
    assert "status" in _json(status="bogus")["error"]


def test_unknown_format_is_refused(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = backlog_server.backlog_handover_list(format="xml")
    assert out.startswith("Error:") and "format" in out


# ── verbose text carries the same fields ─────────────────────────────────────


def test_verbose_text_shows_thread_git_context_and_links(tmp_path, monkeypatch):
    bp = _setup(tmp_path, monkeypatch)
    old, _ = write_handover(bp, tldr="old", when="2026-04-01")
    write_handover(bp, tldr="new", thread="line-a", branch="feature/x", tip_commit="abc1234",
                   when="2026-04-02", supersedes=old)

    verbose = backlog_server.backlog_handover_list(verbose=True)
    assert "  thread: line-a" in verbose
    assert "  branch: feature/x" in verbose
    assert "  tip_commit: abc1234" in verbose
    assert f"supersedes {old}" in verbose
    assert "superseded_by 2026-04-02-new" in verbose

    slim = backlog_server.backlog_handover_list()
    for field in ("thread:", "branch:", "tip_commit:", "links:"):
        assert field not in slim


# ── filters ──────────────────────────────────────────────────────────────────


def test_thread_filter_returns_only_that_thread(tmp_path, monkeypatch):
    bp = _setup(tmp_path, monkeypatch)
    a, _ = write_handover(bp, tldr="in a", thread="line-a", when="2026-04-01")
    write_handover(bp, tldr="in b", thread="line-b", when="2026-04-02")
    write_handover(bp, tldr="threadless", when="2026-04-03")

    assert _ids(_json(thread="line-a")) == [a]
    text = backlog_server.backlog_handover_list(thread="line-a")
    assert "in a" in text and "in b" not in text and "threadless" not in text
    assert "No handovers match" in backlog_server.backlog_handover_list(thread="no-such-thread")


def test_until_is_inclusive_and_pairs_with_since_for_one_day(tmp_path, monkeypatch):
    bp = _setup(tmp_path, monkeypatch)
    first, _ = write_handover(bp, tldr="first", when="2026-04-01")
    second, _ = write_handover(bp, tldr="second", when="2026-04-02")
    third, _ = write_handover(bp, tldr="third", when="2026-04-03")

    assert _ids(_json(until="2026-04-02")) == [second, first]
    assert _ids(_json(since="2026-04-02", until="2026-04-02")) == [second]
    assert third not in backlog_server.backlog_handover_list(until="2026-04-02")


def test_until_is_validated_like_since(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = backlog_server.backlog_handover_list(until="04/02/2026")
    assert out.startswith("Error:") and "`until`" in out and "YYYY-MM-DD" in out


def test_latest_per_thread_returns_newest_open_handover_of_each_thread(tmp_path, monkeypatch):
    bp = _setup(tmp_path, monkeypatch)
    # Newest written first, so both stay open in the thread.
    a_new, _ = write_handover(bp, tldr="a newest", thread="line-a", when="2026-04-05")
    a_old, _ = write_handover(bp, tldr="a older", thread="line-a", when="2026-04-04")
    # The thread's newest handover is closed: its newest *open* one is returned.
    b_closed, _ = write_handover(bp, tldr="b closed", thread="line-b", when="2026-04-03")
    b_open, _ = write_handover(bp, tldr="b open", thread="line-b", when="2026-04-02")
    update_handover_status(bp, handover_id=b_closed, status="closed")
    # A thread with nothing open contributes nothing.
    c_closed, _ = write_handover(bp, tldr="c closed", thread="line-c", when="2026-04-01")
    update_handover_status(bp, handover_id=c_closed, status="closed")
    # Threadless handovers are each their own group.
    loose_1, _ = write_handover(bp, tldr="loose one", when="2026-03-02")
    loose_2, _ = write_handover(bp, tldr="loose two", when="2026-03-01")

    assert _ids(_json(latest_per_thread=True)) == [a_new, b_open, loose_1, loose_2]
    text = backlog_server.backlog_handover_list(latest_per_thread=True)
    assert a_old not in text and b_closed not in text and c_closed not in text


def test_latest_per_thread_refuses_a_non_open_status_filter(tmp_path, monkeypatch):
    _setup(tmp_path, monkeypatch)
    out = backlog_server.backlog_handover_list(latest_per_thread=True, status="closed")
    assert out.startswith("Error:") and "latest_per_thread" in out


# ── saying when results are missing ──────────────────────────────────────────


def test_limit_cut_sets_truncated_and_keeps_the_true_total(tmp_path, monkeypatch):
    bp = _setup(tmp_path, monkeypatch)
    for day in range(1, 6):
        write_handover(bp, tldr=f"entry {day}", when=f"2026-05-0{day}")

    out = _json(limit=2)
    assert (out["returned"], out["total"], out["truncated"], out["archived_omitted"]) == (2, 5, True, 0)
    whole = _json(limit=0)
    assert (whole["returned"], whole["total"], whole["truncated"]) == (5, 5, False)


def _overflow(bp, extra=2) -> list[str]:
    """Write `extra` handovers more than the index holds; returns ids oldest first."""
    ids = []
    for n in range(HANDOVER_INDEX_CAP + extra):
        hid, _ = write_handover(bp, tldr=f"entry {n:02d}", task_ids=["T-OLD"] if n == 0 else [],
                                when=f"2026-{1 + n // 28:02d}-{1 + n % 28:02d}")
        ids.append(hid)
    return ids


def test_handovers_beyond_the_index_are_reported_not_silently_dropped(tmp_path, monkeypatch):
    bp = _setup(tmp_path, monkeypatch)
    ids = _overflow(bp)

    text = backlog_server.backlog_handover_list(limit=0)
    assert ids[0] not in text and ids[-1] in text
    assert "2 older handovers" in text and "include_archived=True" in text

    out = _json(limit=0)
    assert (out["returned"], out["total"]) == (HANDOVER_INDEX_CAP, HANDOVER_INDEX_CAP)
    assert out["archived_omitted"] == 2 and out["truncated"] is True

    # A filter that matches nothing in the index still says the archive went unsearched.
    missed = backlog_server.backlog_handover_list(task_id="T-OLD")
    assert "No handovers match" in missed and "2 older handovers" in missed
    assert _json(task_id="T-OLD")["truncated"] is True


def test_include_archived_lists_and_filters_beyond_the_index(tmp_path, monkeypatch):
    bp = _setup(tmp_path, monkeypatch)
    ids = _overflow(bp)

    out = _json(limit=0, include_archived=True)
    assert _ids(out) == list(reversed(ids))
    assert (out["returned"], out["total"], out["truncated"], out["archived_omitted"]) == (
        HANDOVER_INDEX_CAP + 2, HANDOVER_INDEX_CAP + 2, False, 0)

    assert _ids(_json(task_id="T-OLD", include_archived=True)) == [ids[0]]
    text = backlog_server.backlog_handover_list(limit=0, include_archived=True)
    assert ids[0] in text and "older handovers" not in text


def test_no_archive_footer_when_everything_is_in_the_index(tmp_path, monkeypatch):
    bp = _setup(tmp_path, monkeypatch)
    write_handover(bp, tldr="only one", when="2026-04-01")
    assert "older handovers" not in backlog_server.backlog_handover_list()


def test_explicit_supersession_helper_is_visible_in_the_listing(tmp_path, monkeypatch):
    """A chain repaired after the fact (`backlog_handover_supersede`) reads the same."""
    bp = _setup(tmp_path, monkeypatch)
    old, _ = write_handover(bp, tldr="old", when="2026-04-01")
    new, _ = write_handover(bp, tldr="new", when="2026-04-02")
    apply_supersession(bp, old_id=old, new_id=new)

    by_id = {h["id"]: h for h in _json()["handovers"]}
    assert by_id[old]["superseded_by"] == new
    assert by_id[old]["links"] == [{"type": "superseded_by", "target": new}]
