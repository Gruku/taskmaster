"""User intent: a *selection* budget, never a content budget (D1). Mandatory
context always comes back complete, the reported byte count is the bytes actually
returned, and what was left out is an exact `COUNT(*)` — never an estimate, and
never a trimmed blocker list that reads like a clear one.
"""
import json

import pytest

from taskmaster.native.budget import Selection, budget


def render(mandatory=None, selections=(), limit_bytes=8000, envelope=None):
    return budget(envelope=envelope if envelope is not None else {"store_id": "s", "sequence": 1},
                  mandatory=mandatory if mandatory is not None else {"clear": True, "blockers": []},
                  selections=list(selections), limit_bytes=limit_bytes)


def payload(answer):
    return json.loads(answer.text)


# ── The reported number is the bytes that crossed the wire ──────────────────


def test_used_bytes_equals_the_bytes_of_the_answer_itself():
    answer = render(selections=[Selection("handovers", [{"id": f"H-{n}"} for n in range(5)], 5)])
    assert len(answer.text.encode("utf-8")) == answer.budget["used_bytes"]
    assert payload(answer)["budget"]["used_bytes"] == answer.budget["used_bytes"]


def test_the_count_is_utf8_bytes_not_characters():
    plain = render(mandatory={"clear": False, "blockers": [{"kind": "gate", "id": "aaaa"}]})
    wide = render(mandatory={"clear": False, "blockers": [{"kind": "gate", "id": "ααββ"}]})
    assert wide.budget["used_bytes"] == plain.budget["used_bytes"] + 4
    assert len(wide.text.encode("utf-8")) == wide.budget["used_bytes"]
    assert len(wide.text) < wide.budget["used_bytes"]


def test_a_multibyte_item_is_dropped_on_its_byte_cost_not_its_length():
    items = [{"note": "é" * 40}, {"note": "é" * 40}]
    base = render(selections=[Selection("notes", [], 0)]).budget["used_bytes"]
    answer = render(selections=[Selection("notes", items, 2)], limit_bytes=base + 100)
    assert answer.budget["omitted"] == {"notes": 1}
    assert len(answer.text.encode("utf-8")) == answer.budget["used_bytes"] <= base + 100


# ── The boundary ────────────────────────────────────────────────────────────


def test_an_item_that_lands_exactly_on_the_limit_is_kept():
    items = [{"id": "H-1"}, {"id": "H-2"}]
    full = render(selections=[Selection("handovers", items, 2)])
    exact = render(selections=[Selection("handovers", items, 2)],
                   limit_bytes=full.budget["used_bytes"])
    assert exact.budget["used_bytes"] == full.budget["used_bytes"]
    assert payload(exact)["selected"]["handovers"] == items
    assert exact.budget["omitted_total"] == 0 and exact.budget["over_budget"] is False


def test_one_byte_under_the_limit_drops_the_last_item():
    items = [{"id": "H-1"}, {"id": "H-2"}]
    full = render(selections=[Selection("handovers", items, 2)])
    tight = render(selections=[Selection("handovers", items, 2)],
                   limit_bytes=full.budget["used_bytes"] - 1)
    assert payload(tight)["selected"]["handovers"] == [{"id": "H-1"}]
    assert tight.budget["omitted"] == {"handovers": 1} and tight.budget["omitted_total"] == 1
    assert len(tight.text.encode("utf-8")) == tight.budget["used_bytes"] <= full.budget["used_bytes"] - 1


# ── Mandatory context is never trimmed ──────────────────────────────────────


def test_mandatory_over_budget_returns_complete_with_nothing_selected():
    blockers = [{"kind": "gate", "id": f"gate-{n}", "state": "pending"} for n in range(20)]
    mandatory = {"clear": False, "blockers": blockers}
    answer = render(mandatory=mandatory,
                    selections=[Selection("handovers", [{"id": "H-1"}], 4),
                                Selection("siblings", [{"id": "T-1"}], 11)],
                    limit_bytes=200)
    body = payload(answer)
    assert body["mandatory"] == mandatory
    assert body["selected"] == {}
    assert answer.budget["over_budget"] is True
    assert answer.budget["used_bytes"] > 200 == answer.budget["limit_bytes"]
    assert answer.budget["omitted"] == {"handovers": 4, "siblings": 11}
    assert answer.budget["omitted_total"] == 15
    assert len(answer.text.encode("utf-8")) == answer.budget["used_bytes"]


def test_mandatory_bytes_are_reported_so_a_caller_can_raise_the_budget():
    mandatory = {"clear": False, "blockers": [{"kind": "bug", "id": "B-118"}]}
    answer = render(mandatory=mandatory, limit_bytes=40)
    assert answer.budget["mandatory_bytes"] == len(
        json.dumps(mandatory, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    assert answer.budget["over_budget"] is True


def test_over_budget_means_mandatory_did_not_fit_not_that_selection_was_dropped():
    """`over_budget` is the caller's signal to raise the budget because the
    mandatory block alone exceeds it. A budget that holds the mandatory block but
    no selected item is a normal, honest answer with omissions."""
    base = render(selections=[Selection("siblings", [], 0)]).budget["used_bytes"]
    nothing_fits = render(selections=[Selection("siblings", [{"id": "T" * 500}], 11)],
                          limit_bytes=base + 50)
    assert nothing_fits.budget["over_budget"] is False
    assert nothing_fits.budget["used_bytes"] <= base + 50
    assert nothing_fits.budget["omitted"] == {"siblings": 11}
    assert payload(nothing_fits)["selected"] == {}
    assert render(selections=[Selection("siblings", [{"id": "T-1"}], 11)],
                  limit_bytes=0).budget["over_budget"] is True


# ── Omission counts are exact, from the paired COUNT(*) ─────────────────────


def test_omitted_counts_come_from_the_paired_count_not_from_the_page():
    """The query that produced `items` was itself limited, so `len(items)` is not
    the population. The count has to be the store's, or `omitted` understates."""
    answer = render(selections=[Selection("bugs", [{"id": "B-1"}, {"id": "B-2"}], 97)])
    assert answer.budget["omitted"] == {"bugs": 95}
    assert answer.budget["omitted_total"] == 95


def test_a_section_with_nothing_omitted_still_reports_zero():
    answer = render(selections=[Selection("bugs", [{"id": "B-1"}], 1),
                                Selection("siblings", [], 0)])
    assert answer.budget["omitted"] == {"bugs": 0, "siblings": 0}
    assert answer.budget["omitted_total"] == 0


def test_a_count_below_what_was_handed_in_is_refused_rather_than_reported_negative():
    with pytest.raises(ValueError, match="total 1 is below"):
        render(selections=[Selection("bugs", [{"id": "B-1"}, {"id": "B-2"}], 1)])


# ── Selection order and prefix semantics ────────────────────────────────────


def test_selections_fill_in_order_and_stop_at_the_first_item_that_does_not_fit():
    first = [{"id": f"A-{n}"} for n in range(3)]
    second = [{"id": f"B-{n}"} for n in range(3)]
    full = render(selections=[Selection("a", first, 3), Selection("b", second, 3)])
    tight = render(selections=[Selection("a", first, 3), Selection("b", second, 3)],
                   limit_bytes=full.budget["used_bytes"] - 20)
    body = payload(tight)
    assert body["selected"]["a"] == first[:len(body["selected"].get("a", []))]
    assert body["selected"].get("b", []) == second[:len(body["selected"].get("b", []))]
    assert tight.budget["omitted_total"] == (
        3 - len(body["selected"].get("a", []))) + (3 - len(body["selected"].get("b", [])))


@pytest.mark.parametrize("limit", [0, 60, 120, 300, 8000])
def test_over_budget_holds_exactly_when_the_answer_exceeds_its_limit(limit):
    answer = render(mandatory={"clear": False, "blockers": [
                        {"kind": "gate", "id": f"g-{n}", "state": "pending"} for n in range(6)]},
                    selections=[Selection("bugs", [{"id": f"B-{n}"} for n in range(4)], 9)],
                    limit_bytes=limit)
    assert answer.budget["over_budget"] is (answer.budget["used_bytes"] > limit)
    assert len(answer.text.encode("utf-8")) == answer.budget["used_bytes"]


def test_the_budget_block_says_it_governs_the_selection_only():
    answer = render()
    assert payload(answer)["budget"]["applies_to"] == "selected"


def test_the_envelope_and_the_mandatory_block_are_passed_through_verbatim():
    envelope = {"store_id": "abc", "sequence": 41207, "scope": "task", "focus": "N09-3"}
    mandatory = {"clear": False, "blockers": [{"kind": "claim", "id": "N09-3"}],
                 "gate_state": "review-gate:pending", "human_action": ""}
    body = payload(render(mandatory=mandatory, envelope=envelope))
    assert {key: body[key] for key in envelope} == envelope
    assert body["mandatory"] == mandatory
