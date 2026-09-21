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

    `offset` is where `items` starts within that total. Rows before it were
    delivered by an earlier page, so they are not omitted from this one.
    """
    name: str
    items: Sequence[Any]
    total: int
    offset: int = 0


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
            "budget": wire_block(budget_block)}


def _omission_bytes(block: Mapping[str, Any]) -> int:
    """An upper bound on the bytes the omission entries add to the wire block."""
    wire = wire_block(block)
    if "omitted" not in wire:
        return 0
    return len(_encode({"omitted": wire["omitted"], "omitted_total": wire["omitted_total"]})) + 2


def wire_block(block: Mapping[str, Any]) -> dict:
    """The budget block as the caller receives it: the facts, not the defaults.

    `used_bytes` always. `over_budget` and `mandatory_bytes` only when the
    blockers alone overflowed — the one case a caller must act on. `omitted` names
    only the sections that left rows out, with their total; an absent section or
    an absent total means nothing was left out. The limit is the caller's own
    argument and is not echoed back. `Answer.budget` keeps the full accounting.
    """
    wire = {"used_bytes": block["used_bytes"]}
    if block["over_budget"]:
        wire.update(over_budget=True, mandatory_bytes=block["mandatory_bytes"])
    omitted = {name: count for name, count in block["omitted"].items() if count}
    if omitted:
        wire.update(omitted=omitted, omitted_total=sum(omitted.values()))
    return wire


def _budget_block(*, limit_bytes, used_bytes, mandatory_bytes, over_budget, omitted) -> dict:
    return {"applies_to": "selected", "limit_bytes": limit_bytes, "used_bytes": used_bytes,
            "mandatory_bytes": mandatory_bytes, "over_budget": over_budget,
            "omitted": dict(omitted), "omitted_total": sum(omitted.values())}


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
        if selection.total < selection.offset + len(selection.items):
            raise ValueError(f"selection `{selection.name}` total {selection.total} is below "
                             f"the {len(selection.items)} rows handed in at offset {selection.offset}")

    mandatory_bytes = _size(mandatory)
    omitted = {selection.name: selection.total - selection.offset for selection in selections}
    selected: dict = {selection.name: [] for selection in selections}

    def render(rows, left):
        return _stabilise(envelope, mandatory, rows, limit_bytes=limit_bytes,
                          mandatory_bytes=mandatory_bytes, omitted=left, over_budget=False)

    # The longest prefix that fits, not the first prefix that does not: the wire
    # budget block names only sections with rows left out, so delivering one more
    # row can *shrink* the answer (a section's omission entry disappears once it
    # is complete), and the empty selection is not the smallest answer. Rows only
    # ever add bytes, so the scan stops once a prefix would overflow even with its
    # whole omission entry taken away.
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
            elif candidate_block["used_bytes"] - _omission_bytes(candidate_block) > limit_bytes:
                done = True
                break
        if done:
            break

    # Over budget means exactly one thing: no answer fits, not even the one that
    # carries the blockers alone. It is never "some selected row was dropped".
    if best is None:
        text, block = _stabilise(envelope, mandatory, selected, limit_bytes=limit_bytes,
                                 mandatory_bytes=mandatory_bytes, omitted=omitted,
                                 over_budget=True)
        return Answer(text=text, budget=block, selected={})
    selected, omitted, text, block = best
    return Answer(text=text, budget=block,
                  selected={name: rows for name, rows in selected.items() if rows})
