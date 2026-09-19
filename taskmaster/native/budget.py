"""User intent: bound the *selected* part of an agent answer by bytes, and never
the mandatory part (D1). A trimmed blocker list is indistinguishable from a clear
one at the point of use, so mandatory context is always returned complete and the
answer says it went over instead. Constraint: the reported `used_bytes` is the
bytes of the string actually returned, and `omitted` counts come from the caller's
paired `COUNT(*)` — never from the length of the page it happened to read.
"""
from dataclasses import dataclass, field
import json
from typing import Any, Mapping, Sequence


@dataclass(frozen=True)
class Selection:
    """One selected section: the rows read, and how many exist.

    `total` is the store's count for the same predicate in the same snapshot.
    It can exceed `len(items)` because the read was itself limited — which is
    exactly why the omission count cannot be derived from the page.
    """
    name: str
    items: Sequence[Any]
    total: int


@dataclass(frozen=True)
class Answer:
    text: str
    budget: Mapping[str, Any]
    selected: Mapping[str, Sequence[Any]] = field(default_factory=dict)


def _encode(payload: Mapping[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


def _size(payload: Mapping[str, Any]) -> int:
    return len(_encode(payload).encode("utf-8"))


def _assemble(envelope, mandatory, selected, budget_block) -> dict:
    return {**dict(envelope), "mandatory": mandatory,
            "selected": {name: rows for name, rows in selected.items() if rows},
            "budget": budget_block}


def _budget_block(*, limit_bytes, used_bytes, mandatory_bytes, over_budget, omitted) -> dict:
    return {"applies_to": "selected", "limit_bytes": limit_bytes, "used_bytes": used_bytes,
            "mandatory_bytes": mandatory_bytes, "over_budget": over_budget,
            "omitted": omitted, "omitted_total": sum(omitted.values())}


def _stabilise(envelope, mandatory, selected, *, limit_bytes, mandatory_bytes, omitted,
               over_budget) -> "tuple[str, dict]":
    """Render until `used_bytes` equals the size of the render that reports it.

    `used_bytes` is inside the answer it measures, so its own digits change the
    total. The iteration `n <- size(answer with used_bytes=n)` is non-decreasing
    in the digit count and reaches a fixed point in a couple of rounds; without
    it the number would be a lie by one or two bytes, which is the one thing a
    byte budget may not be.
    """
    used = 0
    for _ in range(12):
        block = _budget_block(limit_bytes=limit_bytes, used_bytes=used,
                              mandatory_bytes=mandatory_bytes, over_budget=over_budget,
                              omitted=omitted)
        text = _encode(_assemble(envelope, mandatory, selected, block))
        size = len(text.encode("utf-8"))
        if size == used:
            return text, block
        used = size
    raise RuntimeError("byte budget failed to settle on a stable used_bytes")


def budget(*, envelope: Mapping[str, Any], mandatory: Any,
           selections: Sequence[Selection], limit_bytes: int) -> Answer:
    """Assemble one answer, dropping selected rows until it fits.

    Selected rows are filled in the order given and the fill stops at the first
    row that does not fit, so what comes back is a prefix of the selection — the
    only shape a continuation cursor can resume from honestly.
    """
    for selection in selections:
        if selection.total < len(selection.items):
            raise ValueError(f"selection `{selection.name}` total {selection.total} is below "
                             f"the {len(selection.items)} rows handed in")

    mandatory_bytes = _size(mandatory)
    omitted = {selection.name: selection.total for selection in selections}
    selected: dict = {selection.name: [] for selection in selections}

    # The floor: mandatory complete, nothing selected. Over budget is decided
    # here and nowhere else — it means the caller must raise the limit, not that
    # some selected row was dropped.
    text, block = _stabilise(envelope, mandatory, selected, limit_bytes=limit_bytes,
                             mandatory_bytes=mandatory_bytes, omitted=omitted, over_budget=False)
    if block["used_bytes"] > limit_bytes:
        text, block = _stabilise(envelope, mandatory, selected, limit_bytes=limit_bytes,
                                 mandatory_bytes=mandatory_bytes, omitted=omitted,
                                 over_budget=True)
        return Answer(text=text, budget=block, selected={})

    for selection in selections:
        stop = False
        for item in selection.items:
            trial = {name: list(rows) for name, rows in selected.items()}
            trial[selection.name].append(item)
            trial_omitted = dict(omitted)
            trial_omitted[selection.name] = selection.total - len(trial[selection.name])
            candidate, candidate_block = _stabilise(
                envelope, mandatory, trial, limit_bytes=limit_bytes,
                mandatory_bytes=mandatory_bytes, omitted=trial_omitted, over_budget=False)
            if candidate_block["used_bytes"] > limit_bytes:
                stop = True
                break
            selected, omitted, text, block = trial, trial_omitted, candidate, candidate_block
        if stop:
            break

    return Answer(text=text, budget=block,
                  selected={name: rows for name, rows in selected.items() if rows})
