# Spec Review: Gate Detail

The axes for Gates A, B and D, and the task summary written after the gate is recorded. Verdict rules and the recording call are in the playbook.

## Gate A: spec sanity

Read the spec. At minimum it should cover:
- **Goal**: what problem this solves and why now.
- **Approach**: the chosen design, not just "we'll add X".
- **Scope**: what is in and what is explicitly out.
- **Risk / open questions**: at least one.
- **Target files or modules**: even a rough list helps blast radius.

List each missing item as a WARN row. Don't stop to ask; the closing message asks whether the user wants to fill them in.

## Gate B: adversarial design review

Read the spec and enough of the codebase to ground the critique. Be specific and cite files and lines.

- **Assumptions**: what does the spec assume that may not hold (data shape, API contracts, user behavior, performance budget)?
- **Scope creep / gaps**: does it do too much or too little? Are obvious sibling cases excluded?
- **Alternative approaches**: is there a simpler, smaller or safer way? Name one or two and say why the spec's approach is or isn't better.
- **Failure modes**: where does it break first under load, partial failure, concurrent writes, malformed input?
- **Reversibility**: if it ships and is wrong, how hard is it to undo?
- **Dependencies and coupling**: does it entangle modules that should stay separate?

Severity:
- **Critical**: a fundamental design flaw; don't proceed without rework.
- **Important**: address in the spec before implementing, or proceed with explicit acknowledgment.
- **Minor**: worth considering, not blocking.

## Gate D: predicted blast radius

`backlog_blast_radius(task_id, mode="predictive")`. Read the result for:
- modules and subsystems likely affected;
- overlapping in-progress tasks, which go at the top: "task X is in-progress in the same area";
- existing features that may need updating;
- 2-4 follow-ups or extra tasks the spec implies but doesn't cover.

Advisory verdict, never blocking: PASS (low fan-out, no overlap), WARN (moderate fan-out or shared modules), WARN with the overlap called out (an in-progress task on the same files).

## Task summary

After `backlog_record_gate`, leave a short trace on the task so `pick-task` and the dashboard show the review. The spec stays in its own doc; don't copy it into the task. Task `notes` are replaced whole by `backlog_update_task`, so read first: `backlog_get_task(task_id, sections=["notes"])`, append this block to what is there, and write it back with `backlog_update_task(task_id, "notes", <existing notes + block>)`.

```
### Spec Review
- Verdict: pass | warn | fail
- Date: YYYY-MM-DD
- Spec: <path>
- Findings: critical=N, important=N (codex: yes|no)
```

Skip this on the express lane, where nothing is recorded.
