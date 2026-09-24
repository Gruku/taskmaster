"""User intent: N16-B — `budget.budget` must stop re-rendering the whole answer per
candidate row (O(n^2)) while returning byte-identical answers. The reference below
is the pre-N16 implementation, kept verbatim as the oracle.
"""
import json
import random

import pytest

from taskmaster.native import budget as budget_mod
from taskmaster.native.budget import Answer, Selection, budget


# ── Reference: the pre-N16 fill loop, verbatim ──────────────────────────────


def _reference_budget(*, envelope, mandatory, selections, limit_bytes):
    for selection in selections:
        if selection.total < selection.offset + len(selection.items):
            raise ValueError("total below rows")
    mandatory_bytes = budget_mod._size(mandatory)
    omitted = {selection.name: selection.total - selection.offset for selection in selections}
    selected: dict = {selection.name: [] for selection in selections}

    def render(rows, left):
        return budget_mod._stabilise(envelope, mandatory, rows, limit_bytes=limit_bytes,
                                     mandatory_bytes=mandatory_bytes, omitted=left,
                                     over_budget=False)

    best = None
    text, block = render(selected, omitted)
    if block["used_bytes"] <= limit_bytes:
        best = ({name: [] for name in selected}, dict(omitted), text, block)
    trial, trial_omitted = {name: [] for name in selected}, dict(omitted)
    done = False
    for selection in selections:
        for item in selection.items:
            trial[selection.name].append(item)
            trial_omitted[selection.name] = (selection.total - selection.offset
                                             - len(trial[selection.name]))
            candidate, candidate_block = render(trial, trial_omitted)
            if candidate_block["used_bytes"] <= limit_bytes:
                best = ({name: list(rows) for name, rows in trial.items()}, dict(trial_omitted),
                        candidate, candidate_block)
            elif candidate_block["used_bytes"] - budget_mod._omission_bytes(candidate_block) > limit_bytes:
                done = True
                break
        if done:
            break
    if best is None:
        text, block = budget_mod._stabilise(envelope, mandatory, selected, limit_bytes=limit_bytes,
                                            mandatory_bytes=mandatory_bytes, omitted=omitted,
                                            over_budget=True)
        return Answer(text=text, budget=block, selected={})
    selected, omitted, text, block = best
    return Answer(text=text, budget=block,
                  selected={name: rows for name, rows in selected.items() if rows})


# ── Random inputs ───────────────────────────────────────────────────────────

_ALPHABET = "abcXYZ019 _-\"\\/\n\té€ß漢字😀"


def _text(rng, lo=0, hi=30):
    return "".join(rng.choice(_ALPHABET) for _ in range(rng.randint(lo, hi)))


def _value(rng, depth=0):
    kind = rng.randint(0, 6 if depth < 2 else 3)
    if kind == 0:
        return rng.randint(-10 ** 6, 10 ** 12)
    if kind == 1:
        return _text(rng)
    if kind == 2:
        return rng.choice([None, True, False, 1.5, -0.25, 1e21])
    if kind == 3:
        return _text(rng, 0, 4)
    if kind in (4, 5):
        return {_text(rng, 1, 6): _value(rng, depth + 1) for _ in range(rng.randint(0, 4))}
    return [_value(rng, depth + 1) for _ in range(rng.randint(0, 4))]


def _case(rng):
    names = ["handovers", "tasks", "bugs", "notes", "ideas", "ελ"]
    selections = []
    for _ in range(rng.randint(0, 4)):
        # Duplicate names are allowed on purpose: the fill must mirror the
        # reference's name-keyed bookkeeping exactly.
        name = rng.choice(names)
        items = [_value(rng) for _ in range(rng.randint(0, 25))]
        offset = rng.randint(0, 3)
        total = offset + len(items) + rng.choice([0, 0, 1, 7, 120, 10 ** 5])
        selections.append(Selection(name, items, total, offset))
    envelope = {_text(rng, 1, 5): _value(rng) for _ in range(rng.randint(0, 3))}
    if rng.random() < 0.1:
        envelope["selected"] = "shadowed"
    if rng.random() < 0.1:
        envelope["mandatory"] = 1
    mandatory = rng.choice([{"clear": True, "blockers": []}, _value(rng), [_value(rng)]])
    full = _reference_budget(envelope=envelope, mandatory=mandatory, selections=selections,
                             limit_bytes=10 ** 9).budget["used_bytes"]
    limit = rng.choice([0, 50, rng.randint(0, full + 50), full, full - 1, full + 1,
                        rng.randint(max(0, full // 2), full + 5)])
    return dict(envelope=envelope, mandatory=mandatory, selections=selections, limit_bytes=limit)


@pytest.mark.parametrize("seed", range(40))
def test_linear_budget_matches_the_reference_byte_for_byte(seed):
    rng = random.Random(seed)
    for _ in range(25):
        case = _case(rng)
        expected = _reference_budget(**case)
        actual = budget(**case)
        assert actual.text == expected.text
        assert dict(actual.budget) == dict(expected.budget)
        assert {k: list(v) for k, v in actual.selected.items()} == \
            {k: list(v) for k, v in expected.selected.items()}


def test_boundary_limits_around_every_prefix_match_the_reference():
    rng = random.Random(7)
    items = [{"id": f"H-{n}", "t": _text(rng, 0, 12)} for n in range(30)]
    selections = [Selection("handovers", items[:18], 40), Selection("tasks", items[18:], 12)]
    env, mand = {"store_id": "s", "sequence": 1}, {"clear": True, "blockers": []}
    top = _reference_budget(envelope=env, mandatory=mand, selections=selections,
                            limit_bytes=10 ** 9).budget["used_bytes"]
    for limit in range(0, top + 3):
        case = dict(envelope=env, mandatory=mand, selections=selections, limit_bytes=limit)
        assert budget(**case).text == _reference_budget(**case).text


# ── Work assertion: bytes encoded grow linearly in the row count ────────────


def _encoded_bytes(n_rows):
    counter = {"bytes": 0}
    real = budget_mod._encode

    def counting(payload):
        text = real(payload)
        counter["bytes"] += len(text)
        return text

    items = [{"id": f"T-{n:05d}", "title": "x" * 40} for n in range(n_rows)]
    budget_mod._encode = counting
    try:
        answer = budget(envelope={"store_id": "s"}, mandatory={"clear": True, "blockers": []},
                        selections=[Selection("tasks", items, n_rows)], limit_bytes=10 ** 9)
    finally:
        budget_mod._encode = real
    assert len(answer.selected["tasks"]) == n_rows
    return counter["bytes"], len(answer.text)


def test_encoding_work_is_linear_in_rows():
    small_work, small_text = _encoded_bytes(200)
    large_work, large_text = _encoded_bytes(1600)
    # Quadratic fill encodes ~n^2/2 rows (8x rows -> ~64x work). Linear stays ~8x.
    assert large_work / small_work < 10
    # And the constant is small: a handful of full renders, not one per row.
    assert large_work < 12 * large_text
