"""User intent: prove N09's exit criterion directly — `backlog_context` never
reports a task as clear while something blocks it (§5.2(1), the milestone's gate).

A seeded population of tasks is generated over every blocker-relevant dimension
and each task's `mandatory` answer is compared, on both stores, against a naive
oracle written here from the scope document — not by calling `blockers.py`. Two
readings computed by different code, compared: `clear` must imply the oracle
finds nothing, the converse must hold too, and legacy must equal native.

Claims are produced through the real tools (pick, status change, a bare
`locked_by` write) on a controlled clock, because the defects this gate exists
for live in how those writes interact, not in any one document shape.
"""
from __future__ import annotations

from contextlib import contextmanager
import datetime as _datetime
from contextlib import closing
import json
import os
import random
import sqlite3

import pytest

from taskmaster import backlog_server as bs
from taskmaster import store
import native_twins
from native_twins import hand_edit_entity, hand_set_holder, make_twins

SEEDS = (11, 23, 37, 41, 59)
TASKS = 30
T0 = _datetime.datetime(2026, 9, 17, 12, 0, 0, tzinfo=_datetime.timezone.utc)
SHORT_TTL = 60             # seconds: burnt by the jump below
JUMP = _datetime.timedelta(hours=2)  # past SHORT_TTL, well inside the 4-hour default

# ── The oracle's reading of the spec (§2.1, §2.5, §4a D6, §4c, §4c′) ─────────

VERDICT_GATES = {   # a lane's blocking gates are its review/verdict gates
    "full": ("spec-review", "plan-review", "review-gate"),
    "standard": ("design-review", "review-gate"),
    "express": ("review-gate",),
}
# Every open bug blocks, whatever its severity: the close refuses on any of them,
# and nothing context calls clear may be refused at close (user decision, §4f).
# The producer an answered blocker belongs to. An `unknown` blocker names its
# producer as its id, so it maps through that id instead.
PRODUCER = {"gate": "gates", "dependency": "dependencies", "bug": "bugs",
            "handover": "handovers", "human_action": "human_action", "claim": "claim"}


def _gate_done(record):
    return bool(record) and (record.get("skipped") or record.get("status") == "done"
                             or record.get("verdict") == "pass")


def oracle(task_id, docs, bugs, handovers, claim_truth, session):
    """Every reason `task_id` is not clear, from the documents alone, as
    `(producer, why)` — the producer being the fact the reason is about."""
    reasons = []
    doc = docs.get(task_id)
    if doc is None:
        return [("task", "task not found")]
    lane = doc.get("lane", "")
    gates = doc.get("gates") or {}
    if not isinstance(lane, str) or (lane and lane not in VERDICT_GATES):
        reasons.append(("gates", f"lane {lane!r} cannot be read"))
    elif not isinstance(gates, dict):
        reasons.append(("gates", "gates bag cannot be read"))
    else:
        for gate in VERDICT_GATES.get(lane, ()):
            record = gates.get(gate)
            if record is not None and not isinstance(record, dict):
                reasons.append(("gates", f"gate {gate} record cannot be read"))
            elif not _gate_done(record or {}):
                reasons.append(("gates", f"gate {gate} not satisfied"))

    depends = doc.get("depends_on") or []
    depends = [depends] if isinstance(depends, str) else depends
    if not isinstance(depends, list) or not all(isinstance(d, str) for d in depends):
        reasons.append(("dependencies", "depends_on cannot be read"))
    else:
        for dep in depends:
            if dep not in docs:
                reasons.append(("dependencies", f"dependency {dep} does not resolve"))
            elif docs[dep].get("status") != "done":
                reasons.append(("dependencies", f"dependency {dep} is {docs[dep].get('status')}"))

    action = doc.get("human_action")
    if isinstance(action, str):
        if action.strip():
            reasons.append(("human_action", "waits on a human action"))
    elif action:
        reasons.append(("human_action", "human_action cannot be read"))

    for bug in bugs:
        found_in = bug.get("found_in") or ""
        if not isinstance(found_in, str):
            reasons.append(("bugs", f"bug {bug['id']} found_in cannot be compared"))
        elif found_in.casefold() == task_id.casefold() and bug.get("status") == "open":
            reasons.append(("bugs", f"open bug {bug['id']}"))

    for handover in handovers:
        if (handover.get("status") == "open" and task_id in (handover.get("task_ids") or [])
                and (handover.get("next_action") or "").strip()):
            reasons.append(("handovers", f"handover {handover['id']} asks for an action"))

    holder = doc.get("locked_by")
    if holder and not isinstance(holder, str):
        reasons.append(("claim", "locked_by cannot be read"))
    elif holder and holder != session:
        truth = claim_truth.get(task_id, {})
        # D6: a claim stops blocking only when it is *proven* over — its holder's
        # process is gone, or the expiry stamped for *this* holder has passed.
        # An expiry some earlier holder left behind says nothing about this one.
        if not (truth.get("holder") == holder and (truth.get("dead") or truth.get("expired"))):
            reasons.append(("claim", f"claimed by {holder}"))
    return reasons


# ── The population ──────────────────────────────────────────────────────────


def _dead_pid():
    return next(pid for pid in range(4_000_000, 4_100_000) if not store._local_pid_alive(pid))


def _holders():
    host = store._local_host()
    return {"live": f"{host}-{os.getpid()}-0badc0de",
            "dead": f"{host}-{_dead_pid()}-0badc0de",
            "unjudgeable": "otherhost-4242-abcdef12",
            "shapeless": "not a session id"}


@contextmanager
def _as(session):
    real = bs.SESSION_ID
    bs.SESSION_ID = session
    try:
        yield
    finally:
        bs.SESSION_ID = real


def _at(moment):
    native_twins.CLOCK.update(at=moment, tick=True)


@bs._transactional("test_raw_edit")
def _raw_edit(mutate):
    """A hand edit through the legacy store: the shapes the tools refuse to write."""
    data = bs._load()
    mutate(data)
    bs._mutate_and_save(data)
    return "ok"


CLAIMS = ("self", "live", "dead", "unjudgeable", "stale_live",
          "stale_unjudgeable", "handed_to_live", "handed_to_unjudgeable", "handed_to_dead",
          "bare_live", "bare_unjudgeable", "bare_shapeless", "bare_malformed")


def _plan(rng):
    """One population's plan: per task, the claim story and the raw field shapes.

    Each dimension is left benign most of the time and perturbed only sometimes,
    so a good share of tasks are clear — a population where everything is blocked
    by something would pass `clear ⟹ nothing blocks` without testing it.
    """
    ids = [f"test-epic-{n:03d}" for n in range(1, TASKS + 1)]

    def sometimes(benign, *interesting, p=0.25):
        return rng.choice(interesting) if rng.random() < p else benign

    plan = {}
    for index, ident in enumerate(ids):
        # Laneless is the benign default: `backlog_add_task` would otherwise give
        # every task a lane whose review gates are all still pending.
        lane = sometimes("", "full", "standard", "express", "express", "bogus", 5, p=0.4)
        gates = {}
        if isinstance(lane, str) and lane in VERDICT_GATES:
            gates = sometimes({}, "not a mapping", p=0.08)
            if isinstance(gates, dict):
                for gate in VERDICT_GATES[lane]:
                    record = sometimes({"verdict": "pass"}, None, {"verdict": "warn"},
                                       {"verdict": "fail"}, {"status": "done"}, {"skipped": True},
                                       {"status": "pending"}, "garbled", p=0.3)
                    if record is not None:
                        gates[gate] = record
        earlier = ids[:index]
        depends = sometimes(None, "some", "some", "missing", "single", "malformed", p=0.35)
        if depends == "some" and earlier:
            depends = rng.sample(earlier, k=min(len(earlier), rng.randint(1, 2)))
        elif depends == "missing":
            depends = ([rng.choice(earlier)] if earlier and rng.random() < 0.5 else []) + ["ghost-999"]
        elif depends == "single" and earlier:
            depends = rng.choice(earlier)
        elif depends == "malformed":
            depends = rng.choice([[1, "x"], {"a": 1}])
        else:
            depends = None
        plan[ident] = {
            "claim": sometimes("none", *CLAIMS, p=0.6),
            "lane": lane, "gates": gates, "depends_on": depends,
            "human_action": sometimes(None, "", "   ", "sign the contract", True, False, 0, ["x"],
                                      p=0.25),
            "status": rng.choice(["todo", "in-progress", "done", "done", "done", "blocked", "in-review"]),
        }
    bugs = []
    for n in range(rng.randint(2, 6)):
        target = rng.choice(ids)
        bugs.append({"found_in": rng.choice([target, target.upper(), "", "ghost-999"]),
                     "status": rng.choice(["open", "open", "fixed", "shelved"]),
                     "severity": rng.choice(["P0", "P1", "P2", "P3", None])})
    # Always present, so the population cannot miss the case the close gate is
    # strictest about: an open low-severity bug, once in another case.
    bugs.append({"found_in": ids[-1], "status": "open", "severity": "P3"})
    bugs.append({"found_in": ids[-2].upper(), "status": "open", "severity": "P2"})
    if rng.random() < 0.2:
        # One bug no comparison can read poisons every task's bug producer.
        bugs.append({"found_in": rng.choice([["x"], 5]), "status": "open", "severity": "P2"})
    handovers = [{"task_ids": rng.sample(ids, k=rng.randint(1, 2)),
                  "next_action": rng.choice(["", "  ", "resume the refactor"]),
                  "status": rng.choice(["open", "open", "closed"])}
                 for _ in range(rng.randint(2, 5))]
    return plan, bugs, handovers


def _seed(plan, bugs, handovers, holders):
    """Build the population through the real tools, then hand-edit the shapes.

    Returns the claim ground truth the oracle reads: who each expiry was stamped
    for, and whether that holder is proven dead or its expiry has passed.
    """
    truth = {}
    _at(T0)
    for ident in plan:
        bs.backlog_add_task(title=f"Task {ident}", epic="test-epic", phase="dev")

    # Before the jump: claims whose expiry will have burnt by query time.
    for ident, entry in plan.items():
        story = entry["claim"]
        if story.startswith("stale_"):
            holder = holders[story.split("_", 1)[1]]
            with _as(holder):
                assert "Error" not in bs.backlog_pick_task(ident, ttl_seconds=SHORT_TTL)
            truth[ident] = {"holder": holder, "expired": True}
        elif story.startswith("handed_to_"):
            # This session's claim, paused: the status change drops the holder
            # and nothing else — the expiry it was stamped with stays behind.
            assert "Error" not in bs.backlog_pick_task(ident, ttl_seconds=SHORT_TTL)
            bs.backlog_update_task(task_id=ident, field="status", value="todo")

    _at(T0 + JUMP)
    for ident, entry in plan.items():
        story = entry["claim"]
        if story.startswith("handed_to_"):
            holder = holders[story.rsplit("_", 1)[1]]
            bs.backlog_update_task(task_id=ident, field="status", value="in-progress")
            hand_set_holder(ident, holder)
            truth[ident] = {"holder": holder, "dead": story.endswith("dead")}
        elif story in ("self", "live", "dead", "unjudgeable"):
            holder = bs.SESSION_ID if story == "self" else holders[story]
            with _as(holder):
                assert "Error" not in bs.backlog_pick_task(ident)
            truth[ident] = {"holder": holder, "dead": story == "dead"}
        elif story.startswith("bare_") and story != "bare_malformed":
            # A holder written without a pick: no expiry was ever stamped for it.
            holder = holders[story.split("_", 1)[1]]
            hand_set_holder(ident, holder)
            truth[ident] = {"holder": holder}

    for n, bug in enumerate(bugs, start=1):
        bs.backlog_bug_create(title=f"Bug {n}", found_in="")
    for n, handover in enumerate(handovers, start=1):
        bs.backlog_handover_create(tldr=f"Handover {n}", task_ids=handover["task_ids"],
                                   next_action=handover["next_action"] or "")

    def shapes(data):
        for ident, entry in plan.items():
            task, _epic = bs._find_task(data, ident)
            for name in ("lane", "gates", "depends_on", "human_action"):
                if entry[name] is not None:
                    task[name] = entry[name]
            if entry["claim"] == "none":
                task["status"] = entry["status"]
            if entry["claim"] == "bare_malformed":
                task["locked_by"] = 17
    assert _raw_edit(shapes).startswith("ok")
    # Bug and handover shapes go straight into their store rows. Edited through
    # the loaded backlog they never persisted, and every bug read back with no
    # `found_in` and every handover as open: both producers went unexercised.
    for ident, bug in zip(_store_ids("bug"), bugs):
        def shape_bug(doc, bug=bug):
            doc.update(found_in=bug["found_in"], status=bug["status"])
            if bug["severity"] is None:
                doc.pop("severity", None)
            else:
                doc["severity"] = bug["severity"]
        hand_edit_entity("bug", ident, shape_bug)
    for ident, handover in zip(_store_ids("handover"), handovers):
        hand_edit_entity("handover", ident,
                         lambda doc, handover=handover: doc.__setitem__("status", handover["status"]))
    return truth


def _store_ids(kind):
    store.reset_for_tests()
    with closing(sqlite3.connect(bs.ROOT / ".taskmaster" / "local" / "store.db")) as connection:
        return [row[0] for row in connection.execute(
            "SELECT id FROM entities WHERE kind=? AND deleted=0 ORDER BY id", (kind,))]


def _documents():
    data = bs._load()
    docs = {str(task.get("id")): task for epic in data.get("epics", []) for task in epic.get("tasks", [])}
    bugs = [dict(doc, id=ident) for ident, doc, _b in bs._dict_rows(data, "bug")]
    handovers = [dict(doc, id=ident) for ident, doc, _b in bs._dict_rows(data, "handover")]
    return docs, bugs, handovers


def _close_gate_refusals(twins, ident):
    """The open bugs each store's close gate would refuse `ident` on, read through
    the very predicate `backlog_complete_task` calls, or the error it raises."""
    from taskmaster.native.workflow import _bugs_found_in
    found = {}
    with twins.at(twins.legacy):
        try:
            found["legacy"] = sorted(bs._open_bugs_for_task(bs._backlog_path(), ident)[0])
        except (AttributeError, ValueError) as exc:
            found["legacy"] = exc
    database = twins.native / ".taskmaster" / "local" / "store.db"
    with closing(sqlite3.connect(database)) as connection:
        try:
            found["native"] = sorted(_bugs_found_in(connection, ident)[0])
        except ValueError as exc:
            found["native"] = exc
    return found


def _assert_close_gate_agrees(twins, ident, answers, where):
    """Context clear on bugs => the close does not refuse on bugs, and the bugs it
    names are exactly the ones the close would refuse on. A close gate that cannot
    compare a `found_in` must meet an `unknown` bug producer, never a clear one."""
    for side, refused in _close_gate_refusals(twins, ident).items():
        blockers = answers[side]["mandatory"]["blockers"]
        named = sorted(b["id"] for b in blockers if b["kind"] == "bug")
        unknown = any(b["kind"] == "unknown" and b["id"] == "bugs" for b in blockers)
        if isinstance(refused, Exception):
            assert unknown, f"{where}: {side} close gate raised {refused!r} but context {blockers}"
            continue
        assert named == refused, f"{where}: {side} context names bugs {named}, close refuses on {refused}"
        if answers[side]["mandatory"]["clear"]:
            assert not refused, f"{where}: {side} context clear, close refuses on {refused}"


@pytest.mark.parametrize("seed", SEEDS)
def test_clear_never_hides_a_blocker_the_oracle_finds(tmp_path, monkeypatch, seed):
    rng = random.Random(seed)
    plan, bugs, handovers = _plan(rng)
    holders = _holders()
    truth = {}

    def seed_population():
        truth.update(_seed(plan, bugs, handovers, holders))

    twins = make_twins(tmp_path, monkeypatch, seed_population)
    with twins.at(twins.legacy):
        docs, bug_docs, handover_docs = _documents()

    clear_count = blocked_count = 0
    for ident in plan:
        answers = {}
        for side in ("legacy", "native"):
            with twins.at(getattr(twins, side)):
                answers[side] = json.loads(bs.backlog_context(focus=ident, scope="task",
                                                              include=[]))
        legacy, native = answers["legacy"], answers["native"]
        where = f"seed {seed}, {ident}, plan {plan[ident]!r}, claim truth {truth.get(ident)!r}"
        assert "error" not in legacy and "error" not in native, f"{where}: {legacy} / {native}"
        assert native["mandatory"] == legacy["mandatory"], f"{where}: the stores disagree"
        reasons = oracle(ident, docs, bug_docs, handover_docs, truth, bs.SESSION_ID)
        _assert_close_gate_agrees(twins, ident, answers, where)
        mandatory = legacy["mandatory"]
        if mandatory["clear"]:
            assert not reasons, f"{where}: reported clear, but {reasons}; doc {docs[ident]!r}"
            clear_count += 1
        else:
            assert reasons, f"{where}: blocked by {mandatory['blockers']} the oracle cannot see"
            blocked_count += 1
        # Stronger than `clear` alone: a task blocked for one reason must not hide
        # another. Each producer the oracle faults is one the answer names.
        assert {producer for producer, _why in reasons} ==             {PRODUCER.get(b["kind"], b["id"]) for b in mandatory["blockers"]},             f"{where}: oracle {reasons} vs answer {mandatory['blockers']}"
    # A population that is all-blocked or all-clear proves nothing either way.
    assert blocked_count and (clear_count or any(
        not isinstance(b.get("found_in") or "", str) for b in bugs)), (seed, clear_count, blocked_count)
