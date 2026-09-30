"""User intent: one answer to "what blocks this task", so N09 can prove its exit
criterion — missing mandatory context is never reported as clear. The blocker set
is fixed in code (D8): a project that could configure a blocker out of existence
would make that criterion unprovable. Constraint: a producer that could not answer
degrades to a blocker, never to an absence of blockers.
"""
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from taskmaster import taskmaster_v3 as domain_v3

# The fixed set. `unknown` is not a sixth producer — it is how any of the five
# reports that it could not answer, which is itself mandatory context.
MANDATORY_BLOCKER_KINDS = (
    "gate", "dependency", "bug", "handover", "human_action", "claim", "unknown",
)
UNKNOWN_KIND = "unknown"
FIXED_STATE = {"bug": "open", "handover": "open", UNKNOWN_KIND: "unknown"}
FIXED_SOURCE = {"dependency": "depends_on", "bug": "found_in", "handover": "next_action",
                UNKNOWN_KIND: "resolver"}

# Every open bug whose `found_in` names the task blocks, whatever its severity.
# `backlog_complete_task` refuses on each of them (`_open_bugs_for_task`, native
# `workflow._bugs_found_in`), and a task context calls clear must never then be
# refused at close. The callers feed exactly the rows that close-gate predicate
# returns, so this producer adds no filter of its own.


@dataclass(frozen=True)
class Unknown:
    """A producer's answer when it could not answer. Never falsy-by-accident:
    callers test `isinstance(value, Unknown)`, not truthiness."""
    reason: str
    detail: str = ""


@dataclass(frozen=True)
class Claim:
    """Who holds a task, whether that holder is live, and whether the claim has
    expired. `live=None` means liveness could not be decided.

    `expired` is what actually decides blocking, and it is a separate answer
    because the two questions are separate: a claim whose holder cannot be
    judged is still determinately *held and unexpired* (`native.claims` waits
    out the stored TTL instead of guessing), and reporting that as an
    unanswered producer would misattribute a known fact. A producer that only
    has liveness leaves `expired` as `None` and keeps the older reading, where
    an unjudgeable holder is an `unknown` blocker.
    """
    holder: str
    live: "bool | None"
    expired: "bool | None" = None


@dataclass(frozen=True)
class Blocker:
    kind: str
    id: str
    state: str
    source: str = ""
    extra: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        # The test that "the constant and the resolver agree" is enforced here:
        # a kind the constant does not name cannot be constructed at all.
        if self.kind not in MANDATORY_BLOCKER_KINDS:
            raise ValueError(f"`{self.kind}` is not a mandatory blocker kind "
                             f"(expected one of {MANDATORY_BLOCKER_KINDS})")

    @property
    def reason(self) -> str:
        return str(self.extra.get("reason", ""))

    @property
    def detail(self) -> str:
        return str(self.extra.get("detail", ""))

    def as_dict(self) -> dict:
        # A field the kind already fixes is left out: every bug and handover
        # blocker is open, every unknown is unknown, and a dependency, bug,
        # handover or unknown blocker has only the one source. A gate's state and
        # lane, a dependency's state and a claim's state vary, so those stay.
        answer = {"kind": self.kind, "id": self.id}
        if self.state != FIXED_STATE.get(self.kind):
            answer["state"] = self.state
        if self.source and self.source != FIXED_SOURCE.get(self.kind):
            answer["source"] = self.source
        answer.update(self.extra)
        return answer


@dataclass(frozen=True)
class Facts:
    """What the five mandatory producers found, as gathered by the caller.

    Every producer defaults to `Unknown("not_supplied")` on purpose: a caller
    that forgets one gets a blocker naming it, never a clear answer.
    """
    task: "Mapping[str, Any] | Unknown" = Unknown("not_supplied")
    dependencies: "Mapping[str, str] | Unknown" = Unknown("not_supplied")
    bugs: "Sequence[Mapping[str, Any]] | Unknown" = Unknown("not_supplied")
    handovers: "Sequence[Mapping[str, Any]] | Unknown" = Unknown("not_supplied")
    claim: "Claim | None | Unknown" = Unknown("not_supplied")
    session: str = ""


@dataclass(frozen=True)
class Resolution:
    clear: bool
    blockers: tuple
    gate_state: str = ""
    human_action: str = ""

    def as_dict(self) -> dict:
        # An empty gate state or human action is left out rather than spelled
        # "": the blockers already say everything that blocks.
        answer = {"clear": self.clear, "blockers": [blocker.as_dict() for blocker in self.blockers]}
        # Left out too when a gate blocker already says it ("review-gate" pending
        # is `review-gate:pending`); kept when it says something no blocker does.
        if self.gate_state and not any(
                f"{b.id}:{b.state}" == self.gate_state for b in self.blockers if b.kind == "gate"):
            answer["gate_state"] = self.gate_state
        if self.human_action:
            answer["human_action"] = self.human_action
        return answer


def probe(producer):
    """Run one producer, turning its refusal into an `Unknown`.

    N08 made `_bugs_found_in` raise on a `found_in` shape it cannot compare,
    rather than silently miss the bug. That refusal has to reach the caller as
    mandatory context, so it becomes a blocker here instead of an exception that
    a caller might swallow into an empty list.
    """
    try:
        return producer()
    except Exception as exc:  # noqa: BLE001 - any producer failure is mandatory context
        return Unknown("producer_failed", str(exc))


def _unknown(producer: str, reason: str, detail: str = "") -> Blocker:
    return Blocker(kind=UNKNOWN_KIND, id=producer, state="unknown", source="resolver",
                   extra={"reason": reason, "detail": detail})


def _from_unknown(producer: str, value: Unknown) -> Blocker:
    return _unknown(producer, value.reason, value.detail)


def _gate_blockers(task: Mapping[str, Any]) -> list:
    lane = task.get("lane", "")
    if not isinstance(lane, str):
        return [_unknown("gates", "malformed_lane", type(lane).__name__)]
    if lane and lane not in domain_v3.LANE_GATES:
        return [_unknown("gates", "unknown_lane", lane)]
    gates = task.get("gates") or {}
    if not isinstance(gates, Mapping):
        return [_unknown("gates", "malformed_gates", type(gates).__name__)]
    blocking = domain_v3.blocking_gates(lane)
    for name in blocking:
        record = gates.get(name)
        if record is not None and not isinstance(record, Mapping):
            return [_unknown("gates", "malformed_gate_record", name)]
    found = []
    for name in blocking:
        record = gates.get(name) or {}
        if record.get("verdict") == "fail":
            found.append(Blocker(kind="gate", id=name, state="fail", source=f"lane:{lane}"))
        elif not domain_v3.gate_satisfied(record):
            found.append(Blocker(kind="gate", id=name, state="pending", source=f"lane:{lane}"))
    return found


def declared_dependencies(task: Mapping[str, Any]):
    """The task's declared dependency ids, or an `Unknown` for a shape that
    cannot be read. A bare string means one dependency, as every shipped site
    already treats it."""
    value = task.get("depends_on")
    declared = domain_v3.dependency_ids(value)
    if declared is None:
        return Unknown("malformed_depends_on", domain_v3.dependency_shape(value))
    return declared


def dependency_blockers(task: Mapping[str, Any], statuses: Mapping[str, str]) -> list:
    declared = declared_dependencies(task)
    if isinstance(declared, Unknown):
        return [_from_unknown("dependencies", declared)]
    found = []
    for ident in sorted(set(declared)):
        if ident not in statuses:
            # A missing id blocks, and says it could not be resolved. Every
            # availability reader answers through `unmet_dependencies`, so this
            # is the one place that decides it.
            found.append(Blocker(kind="dependency", id=ident, state="missing",
                                 source="depends_on", extra={"unresolved": True}))
        elif statuses[ident] != "done":
            found.append(Blocker(kind="dependency", id=ident, state=statuses[ident],
                                 source="depends_on"))
    return found


# How an availability reader names a `depends_on` it cannot read. It is one
# entry rather than ids guessed out of the shape, because the resolver reports
# one `unknown` blocker for it.
UNREADABLE_DEPENDENCIES = "depends_on (unreadable)"


def unmet_dependencies(task: Mapping[str, Any], statuses: Mapping[str, str]) -> list:
    """What a task waits on, as `next_available`, `pick_task`, `_derive_context`
    and `backlog_dependencies` name it: exactly the dependency blockers `resolve`
    reports, in the order the task declares them. Empty means not blocked by a
    dependency.

    `statuses` maps each existing task id to its status; an id it does not hold
    is unresolved and blocks. Readers must not default a missing id to a status
    of their own, or they are back to deciding blocking themselves.
    """
    found = dependency_blockers(task, statuses)
    if any(blocker.kind == UNKNOWN_KIND for blocker in found):
        return [UNREADABLE_DEPENDENCIES]
    blocked = {blocker.id for blocker in found}
    return [ident for ident in declared_dependencies(task) if ident in blocked]


def _bug_blockers(bugs: Sequence[Mapping[str, Any]]) -> list:
    found = []
    for bug in sorted(bugs, key=lambda row: str(row.get("id", ""))):
        if bug.get("status") != "open":
            continue
        severity = bug.get("severity")
        found.append(Blocker(kind="bug", id=str(bug.get("id", "")), state="open",
                             source="found_in", extra={"severity": severity if severity else None}))
    return found


def _handover_blockers(handovers: Sequence[Mapping[str, Any]]) -> list:
    found = []
    for handover in sorted(handovers, key=lambda row: str(row.get("id", ""))):
        action = (handover.get("next_action") or "").strip()
        if not action:
            continue
        found.append(Blocker(kind="handover", id=str(handover.get("id", "")), state="open",
                             source="next_action", extra={"next_action": action}))
    return found


def _claim_blockers(claim: "Claim | None", session: str, task_id: str) -> list:
    if claim is None or not claim.holder:
        return []
    if claim.holder == session:
        return []
    if claim.expired is None:
        # Liveness alone decided it: an unjudgeable holder is an open question.
        if claim.live is None:
            return [_unknown("claim", "claim_liveness_unknown", claim.holder)]
        if not claim.live:
            return []
    elif claim.expired:
        return []
    return [Blocker(kind="claim", id=task_id, state="held", source="locked_by",
                    extra={"by": claim.holder, "live": claim.live})]


def resolve(facts: Facts) -> Resolution:
    """The one mandatory-blocker answer for a task.

    `clear` is true if and only if the blocker list is empty, and the list holds
    an `unknown` entry for every producer that could not answer. There is no
    path on which an undetermined fact reads as an absent one.
    """
    found: list = []
    gate_state, human_action = "", ""
    task = facts.task
    if isinstance(task, Unknown):
        found.append(_from_unknown("task", task))
        task_id = ""
    else:
        task_id = str(task.get("id", ""))
        found.extend(_gate_blockers(task))
        if isinstance(facts.dependencies, Unknown):
            found.append(_from_unknown("dependencies", facts.dependencies))
        else:
            found.extend(dependency_blockers(task, facts.dependencies))
        human_action = task.get("human_action") or ""
        if not isinstance(human_action, str):
            found.append(_unknown("human_action", "malformed_human_action",
                                  type(human_action).__name__))
            human_action = ""
        human_action = human_action.strip()
        if human_action:
            found.append(Blocker(kind="human_action", id=task_id, state="waiting",
                                 source="task", extra={"action": human_action}))
        try:
            gate_state = domain_v3.compute_gate_state(task)
        except Exception:  # noqa: BLE001 - a shape `_gate_blockers` already reported
            gate_state = ""

    for producer, handler in (("bugs", _bug_blockers), ("handovers", _handover_blockers)):
        value = getattr(facts, producer)
        if isinstance(value, Unknown):
            found.append(_from_unknown(producer, value))
        else:
            found.extend(handler(value))

    if isinstance(facts.claim, Unknown):
        found.append(_from_unknown("claim", facts.claim))
    else:
        found.extend(_claim_blockers(facts.claim, facts.session, task_id))

    # Stable, so each producer's own order survives: gates stay in lane order
    # (the pipeline position a reader expects), the rest are already id-sorted.
    found.sort(key=lambda blocker: MANDATORY_BLOCKER_KINDS.index(blocker.kind))
    return Resolution(clear=not found, blockers=tuple(found),
                      gate_state=gate_state, human_action=human_action)
