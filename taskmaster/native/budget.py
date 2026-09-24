"""User intent: bound the *selected* part of an agent answer by bytes, and never
the mandatory part (D1). A trimmed blocker list is indistinguishable from a clear
one at the point of use, so mandatory context is always returned complete and the
answer says it went over instead. Constraint: the reported `used_bytes` is the
bytes of the string actually returned, and `omitted` counts come from the caller's
paired `COUNT(*)` — never from the length of the page it happened to read.
"""
from dataclasses import dataclass, field
import json
import logging
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


# How many answers fell back to the rendering fill because the size prediction
# disagreed with a render (see `budget`). Expected to stay 0; logged when it moves.
FALLBACKS = 0
_LOG = logging.getLogger(__name__)


class _Sizer:
    """Predicts `_stabilise`'s settled size without rendering the selected rows.

    The encoding is compositional: a list of k rows encodes as `[` + the rows
    joined by `,` + `]`, and the mandatory value appears once. So the frame is
    rendered with every non-empty section as `[0]` and mandatory as `0`, and the
    row bytes, separators and mandatory bytes are added back. Each row is
    encoded once; each candidate costs the frame (envelope + budget block), not
    the rows delivered so far. The result is checked against a real render
    before it is returned (see `budget`).
    """

    def __init__(self, envelope, mandatory_bytes, limit_bytes):
        self.envelope = envelope
        self.mandatory_extra = mandatory_bytes - 1  # "0" stands in for mandatory
        self.limit_bytes = limit_bytes
        self.mandatory_bytes = mandatory_bytes

    def settle(self, counts, row_bytes, omitted) -> dict:
        # `[0]` rather than `[]`: `_assemble` drops empty sections from the wire.
        frame_selected = {name: [0] for name, count in counts.items() if count}
        extra = self.mandatory_extra + sum(row_bytes[name] + counts[name] - 2
                                           for name in frame_selected)
        used = 0
        for _ in range(12):
            block = _budget_block(limit_bytes=self.limit_bytes, used_bytes=used,
                                  mandatory_bytes=self.mandatory_bytes, over_budget=False,
                                  omitted=omitted)
            frame = _encode(_assemble(self.envelope, 0, frame_selected, block))
            size = len(frame.encode("utf-8")) + extra
            if size == used:
                return block
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
    names = {selection.name: None for selection in selections}
    sizer = _Sizer(envelope, mandatory_bytes, limit_bytes)

    # The longest prefix that fits, not the first prefix that does not: the wire
    # budget block names only sections with rows left out, so delivering one more
    # row can *shrink* the answer (a section's omission entry disappears once it
    # is complete), and the empty selection is not the smallest answer. Rows only
    # ever add bytes, so the scan stops once a prefix would overflow even with its
    # whole omission entry taken away.
    #
    # The scan works on sizes only (`_Sizer`); rows are rendered once, for the
    # answer actually returned. `trial` only ever grows by appending, so the best
    # answer is recorded as per-section prefix lengths, not copies.
    trial = {name: [] for name in names}
    counts = {name: 0 for name in names}
    row_bytes = {name: 0 for name in names}
    trial_omitted = dict(omitted)
    best = None
    # Checked renders: the answer returned, and the state that ended the scan (the
    # first prefix too large even without its omission entry, or the empty answer
    # when nothing fits). If a prediction disagrees with its render, the scan's
    # decisions cannot be trusted and the answer comes from the rendering fill.
    checks = []
    empty_block = sizer.settle(counts, row_bytes, omitted)
    if empty_block["used_bytes"] <= limit_bytes:
        best = (dict(counts), dict(omitted))
    done = False
    for selection in selections:
        name = selection.name
        for item in selection.items:
            trial[name].append(item)
            counts[name] += 1
            row_bytes[name] += _size(item)
            trial_omitted[name] = selection.total - selection.offset - counts[name]
            candidate_block = sizer.settle(counts, row_bytes, trial_omitted)
            if candidate_block["used_bytes"] <= limit_bytes:
                best = (dict(counts), dict(trial_omitted))
            elif candidate_block["used_bytes"] - _omission_bytes(candidate_block) > limit_bytes:
                checks.append((dict(counts), dict(trial_omitted), candidate_block["used_bytes"]))
                done = True
                break
        if done:
            break

    def diverged(state_counts, state_omitted, predicted):
        rows = {name: trial[name][:state_counts[name]] for name in names}
        _text, rendered = _stabilise(envelope, mandatory, rows, limit_bytes=limit_bytes,
                                     mandatory_bytes=mandatory_bytes, omitted=state_omitted,
                                     over_budget=False)
        return rendered["used_bytes"] != predicted

    def fallback():
        global FALLBACKS
        FALLBACKS += 1
        _LOG.warning("byte budget size prediction diverged from the render; "
                     "answered by the rendering fill (%d so far)", FALLBACKS)
        return _budget_by_rendering(envelope, mandatory, selections, limit_bytes, mandatory_bytes)

    if best is None:
        checks.append(({name: 0 for name in names}, dict(omitted), empty_block["used_bytes"]))
    if any(diverged(*check) for check in checks):
        return fallback()

    # Over budget means exactly one thing: no answer fits, not even the one that
    # carries the blockers alone. It is never "some selected row was dropped".
    if best is None:
        empty = {name: [] for name in names}
        text, block = _stabilise(envelope, mandatory, empty, limit_bytes=limit_bytes,
                                 mandatory_bytes=mandatory_bytes, omitted=omitted,
                                 over_budget=True)
        return Answer(text=text, budget=block, selected={})
    best_counts, best_omitted = best
    selected = {name: trial[name][:best_counts[name]] for name in names}
    text, block = _stabilise(envelope, mandatory, selected, limit_bytes=limit_bytes,
                             mandatory_bytes=mandatory_bytes, omitted=best_omitted,
                             over_budget=False)
    if block["used_bytes"] != sizer.settle(best_counts, _row_bytes(selected), best_omitted)["used_bytes"]:
        return fallback()
    return Answer(text=text, budget=block,
                  selected={name: rows for name, rows in selected.items() if rows})


def _row_bytes(selected: Mapping[str, Sequence[Any]]) -> dict:
    return {name: sum(_size(row) for row in rows) for name, rows in selected.items()}


def _budget_by_rendering(envelope, mandatory, selections, limit_bytes, mandatory_bytes) -> Answer:
    """The pre-N16 fill: every candidate rendered in full. O(n^2); the fallback only."""
    omitted = {selection.name: selection.total - selection.offset for selection in selections}
    selected: dict = {selection.name: [] for selection in selections}

    def render(rows, left):
        return _stabilise(envelope, mandatory, rows, limit_bytes=limit_bytes,
                          mandatory_bytes=mandatory_bytes, omitted=left, over_budget=False)

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
    if best is None:
        text, block = _stabilise(envelope, mandatory, selected, limit_bytes=limit_bytes,
                                 mandatory_bytes=mandatory_bytes, omitted=omitted, over_budget=True)
        return Answer(text=text, budget=block, selected={})
    selected, omitted, text, block = best
    return Answer(text=text, budget=block,
                  selected={name: rows for name, rows in selected.items() if rows})
