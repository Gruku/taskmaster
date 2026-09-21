# User intent: N09's unmeasured exit half — do representative agent journeys need
# fewer calls and bytes on the new tools while yielding the same required context?
# Shared by the seeded harness (tests/test_agent_journeys.py) and the CodeMaestro-copy run.
"""Agent journeys, each spelled twice: the call sequence today's playbooks give an
agent, and the sequence the N09 tools allow. Every call is made through the public
tool function and its answer is measured in UTF-8 bytes, the unit `backlog_context`
budgets in (D1).

A journey is four pieces, all taking the same `ids` mapping:

- `setup` puts the project in the state the journey starts from. Not measured.
- `prelude` (new path only) is a call the new path makes *before* the gap, such as
  taking a change cursor. Measured, because an agent really pays for it.
- `gap` is what other sessions do while this one is away. Not measured, and run
  identically before either path.
- `old` / `new` are the paths themselves.

"Required context" is the mandatory blocker set of the task in focus, as `(kind,
id)`: the gates, dependencies, bugs, handovers, human action and claims that must
be known before acting. The new path reads it from `backlog_context`; the old path
is credited with a blocker whenever the blocker's id appears anywhere in its
answers, which is the most generous reading the rendered text allows.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, field
import json
import re
from typing import Callable

from taskmaster import backlog_server as bs

PEER = "otherhost-4242-abcdef12"   # a session on another machine: never judged dead


@contextmanager
def as_session(session: str):
    real = bs.SESSION_ID
    bs.SESSION_ID = session
    try:
        yield
    finally:
        bs.SESSION_ID = real


@dataclass
class Trace:
    calls: list = field(default_factory=list)   # (tool, bytes)
    answers: list = field(default_factory=list)  # (tool, text)

    def run(self, tool: str, **kwargs) -> str:
        answer = getattr(bs, tool)(**kwargs)
        self.calls.append((tool, len(answer.encode("utf-8"))))
        self.answers.append((tool, answer))
        return answer

    @property
    def count(self) -> int:
        return len(self.calls)

    @property
    def bytes(self) -> int:
        return sum(size for _tool, size in self.calls)

    def text(self) -> str:
        return "\n".join(answer for _tool, answer in self.answers)

    def contexts(self) -> list[dict]:
        return [json.loads(answer) for tool, answer in self.answers if tool == "backlog_context"]


@dataclass
class Journey:
    name: str
    old: Callable[[Trace, dict], None]
    new: Callable[[Trace, dict], None]
    focus: str | None                              # key into ids; None = no task in focus
    setup: Callable[[dict], None] = lambda ids: None
    prelude: Callable[[Trace, dict], None] = lambda trace, ids: None
    gap: Callable[[dict], None] = lambda ids: None


SECTION_KINDS = {"bugs": "bug", "handovers": "handover", "dependencies": "dependency"}


def blocker_set(context: dict) -> set[tuple[str, str]]:
    return {(b["kind"], str(b["id"])) for b in context["mandatory"]["blockers"]}


def named_facts(context: dict) -> set[tuple[str, str]]:
    """Everything a context answer names: its blockers and its selected rows."""
    facts = blocker_set(context)
    for section, rows in context.get("selected", {}).items():
        if section in SECTION_KINDS:
            facts |= {(SECTION_KINDS[section], str(row["id"])) for row in rows}
    return facts


def close_bugs(context: dict) -> set[tuple[str, str]]:
    """Open bugs filed against the focus, whatever their severity.

    `backlog_complete_task` refuses on *any* open `found_in` bug, while the
    mandatory set holds only P0/P1 and unstated severities (scope D8). What a
    close needs is therefore wider than `mandatory`, and the selected `bugs`
    section is where the rest is found.
    """
    return {("bug", str(row["id"])) for row in context.get("selected", {}).get("bugs", [])
            if row.get("status") == "open"}


def old_coverage(required: set[tuple[str, str]], text: str) -> set[tuple[str, str]]:
    """The required blockers the old path's answers name at all."""
    return {(kind, ident) for kind, ident in required if ident in text}


# ── The journeys ────────────────────────────────────────────────────────────


def _orient_old(t: Trace, ids: dict) -> None:
    # start-session glance: dashboard, thread board, desk.
    t.run("backlog_status")
    t.run("backlog_thread_list")
    t.run("backlog_note", action="list")


def _orient_new(t: Trace, ids: dict) -> None:
    t.run("backlog_status")
    t.run("backlog_context", scope="project", include=["handovers", "notes"])


def _pick_old(t: Trace, ids: dict) -> None:
    # pick-task glance: steps 1, 2, 3, 4, 5a, 5b.
    t.run("backlog_next_available")
    t.run("backlog_status")
    t.run("backlog_dependencies", task_id=ids["pick"])
    t.run("backlog_pick_task", task_id=ids["pick"])
    t.run("backlog_handover_list", task_id=ids["pick"], status="open", limit=3)
    t.run("backlog_issue_list", status="open")


def _pick_new(t: Trace, ids: dict) -> None:
    t.run("backlog_next_available")
    t.run("backlog_claim", action="status")          # the parallel-task check
    t.run("backlog_context", focus=ids["pick"], scope="task", include=["handovers"])
    t.run("backlog_pick_task", task_id=ids["pick"])


def _resume_setup(ids: dict) -> None:
    bs.backlog_pick_task(task_id=ids["resume"], force=True)


def _resume_prelude(t: Trace, ids: dict) -> None:
    # Before stepping away, the new path takes a cursor on the epic it works in.
    ids["_cursor"] = json.loads(t.run("backlog_changes_since", epic=ids["resume_epic"]))["cursor"]


def _resume_gap(ids: dict) -> None:
    """A peer files a bug and asks for an action on the task, and edits elsewhere."""
    with as_session(PEER):
        ids["resume_bug"] = created_id(bs.backlog_bug_create(
            title="Regression seen while away", found_in=ids["resume"], severity="P1"))
        ids["resume_handover"] = created_id(bs.backlog_handover_create(
            tldr="Peer looked at the task", next_action="rerun the flaky suite",
            task_ids=[ids["resume"]], thread=ids["resume_thread"]))
        for ident in ids["noise"]:
            bs.backlog_update_task(task_id=ident, field="priority", value="high")


def _resume_old(t: Trace, ids: dict) -> None:
    # pick-task step 0 ("continue"): board, thread, task, then step 4 and 5.
    t.run("backlog_thread_list")
    t.run("backlog_thread_resume", ref=ids["resume_thread"])
    t.run("backlog_get_task", task_id=ids["resume"])
    t.run("backlog_pick_task", task_id=ids["resume"])
    t.run("backlog_handover_list", task_id=ids["resume"], status="open", limit=3)
    t.run("backlog_issue_list", status="open")


def _resume_new(t: Trace, ids: dict) -> None:
    t.run("backlog_changes_since", cursor=ids["_cursor"], epic=ids["resume_epic"])
    t.run("backlog_context", scope="session", include=["handovers"])
    t.run("backlog_claim", action="renew", task_id=ids["resume"])


def _close_setup(ids: dict) -> None:
    bs.backlog_pick_task(task_id=ids["close"], force=True)


def _close_old(t: Trace, ids: dict) -> None:
    # review-gate steps 1, 8b and 9, then end-session's close.
    t.run("backlog_get_task", task_id=ids["close"])
    t.run("backlog_task_pipeline", task_id=ids["close"])
    t.run("backlog_bug_list", status="open", found_in=ids["close"])
    _close_writes(t, ids)


def _close_new(t: Trace, ids: dict) -> None:
    t.run("backlog_get_task", task_id=ids["close"])
    # `bugs` is selected because the close refuses on an open bug of any severity.
    t.run("backlog_context", focus=ids["close"], scope="task", include=["bugs"])
    _close_writes(t, ids)


def _close_writes(t: Trace, ids: dict) -> None:
    """The same writes on both paths: dispose of the open bugs, pass the gate, close.

    No session summary rides on the close: a native store refuses one until N11's
    PROGRESS export lands, and the paths must stay comparable on both stores.
    """
    for bug in ids.get("close_open_bugs", ()):
        t.run("backlog_bug_update", bug_id=bug, field="status", value="shelved")
    for gate in ids.get("close_gates", ("review-gate",)):
        t.run("backlog_record_gate", task_id=ids["close"], gate=gate, verdict="pass",
              commit_sha="0" * 40)
    t.run("backlog_complete_task", task_id=ids["close"])


JOURNEYS = (
    Journey("orient", _orient_old, _orient_new, focus=None),
    Journey("pick", _pick_old, _pick_new, focus="pick"),
    Journey("resume", _resume_old, _resume_new, focus="resume",
            setup=_resume_setup, prelude=_resume_prelude, gap=_resume_gap),
    Journey("close", _close_old, _close_new, focus="close", setup=_close_setup),
)


@dataclass
class Measured:
    journey: str
    old: Trace
    new: Trace
    required: set          # what the new path says must be known at the decision point
    covered_old: set       # the part of it the old path's answers name
    named_new: set         # every fact the new path's context answers name
    ids: dict              # the ids the new path ran with, including any the gap minted

    def row(self) -> str:
        return (f"{self.journey}: calls {self.old.count} -> {self.new.count}, "
                f"bytes {self.old.bytes} -> {self.new.bytes}, "
                f"required {len(self.required)}, old names {len(self.covered_old)}")


def measure(journey: Journey, ids: dict, fresh: Callable[[str], object]) -> Measured:
    """Run both paths, each on its own fresh copy of the same starting state.

    `fresh(label)` must return a context manager that points the server at a new
    copy of the starting project and restores it afterwards.
    """
    traces, ran = {}, {}
    for path in ("old", "new"):
        run_ids = dict(ids)
        trace = Trace()
        with fresh(f"{journey.name}-{path}"):
            journey.setup(run_ids)
            if path == "new":
                journey.prelude(trace, run_ids)
            journey.gap(run_ids)
            getattr(journey, path)(trace, run_ids)
        traces[path], ran[path] = trace, run_ids
    old, new = traces["old"], traces["new"]
    contexts = new.contexts()
    required = (blocker_set(contexts[0]) | (close_bugs(contexts[0]) if journey.name == "close" else set())
                if contexts and journey.focus else set())
    named = set().union(*(named_facts(c) for c in contexts)) if contexts else set()
    return Measured(journey.name, old, new, required, old_coverage(required, old.text()), named,
                    ran["new"])


CREATED = re.compile(r"\b(B-\d+|ISS-\d+|NOTE-\d+|\d{4}-\d{2}-\d{2}-[a-z0-9-]+)")


def created_id(answer: str) -> str:
    match = CREATED.search(answer)
    assert match, answer
    return match.group(1)
