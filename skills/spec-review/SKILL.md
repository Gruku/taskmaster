---
name: spec-review
description: "Adversarial design review of a task's spec or plan before implementation begins. Invoke when the user says 'review this spec', 'challenge this design', 'is this the right approach?', 'spec review', or after writing a new spec for a critical/high task. Reviews the proposed approach for assumptions, scope, edge cases, and predicted blast radius — does NOT review code."
---

# Spec Review

Pre-implementation design gate. The user has a task with a written spec/plan and wants to know whether the *approach* is sound before any code gets written. This is adversarial — challenge the design, not pat it on the back.

This skill does NOT review code. For post-implementation code review, use `taskmaster:review-gate`.

## Arguments

- `task_id` (optional) — specific task ID. If omitted, uses the current task in scope (asks if multiple match).
- `--codex` / `--no-codex` (optional) — force enable/skip the Codex adversarial pass.
  Default behavior:
    - `critical` priority: auto-suggest, ask user before running
    - `high` priority: offer if Codex is detected, default off
    - `medium` / `low`: never run
  Codex is detected if `~/.claude/plugins/cache/openai-codex/` exists or `codex` is on PATH.
  If `--codex` is passed but Codex isn't available, WARN and continue without it.

## When This Should Run

Lifecycle: `todo (with spec)` → **spec-review** → `pick-task` → `in-progress` → … → `review-gate` → `in-review`.

Run spec-review:
- After writing a spec for a critical/high task, before `pick-task`.
- On demand whenever the user wants a second opinion on an approach.
- Re-run if the spec was significantly revised.

Skip spec-review when there's no spec/plan, or for medium/low tasks (it's overkill).

## Steps

1. **Get task details** — call `backlog_get_task(task_id)`. Resolve the spec/plan path:
   - First check `task.docs.spec` and `task.docs.plan`.
   - Otherwise search `docs/specs/`, `docs/plans/`, and `{sub_repo}/docs/` for a file matching the task ID.
   - If no spec/plan exists: stop. Tell the user "No spec/plan found for `{task_id}` — write one first, or skip spec-review for this task."

2. **Gate A: Spec sanity (always runs)**

   Read the spec/plan. Check it covers, at minimum:
   - **Goal** — what problem this solves and why now.
   - **Approach** — the chosen design, not just "we'll add X."
   - **Scope** — what's in and what's explicitly out.
   - **Risk / open questions** — at least one.
   - **Target files or modules** — even a rough list helps blast radius.

   For each missing item: WARN, list what's missing, ask if the user wants to fill it in or proceed anyway.

3. **Gate B: Adversarial Design Review (Claude)**

   Read the spec and enough of the codebase to ground the critique. Then challenge the design across these axes — be specific, cite files/lines:

   - **Assumptions** — what does this spec assume that may not hold? (existing data shape, API contracts, user behavior, performance budget)
   - **Scope creep / scope gaps** — does the spec do too much? Too little? Are there obvious sibling cases excluded?
   - **Alternative approaches** — is there a simpler / smaller / safer way? Name one or two and say why the spec's approach is or isn't preferable.
   - **Failure modes** — where will this break first under load, partial failure, concurrent writes, malformed input?
   - **Reversibility** — if this ships and is wrong, how hard is it to undo?
   - **Dependencies & coupling** — does this entangle modules that should stay separate?

   Group findings by severity:
   - **Critical** — design has a fundamental flaw; should not proceed without rework.
   - **Important** — should be addressed in spec before implementation, may proceed with explicit acknowledgment.
   - **Minor** — worth considering, not blocking.

4. **Gate C: Codex Adversarial Pass (opt-in, second opinion)**

   Skip silently if Codex isn't detected and not requested.

   Codex is the precision knife — best for catching blind spots and cross-codebase patterns Claude's own review missed. Two key constraints:

   - `/codex:adversarial-review` is **diff-only** and won't work on prose — do NOT use it here.
   - Instead, dispatch the `codex:codex-rescue` subagent with explicit review-only framing.

   Build a focus from what Gate B *didn't* flag (avoids paying for a duplicate pass). Example:

   ```
   Agent({
     subagent_type: "codex:codex-rescue",
     description: "Codex adversarial spec review",
     prompt: `REVIEW-ONLY. Do not write or modify code. Do not propose patches.

   Read the spec at <abs path to spec> and the surrounding codebase. Produce
   an adversarial design review challenging the chosen approach.

   Claude's own review already flagged: <bullet list of Gate B findings>.

   Look specifically for blind spots Claude may have missed:
   - Repeated bugs in this area that the spec doesn't address.
   - Implicit assumptions about <area-specific concerns>.
   - Cross-codebase patterns that suggest this approach has been tried/rejected before.
   - Edge cases in <specific functions or flows> that the spec glosses over.

   Return a concise list of issues with file:line citations. Group by Critical /
   Important / Minor. Do not summarize Claude's findings — only add new ones.`
   })
   ```

   Tag Codex findings with `(codex)` so the source is visible when merged into the report.

5. **Gate D: Predicted Blast Radius**

   Call `backlog_blast_radius(task_id, mode="predictive")`. This uses the task's anchors / target files (or the spec's declared targets) to estimate fan-out.

   Interpret the result with judgment:
   - Modules and subsystems likely affected.
   - Overlapping in-progress tasks — call out conflicts loudly: "⚠ `{other_task}` is in-progress in the same area."
   - Existing features that may need updating given the proposed changes.
   - 2–4 specific suggested follow-ups or additional tasks the spec implies but doesn't cover.

   Verdict (advisory only — never blocks):
   - **PASS** — low fan-out, no overlaps, well-contained.
   - **WARN** — moderate fan-out, or shared modules.
   - **WARN (loud)** — overlapping in-progress task on the same files.

6. **Present Results — lead with the verdict.**

   ```
   Spec Review — {task_id}
   ─────────────────────────────────
   Gate A — Spec Sanity:           PASS / WARN
   Gate B — Adversarial (Claude):  PASS / FAIL (N issues)
   Gate C — Adversarial (Codex):   PASS / FAIL / SKIP (not installed | opt-out)
   Gate D — Blast Radius:          PASS / WARN (advisory)
   ```

   Then list issues grouped by severity, tagged with their source (Claude / Codex), with file:line citations.

   **Blocking rules:**
   - Critical findings (either source) block — the spec needs revision before `pick-task`.
   - Important findings require explicit user acknowledgment.
   - Minor findings, WARN/SKIP, and Gate D never block.

7. **Record the review**

   Save a record on the task so it's visible to `pick-task`, `review-gate`, and the dashboard, and so we don't re-run unnecessarily:

   ```
   backlog_set_spec_review(
     task_id,
     verdict="pass" | "warn" | "fail",
     spec_path="<path that was reviewed>",
     codex_used=true | false,
     critical_count=N,
     important_count=N,
   )
   ```

   If the spec is revised later, re-running spec-review overwrites the record automatically. To explicitly invalidate a prior review (e.g. major spec rewrite without re-reviewing), call `backlog_clear_spec_review(task_id)`.

8. **Next step**

   If the verdict is PASS or WARN with acknowledged Important findings: "Ready to `pick-task {task_id}` and start implementation."

   If FAIL: "Revise the spec to address the Critical findings, then re-run spec-review."

## Why This Skill Exists

`review-gate` reviews **code** — defects, regressions, spec adherence. It can't tell you whether you were building the right thing in the first place.

`spec-review` reviews **the plan** — design choices, assumptions, scope. Catching a bad design here is dramatically cheaper than catching it post-implementation.

Adversarial framing belongs at spec time. Once code is written, the question shifts from "is this the right approach?" to "did we execute the chosen approach correctly?" — that's review-gate's job, with a precision (not adversarial) framing.

## Related

- `taskmaster:review-gate` — post-implementation code review.
- `taskmaster:pick-task` — start implementation after spec-review passes.
- `superpowers:brainstorming` — for spec *creation*, not review.
