"""User intent: an agent that follows `backlog_context` cursors must get every
selected row exactly once and then stop. A cursor that re-delivers a finished
section, or hands back the cursor it was given, turns a bounded read into an
unbounded one — and an omission count that ignores where the page starts tells
the caller rows are missing that it already has.

Checked as properties over many budgets and multi-section includes on both
stores, not as one example: the defect this replaces passed every single-page
test and only showed on the second page of a two-section chain.
"""
from __future__ import annotations

import json

import pytest

from taskmaster import backlog_server as bs
from native_twins import make_twins

FOCUS = "test-epic-001"
INCLUDES = (["notes", "siblings"], ["siblings", "notes"], ["notes", "siblings", "handovers"])
BUDGETS = range(900, 3200, 45)


@pytest.fixture
def twins(tmp_path, monkeypatch):
    def seed():
        bs.backlog_add_task(title="Focus", epic="test-epic", phase="dev")
        for i in range(3):
            bs.backlog_note(action="create", text=f"pinned note {i} " + "x" * 200, pinned=True)
        for i in range(7):
            # Uneven rows, so page boundaries fall at different places per budget.
            bs.backlog_add_task(title=f"Sibling {i} " + "y" * (120 + 60 * (i % 3)),
                                epic="test-epic", phase="dev")
        for i in range(2):
            bs.backlog_handover_create(tldr=f"Handover {i}", task_ids=[FOCUS],
                                       next_action="")
    return make_twins(tmp_path, monkeypatch, seed)


def _ask(include, budget, cursor=""):
    return json.loads(bs.backlog_context(focus=FOCUS, scope="task", include=include,
                                         budget_bytes=budget, cursor=cursor))


def _chain(include, budget, totals):
    """Follow cursors from page 1, checking every page as it arrives."""
    delivered = {name: [] for name in include}
    pages, cursor = [], ""
    for _ in range(sum(totals.values()) + 2):
        page = _ask(include, budget, cursor)
        assert "error" not in page, page
        assert page.get("cursor", "") == "" or page.get("cursor", "") != cursor, \
            f"budget {budget}: a page handed back the cursor it was given"
        for name, rows in page["selected"].items():
            delivered[name] += [row["id"] for row in rows]
        for name in include:
            # Exact for this page's position: rows neither delivered earlier nor on
            # this page. Rows an earlier page delivered are not "omitted".
            assert page["budget"].get("omitted", {}).get(name, 0) == totals[name] - len(delivered[name]), \
                f"budget {budget}: {name} omitted count ignores the page's offset"
            assert page.get("provenance", {}).get(name, {}).get("truncated", False) == (len(delivered[name]) < totals[name]), \
                f"budget {budget}: {name} truncated disagrees with what was delivered"
        pages.append(page)
        cursor = page.get("cursor", "")
        if not cursor:
            return delivered, pages
    pytest.fail(f"budget {budget}: cursor chain did not terminate")


@pytest.mark.parametrize("side", ["legacy", "native"])
def test_following_cursors_delivers_every_row_once_and_terminates(twins, side):
    root = getattr(twins, side)
    complete_chains = 0
    with twins.at(root):
        for include in INCLUDES:
            whole = _ask(include, 200000)
            everything = {name: [row["id"] for row in whole["selected"].get(name, [])]
                          for name in include}
            totals = {name: len(ids) for name, ids in everything.items()}
            assert whole.get("cursor", "") == "" and totals["notes"] == 3 and totals["siblings"] == 7
            for budget in BUDGETS:
                delivered, pages = _chain(include, budget, totals)
                for name in include:
                    assert len(delivered[name]) == len(set(delivered[name])), \
                        f"budget {budget}: {name} re-delivered {delivered[name]}"
                    # A prefix of the section, in order: nothing skipped between pages.
                    assert delivered[name] == everything[name][:len(delivered[name])]
                last = pages[-1]
                if last["selected"]:
                    # The chain stopped because it was done, not because it stalled.
                    assert delivered == everything, f"budget {budget}: rows never delivered"
                    assert last["budget"].get("omitted_total", 0) == 0
                    complete_chains += 1
                else:
                    # A page that delivered nothing ends the chain and says, exactly,
                    # what the caller still does not have.
                    assert last["budget"].get("omitted_total", 0) == \
                        sum(totals.values()) - sum(map(len, delivered.values()))
    # The range must actually exercise multi-page chains that finish.
    assert complete_chains > len(INCLUDES) * 10
