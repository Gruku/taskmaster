# Taskmaster v3 — Narrative Continuity & Compounding Context

> Status: Draft
> Date: 2026-04-26
> Origin: Conversation comparing Taskmaster with Storybloq, identifying the missing layer of cross-session compounding context.

---

## Table of Contents

- [Background](#background)
- [Design Goals](#design-goals)
- [Token Budget Contract](#token-budget-contract)
- [1. Folder Layout & Migration](#1-folder-layout--migration)
- [2. Handovers](#2-handovers)
- [3. Lessons](#3-lessons)
- [4. Issues](#4-issues)
- [5. Recap + Snapshots + PreCompact](#5-recap--snapshots--precompact)
- [6. Auto Mode (Task / Epic / Phase)](#6-auto-mode-task--epic--phase)
- [Build Order](#build-order)
- [Open Questions](#open-questions)

---

## Background

Taskmaster v1 introduced the lifecycle (pick → worktree → implement → review-gate → end-session). v2 introduced spikes and exploration tolerance. Both shaped *individual task* quality.

The remaining gap is *between* sessions and *across* tasks: a long session writes a lot of unwritten context that disappears at compaction; the same mistake gets corrected three times in three weeks because nothing learns; bug reports get folded into tasks and lose their identity; auto mode is a manual sequence of skills.

v3 closes that gap with five additions, all built around one principle: **narrative continuity should compound, but tokens shouldn't.**

## Design Goals

1. **Compounding context.** Each session should make the project measurably easier to work on next time, not just produce code.
2. **Token discipline.** Every new feature declares its load tier and a soft target. We watch the targets in tests as signals; we don't fail builds on them. Hard count caps (active lessons, handover index size, top-N issues) keep growth bounded.
3. **Lazy-by-default.** Heavy artifacts (handovers, lesson bodies, task bodies) live on disk and are fetched on demand. Indexes are slim.
4. **Subagent isolation for batch.** Auto-epic and auto-phase must not accumulate per-task detail in the main context — each task runs in a fresh subagent.
5. **Reversible migrations.** v3 reads v2 backlogs without data loss; the migration is a one-way upgrade users opt into.

## Token Budget Guidance

Soft targets, not hard ceilings — but watched. The point is to keep skills lean by default and notice when something is drifting, not to fail builds on overrun.

| Entry point | Soft target | Warn at | What's loaded |
|---|---|---|---|
| `start-session` | ~3,000 | 5,000 | Dashboard + recap + last session + latest handover frontmatter + lessons digest + core lessons |
| `pick-task` (additive) | ~1,500 | 3,000 | Task body + trigger-matched lessons + linked handovers' tldrs + linked issues |
| `review-gate` (additive) | ~800 | 1,500 | Task body diff + acceptance criteria |
| `end-session` (additive) | ~800 | 1,500 | Today's actions, handover prompt context |

If a surface exceeds its warn threshold in real use, we look at it — maybe trim, maybe raise the target with justification. Tests check the target as a signal, not a gate.

**Approximate breakdown of the start-session target (~3k):**
- Dashboard (phases/epics/active tasks): ~800
- Recap diff: ~250
- Last session log: ~300
- Latest handover frontmatter: ~250
- Lessons digest (≤30 entries × ~10 tokens): ~300
- Core lessons (≤5 full × ~100 tokens): ~500
- Slack for project-specific extras: ~600

Caps on counted things (≤30 active lessons, ≤5 core, ≤30 handover index entries, top 10 issues) stay as hard limits — they keep growth bounded — but the per-section token math is guidance.

---

## 1. Folder Layout & Migration

### Layout

```
.taskmaster/
├── backlog.yaml              # phase/epic structure + task INDEX (slim metadata)
├── tasks/
│   └── <id>.md               # one file per task, sections inside
├── handovers/
│   └── YYYY-MM-DD-<slug>.md  # session handovers
├── lessons/
│   └── L-<NNN>.md            # reusable patterns/anti-patterns/gotchas
├── issues/
│   └── ISS-<NNN>.md          # bugs as first-class entities
├── phases/                   # OPTIONAL narrative docs for phases
│   └── <id>.md
├── epics/                    # OPTIONAL narrative docs for epics
│   └── <id>.md
├── snapshots/                # gitignored — for recap diff
│   └── last.json
└── auto/                     # gitignored — auto-mode state cursor
    └── state.json
```

### Why flat (no phase/epic folder nesting)

- Phase/epic membership changes; file paths shouldn't.
- Two sources of truth (path + frontmatter) drift.
- Cross-cutting tasks have nowhere to live.
- File path is identity; phase/epic are metadata.

### `backlog.yaml` shape (slim index)

```yaml
project: my-project
schema_version: 3
phases:
  - id: development
    name: Development
    status: active           # active | planned | done
epics:
  - id: features
    name: Features
    phase: development
    status: active
    tasks:
      - id: features-001
        title: Build login page
        status: in-progress     # todo | in-progress | in-review | done | wontfix
        priority: P1
        started: "2026-03-28"
        stage: 2                # auto-mode cursor (optional)
        estimate: 3d
        blocked_by: []
        # NO description, NO plan, NO notes — those live in tasks/features-001.md
issues:                          # NEW in v3 — see §4
  - id: ISS-001
    ...
handovers:                       # NEW in v3 — last 30 only, older archived
  - id: 2026-04-26-context-handoff
    date: "2026-04-26"
    tldr: "Picked T-001, plan approved, implementation pending."
    next_action: "Start IMPLEMENT stage on T-001."
    task_ids: [features-001]
lessons_meta:                    # NEW in v3 — index only, bodies in lessons/
  - id: L-001
    title: "Always read before edit"
    kind: anti-pattern
    tier: core
    reinforce_count: 7
```

The discipline: **`backlog.yaml` is the index. Bodies live elsewhere.**

### Per-task file: `tasks/<id>.md`

```markdown
---
id: features-001
title: Build login page
related_handovers: [2026-04-25-end-of-day]
related_issues: [ISS-014]
related_lessons: [L-007]
spec_review_status: approved      # null | pending | approved | rejected
last_updated: "2026-04-26"
---

## Description
Plain English, what and why.

## Acceptance Criteria
- [ ] User can log in with email + password
- [ ] Failed login shows inline error
- [ ] Session persists across reload

## Spec / Plan
(filled by spec-review skill — design before implementation)

## Decisions
(running log — appended during implementation, surfaces non-obvious choices)

## Notes
(scratch space)
```

Loaded only on `pick-task`, not at session start.

### Migration from v2

`taskmaster:migrate-v3` skill (and `taskmaster migrate` CLI):

1. Verify `backlog.yaml` has `schema_version: 2` or missing.
2. Create `.taskmaster/` directory with subdirs.
3. Move existing `backlog.yaml` to `.taskmaster/backlog.yaml`.
4. For each task with non-trivial inline content (description, plan, notes), extract to `.taskmaster/tasks/<id>.md`. Leave the slim metadata in `backlog.yaml`.
5. Tasks without inline content get no `tasks/<id>.md` — created on first pick.
6. Set `schema_version: 3`.
7. Add `.taskmaster/snapshots/` and `.taskmaster/auto/` to `.gitignore`.
8. Print a summary: N tasks migrated, M task files written, gitignore updated.

The migration is opt-in. v2 backlogs continue to work unmodified — v3 features are inert until migration.

---

## 2. Handovers

### Purpose

Capture the unwritten context at the end of a long session — decisions, blockers, "where I'd start tomorrow" — as a committed markdown artifact that the next session can read.

Solves the user's stated pain: writing 300k-token-context handoff messages by hand at the end of big sessions and having no place to put them.

### File format

`.taskmaster/handovers/YYYY-MM-DD-<slug>.md`

```markdown
---
id: 2026-04-26-login-impl
date: "2026-04-26"
tldr: "Login page wired up; OAuth pending; blocked on legal review of session storage."
next_action: "Resume T-001 IMPLEMENT once legal confirms cookie scope."
task_ids: [features-001]
context_size_at_write: "320k"      # optional — flags handovers written near compaction
session_kind: end-of-day           # end-of-day | context-handoff | crash-recovery | auto-stage
---

## Decisions
- Chose stateful sessions over JWT because of audit requirements.
- Deferred OAuth to a separate task (features-009) — out of scope here.

## Blockers
- Legal review on cookie scope.
- Need design for the "session expired" toast.

## Where I'd start
1. Read `src/auth/session.ts` — cookie-set logic is in `setSession()`.
2. The IMPLEMENT stage is mid-write — see uncommitted changes in `src/auth/`.
3. Tests are red (3 failures in `auth.test.ts`) — these are expected, not regressions.

## Open threads
- Should we prune session table or just expire? — discuss with backend team.
```

Body is freeform but the four sections (Decisions / Blockers / Where I'd start / Open threads) are conventions, not enforced.

### Index in `backlog.yaml`

Last 30 handovers carry id + date + tldr + next_action + task_ids + session_kind. Older entries get archived to `.taskmaster/handovers/_archive/<year>/` and dropped from the index.

### Skill flow

**`taskmaster:handover` (new):**
- Triggers: "wrap up", "ending the day", "write a handover", "I'm at 300k tokens", or auto-offered by `end-session` when session was big.
- Asks (if not provided): tldr (one line), next_action (one line), session_kind.
- Generates the four-section body from the conversation, asks user to confirm/edit.
- Writes file, updates `backlog.yaml` index, commits if requested.

**Integration with `start-session`:**
- Loads *only* the latest handover's frontmatter (~200 tokens) by default.
- If the latest handover's frontmatter has `session_kind: context-handoff`, suggest reading the body in full.
- Older handovers are reachable via `backlog_handover_get <id>`.

**Integration with `pick-task`:**
- If task has `related_handovers`, show their tldrs (not bodies). User can ask for full content.

### Token cost

| Surface | Tokens |
|---|---|
| Latest handover frontmatter at session start | ~200 |
| Per-task `related_handovers` tldrs | ~50 each, cap 3 = 150 |
| Full body on demand | varies (uncapped — user-requested) |

Count cap: index holds at most 30 entries. Archive sweep runs on `end-session`.

### Auto-offer rules in `end-session`

`end-session` proposes writing a handover when **any** of:
- Session length > 60 turns.
- Conversation token estimate > 200k.
- Task in flight (in-progress or stage > 1) at session end.
- User says "for tomorrow", "next time", "remind future me", or similar.

Otherwise the existing lightweight session log is fine.

---

## 3. Lessons

### Purpose

Project-scoped, structured knowledge that compounds. Where auto-memory captures *user* preferences globally, lessons capture *project* truths locally. Reinforcement makes the system *better* at the project the longer you use it.

Solves the user's stated pain: repetitive work and the same things breaking and being re-fixed across sessions.

### File format

`.taskmaster/lessons/L-<NNN>.md`

```markdown
---
id: L-007
title: "Always read auth/session.ts before editing auth flow"
kind: gotcha                        # pattern | anti-pattern | gotcha
triggers:                            # match against task fields/file paths
  files: ["src/auth/**"]
  task_titles_match: ["auth", "login", "session"]
  task_kinds: []
tier: active                         # active | core | retired
reinforce_count: 3
last_reinforced: "2026-04-25"
created: "2026-03-12"
related_tasks: [features-001, features-009]
related_issues: [ISS-014]
---

## Why
Session cookie logic has a non-obvious interaction with the legacy refresh-token path. Editing the auth flow without reading `setSession()` typically breaks one of the two paths.

## What to do
1. Read `src/auth/session.ts:setSession` end-to-end.
2. Check if your change crosses the refresh-token branch — if yes, add a test for both paths.
3. Run `auth.test.ts` before *and* after.

## Examples
- T-014 (2026-03-12): broke refresh path because didn't read setSession; reverted, redid with this lesson applied.
- T-019 (2026-04-08): same author, caught it pre-commit because lesson was loaded.
```

### Three-tier loading model

This is the core token discipline:

**Tier 1: digest (always loaded at session start)**
- Just `id + title + kind` for active lessons.
- ~10 tokens each, capped at **30 active lessons** → ~300 tokens.
- If > 30 active, lowest-reinforce-count lessons get retired (not deleted, just `tier: retired`).

**Tier 2: core (always loaded in full at session start)**
- Lessons with `tier: core` (manually marked, or auto-promoted after 5+ reinforcements).
- Capped at **5 core lessons** × ~100 tokens = ~500 tokens.
- Reserved for "this lesson applies to nearly every session in this project."

**Tier 3: trigger-matched (loaded on `pick-task`)**
- For each lesson in active tier, check if `triggers.files` glob-matches any file the task touches, or `triggers.task_titles_match` substring-matches the task title.
- Load full body of matching lessons, cap 3 hits per pick.
- ~100 tokens × 3 = 300 tokens worst case.

**Tier 4: manual fetch**
- `backlog_lesson_get L-014` for any lesson by id.
- Uncapped — user-driven.

### Reinforce semantics

- A lesson is *reinforced* when it was loaded and demonstrably applied (e.g., user accepted Claude's suggestion that referenced it, or Claude correctly avoided the anti-pattern).
- `reinforce_count++`, `last_reinforced = today`.
- Auto-promotion to core: at `reinforce_count >= 5` and `kind in {gotcha, anti-pattern}`, suggest promotion.
- Decay: lessons with `last_reinforced` > 180 days ago and `reinforce_count < 2` are retired automatically. Reviewable via `backlog_lesson_list --tier retired`.

### Skill flow

**`taskmaster:lesson` (new):**
- Triggers: "remember this", "this keeps happening", "we always do X here", user correction patterns.
- Asks for kind, triggers, why, what-to-do.
- Generates examples from session context.
- Writes file, updates `lessons_meta` in `backlog.yaml`.

**Auto-suggestion from corrections:**
- If `feedback` memory accumulates 2+ similar entries in this project, prompt: "This looks like a recurring correction in <project>. Promote to a lesson?"
- Doesn't auto-create — always confirms.

**Integration with `pick-task`:**
- Computes trigger matches from task title + likely-touched files (from spec or task title heuristics).
- Loads matching lesson bodies into context, with a 1-line preamble: "L-007 (gotcha): <title>".

### Token cost

| Surface | Tokens |
|---|---|
| Digest at session start | ≤300 |
| Core lessons full at session start | ≤500 |
| Trigger-matched on pick-task | ≤300 |
| **Total in-context per session** | **≤1,100** |

Count caps (≤30 active, ≤5 core, ≤3 trigger-matched per pick) enforced in skill code; token totals are watched as soft targets.

---

## 4. Issues

### Purpose

A bug exists whether or not someone's working on it. A *task* is the unit of work; an *issue* is the unit of broken-ness. They have different lifecycles. One issue can spawn multiple fix attempts; one task can close multiple issues.

Solves the user's stated pain: bug tracking currently piggybacks on tasks, losing severity/impact metadata and conflating "broken thing" with "work item."

### File format

`.taskmaster/issues/ISS-<NNN>.md`

```markdown
---
id: ISS-014
title: "Login form accepts whitespace-only password"
status: open                         # open | investigating | fixed | wontfix | duplicate
severity: P1                          # P0 (data loss/security) | P1 | P2 | P3 (cosmetic)
components: [auth, web]
impact: "User can create accounts with effectively no password if they use spaces."
location:
  - "src/auth/validate.ts:42"
  - "src/auth/forms/LoginForm.tsx:88"
discovered: "2026-04-15"
discovered_by: "manual QA"
resolved: null
related_tasks: [features-007]         # tasks attempting fix
fixed_in_task: null                   # set when status: fixed
duplicate_of: null
---

## Repro
1. Go to /login
2. Enter username, "   " (3 spaces) as password
3. Account is created with that password.

## Expected
Password validator should reject whitespace-only after trim.

## Investigation notes
- `validate.ts` trims then checks length, but length check is on un-trimmed value.
- ISS-009 was the same root cause but in signup; might be worth a shared validator.
```

### Index in `backlog.yaml`

`issues:` array with slim per-issue metadata: id, title, severity, status, components, related_tasks. No body. Capped display in `start-session` to top 10 by severity.

### Skill flow

**`taskmaster:issue` (new):**
- Triggers: "log a bug", "found an issue", "this is broken", "track this defect".
- Asks for severity, components, impact, repro.
- Writes file, updates index.
- Optionally creates a related task ("Want me to also create a fix task?").

**Integration with `pick-task`:**
- If task has `related_issues`, surface them with title + severity.
- On task completion, if any `related_issues[*].status` is still open, prompt: "ISS-014 still open — should we close it as fixed-in <task>, or leave for follow-up?"

**Integration with `start-session` dashboard:**
- New section: "Open issues (top 10 by severity)".
- P0 issues are bolded/flagged.

### Lifecycle

```
open → investigating → fixed
       ↓
       wontfix or duplicate
```

`fixed` requires `fixed_in_task` to be set (auto-filled when closed via task completion).

### Token cost

| Surface | Tokens |
|---|---|
| Top 10 issues in dashboard | ~150 |
| Per-task `related_issues` summaries | ~30 each, cap 3 = 90 |
| Full body on demand | uncapped, user-driven |

---

## 5. Recap + Snapshots + PreCompact

### Purpose

Two distinct things, both lightweight:

1. **`last_session`** (existing, slightly enhanced): "what *I* did last session."
2. **`recap`** (new): "what changed in *the project* since I last looked." Diff against the last snapshot of `backlog.yaml`.

Together they answer "what's the state of things?" without me re-reading the whole backlog.

### Snapshot format

`.taskmaster/snapshots/last.json` (gitignored):

```json
{
  "taken_at": "2026-04-26T18:14:00Z",
  "schema_version": 3,
  "structural_hash": "sha256:...",
  "tasks": {
    "features-001": { "status": "in-progress", "priority": "P1", "stage": 2 },
    "features-002": { "status": "todo", "priority": "P2" }
  },
  "issues": {
    "ISS-014": { "status": "open", "severity": "P1" }
  },
  "phase_active": "development"
}
```

Just the metadata that matters for diffing — not full bodies.

### Recap output

Compact diff format, ~200 tokens for typical projects:

```
Since last session (2026-04-25 → 2026-04-26):
+ T features-009 "Add SSO" (P1, todo)            [added]
~ T features-002 todo → in-progress              [you, 2026-04-26]
~ T features-007 in-progress → done              [you, 2026-04-26]
~ ISS-014 open → fixed (by features-007)         [auto, 2026-04-26]
- T infra-005                                    [deleted]

Phase development: 8/12 done, 2 in-progress, 2 todo.
```

The "[you]" / "[auto]" tags help separate user-driven backlog edits from in-session task transitions, useful when picking up after a teammate or an auto run.

### PreCompact hook

Runs `backlog_snapshot --quiet` before context compaction so post-compact `recap` reflects pre-compaction state. Cost: zero in-context tokens, ~100ms wall time.

Settings.json snippet (auto-installed by `init-taskmaster` v3):

```json
{
  "hooks": {
    "PreCompact": [{
      "matcher": "",
      "hooks": [
        { "type": "command", "command": "taskmaster snapshot --quiet" }
      ]
    }]
  }
}
```

Also runs on `end-session` and explicitly via `taskmaster snapshot`.

### Composition with `last_session`

`start-session` shows two sections:

```
## Recap (since 2026-04-25)
+ T features-009 "Add SSO" (P1, todo)
~ ISS-014 open → fixed
~ Phase development: 8/12 done

## Last session (you, 2026-04-25)
- Picked features-001, plan approved, halfway through IMPLEMENT
- Wrote handover 2026-04-25-end-of-day
- Logged 3 commits
```

Distinct: recap is *project state* delta, last_session is *your work* log.

### Token cost

| Surface | Tokens |
|---|---|
| Recap | ≤200 |
| Last session (existing) | ~200 |
| Snapshot file (on disk only) | 0 in context |

---

## 6. Auto Mode (Task / Epic / Phase)

### Purpose

Drive the existing skills as a state machine. Single-task auto already exists implicitly (pick → spec-review → implement → review-gate → end). Epic and phase auto loop the machine over a batch, with subagent isolation so the main context doesn't accumulate.

### State machine

```
PICK_TASK
  ↓
SPEC_REVIEW                    — gate: user approves plan (or --no-gate)
  ↓
WRITE_TESTS         (optional) — TDD if task is type=feature with tests
  ↓
IMPLEMENT
  ↓
TEST                           — run test suite
  ↓
REVIEW_GATE                    — gate: tests pass, diff reviewed
  ↓
HANDOVER_STUB                  — write a brief task-level handover
  ↓
END_SESSION                    — transition status to done, log
  ↓
(epic/phase mode: NEXT_TASK or COMPLETE)
```

Gates can be skipped with `--no-gate` (auto-approval) but emit a warning. Default is gated.

### Three entry points

**`taskmaster:auto-task <id>`** — runs the machine for one task. Equivalent to existing flow, but with explicit state persistence so a crash mid-stage is recoverable.

**`taskmaster:auto-epic <epic-id>`** — iterates `epic.tasks[*]` where `status == todo`, in priority/order. For each:
1. Dispatches a fresh subagent (`general-purpose`, `model: sonnet`) with `auto-task <id>` as its prompt.
2. Waits for subagent return.
3. Captures: status (done/failed/blocked), 1-paragraph summary, commit shas.
4. Main context only sees this 3-line summary per task.
5. If task failed and `--continue-on-fail` is unset, halts.
6. After all tasks: runs `epic-handover` aggregating per-task summaries into one epic-level handover.

**`taskmaster:auto-phase <phase-id>`** — iterates `phase.epics[*]` (epics in this phase), runs `auto-epic` for each. Phase-level handover at end. Same subagent isolation.

### Subagent isolation strategy

This is what makes batch auto viable token-wise. Each task subagent:
- Gets only its own task body, related lessons, related handovers (≤2k tokens entry).
- Runs the full inner state machine in its own context.
- Returns a structured result: `{status, summary, commits, handover_id, time_taken}`.
- Its working context is discarded.

The orchestrator (main context) accumulates only the structured results — ~50 tokens per task. An epic of 10 tasks costs ~500 tokens of orchestrator context regardless of how complex each task was.

### State persistence

`.taskmaster/auto/state.json` (gitignored):

```json
{
  "mode": "epic",
  "target": "features",
  "started_at": "2026-04-26T14:00:00Z",
  "cursor": {
    "task_id": "features-003",
    "stage": "IMPLEMENT"
  },
  "completed": [
    { "task_id": "features-001", "status": "done", "commits": ["abc123"] },
    { "task_id": "features-002", "status": "done", "commits": ["def456"] }
  ],
  "pending": ["features-004", "features-005"],
  "failed": []
}
```

PreCompact hook flushes this. On session restart, `taskmaster auto resume` continues from the cursor.

### Failure handling

Per-task failures classified:
- **`tests-failed`**: REVIEW_GATE rejects. By default, halt run, write handover with red-test summary, leave task in `in-review` for user.
- **`spec-rejected`**: SPEC_REVIEW gate rejected by user. Halt; task stays in `todo`.
- **`blocked`**: subagent reports a blocker (missing dep, external blocker). Halt; mark task `blocked`, write handover.
- **`crashed`**: subagent error. Halt; preserve state for resume.

Flags:
- `--continue-on-fail` — keep going past test failures (use for batch runs you don't want to babysit).
- `--no-gate` — skip user-approval gates for SPEC_REVIEW and REVIEW_GATE.
- `--dry-run` — print the task list and what would happen, don't execute.

### Per-task handover stubs

Each task in an auto-epic/phase run writes a *brief* handover at HANDOVER_STUB stage:

```markdown
---
id: 2026-04-26-auto-features-001
session_kind: auto-stage
task_ids: [features-001]
tldr: "Auto-completed: login page wired up, all tests green."
next_action: "Continue auto run; next: features-002."
---

## Decisions
- Used localStorage for session persistence (per L-007).

## Notes
- Touched 4 files, +120 / -8 LOC.
```

These are short and don't bloat the index — they're auto-archived to `.taskmaster/handovers/_archive/auto/` on epic/phase completion, replaced by a single epic/phase-level handover.

### Token cost

| Surface | Tokens |
|---|---|
| Orchestrator per task in batch | ~50 |
| Per-task handover stub (on disk, not in context) | 0 |
| Epic handover (final, in index) | ~200 |
| Auto state file (on disk) | 0 |

A 10-task auto-epic: ~500 tokens of main context for the run, regardless of task complexity. This is the biggest token win in v3.

---

## Build Order

Sequenced to minimize rework — earlier features should not need redesign when later ones land.

1. **Folder layout + migration** (foundation). Without this, nothing else has a home.
2. **PreCompact hook + snapshots + recap**. Independent of all other features. Smallest surface, immediate value, enables state-aware batch operations.
3. **Handovers**. Direct hit on user pain. Two new skills (write/read), one new index in backlog.yaml. Low risk.
4. **Issues**. Schema addition + new skill. Touches dashboard and pick-task. Medium risk.
5. **Lessons**. Most design care needed (token discipline, trigger system, promotion rules). Land after handovers prove the new layout works.
6. **Auto-task**. Composes existing skills with state persistence. Mostly orchestration code.
7. **Auto-epic + auto-phase**. Subagent dispatch layer on top of auto-task. Highest complexity, lands last so all the artifact types it produces (handovers, lessons-trigger, issue-close) are stable.

Each step ships independently, behind a feature flag in `init-taskmaster --v3-features=...` until v3 is the default.

## Open Questions

1. **Lessons promotion threshold.** Is 5 reinforcements the right bar for core tier? Tunable per-project in `config.yaml`?
2. **Handover archive cadence.** 30-entry index cap is heuristic. Does anyone need to scan handovers older than 30? If yes, expose `backlog_handover_search`.
3. **Issues vs spikes.** A bug investigation is sometimes a spike. Should `taskmaster:issue` ask "create a related spike?" or stay strictly scope-limited?
4. **Auto-phase failure aggregation.** If half the epic's tasks pass and half fail, what does `auto-phase` do? Default proposal: halt at first epic failure, but flag worth revisiting.
5. **Handover writes during auto.** Auto-stage handovers might be noisy. Maybe only write them when stage produced non-trivial decisions, judged by length/keyword heuristics?
6. **Migration UX.** Do we ship `taskmaster migrate` as a one-shot CLI, or as a guided skill that explains each change? Skill is friendlier but slower.
7. **Multiple in-flight auto runs.** Can a user have an auto-task running while also working manually on another task? State file is single-cursor today; multi-cursor adds complexity.

---

## Appendix: file size and count estimates

For a project with 100 tasks, 30 issues, 50 lessons, 200 handovers (after a year):

| Path | Files | Total size |
|---|---|---|
| `tasks/*.md` | 100 | ~500 KB |
| `handovers/*.md` (active) | 30 | ~60 KB |
| `handovers/_archive/**` | 170 | ~340 KB |
| `lessons/*.md` | 50 | ~100 KB |
| `issues/*.md` | 30 | ~60 KB |
| `backlog.yaml` | 1 | ~50 KB |
| **Total** | 381 | ~1.1 MB |

Comfortably git-trackable. Compaction-safe (no single huge file).
