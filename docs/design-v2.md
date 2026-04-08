# Taskmaster v2 — Design Document

> Status: Draft
> Date: 2026-03-21
> Origin: Conversation between gruku and Claude analyzing real friction from production use

---

## Table of Contents

- [Background](#background)
- [Pain Points](#pain-points)
- [Design Changes](#design-changes)
  - [1. Spike Work Type](#1-spike-work-type)
  - [2. Rename Milestone → Phase](#2-rename-milestone--phase)
  - [3. Task Anchors](#3-task-anchors)
  - [4. Task Budget per Epic](#4-task-budget-per-epic)
  - [5. Staleness Decay](#5-staleness-decay)
  - [6. Auto-Summary for Lightweight Sessions](#6-auto-summary-for-lightweight-sessions)
  - [7. Observe Untracked Work](#7-observe-untracked-work)
- [Updated Data Model](#updated-data-model)
- [Updated Hierarchy](#updated-hierarchy)
- [Migration Path](#migration-path)
- [Priority & Sequencing](#priority--sequencing)
- [Open Questions](#open-questions)
- [Visual Reference](#visual-reference)

---

## Background

Taskmaster v1 was built around a single assumption: all work has settled scope and benefits from full lifecycle tracking (pick → lock → worktree → implement → review-gate → end-session → log). This works well for execution-phase development where tasks are well-defined and isolation prevents conflicts.

In practice, a significant portion of development time is spent in exploration — figuring out what to build, iterating on approaches, discovering that the original direction was wrong. During this phase, the full ceremony adds friction without proportional value. The tracking overhead (creating tasks, picking them, logging sessions) can exceed the value of the tracking itself.

This was exposed in a real session where:

1. Multiple tasks were created, picked, implemented, completed, and session-logged — all against the wrong codebase (`predictions.html` instead of the React Observatory at `localhost:5173`)
2. The backlog accumulated 25+ tasks for a single feature redesign, creating noise instead of signal
3. Three layers of tracking existed for what was really one evolving thing (old tasks, new tasks on wrong target, React tasks)
4. The user's conclusion: "The backlog starts to get in our way instead of helping us, especially when we're not stabilized on features and require major redesigns"

Separately, external feedback identified that AI models consistently conflate Milestones with Epics — creating feature-milestones ("Auth milestone") instead of temporal phases ("Phase 1: Foundation"). The naming fails to communicate the intended purpose: attention filtering and coarse-grained sequential ordering.

---

## Pain Points

### P1: Single Mode — Full Ceremony or Nothing (Severity: High)

Taskmaster has one operating mode. Every task gets the same treatment regardless of whether scope is settled or exploratory. The pick → lock → worktree → implement → review-gate → end-session cycle takes ~2.5 minutes of overhead per task. During exploration, this overhead is paid repeatedly for work that may be thrown away.

The only alternative is turning Taskmaster off entirely, losing session continuity — the main value proposition.

**Evidence:** User concluded the session by asking to "skip the formal task ceremony for this phase" — effectively disabling Taskmaster.

### P2: No Target Verification (Severity: High)

Tasks don't declare what files, directories, or systems they touch. The AI picked a task, created a worktree, and implemented changes against the wrong codebase. Nothing in the system flagged the mismatch. Hours of work were wasted.

**Evidence:** Full implementation of 7 tasks (dashboard-redesign-010 through 016) completed on `predictions.html` when the actual target was `src/observatory/` served at `localhost:5173`.

### P3: Epic vs Milestone Conflation (Severity: Medium)

AI models see "milestone" and think "feature goal" rather than "temporal phase." They create `milestone: auth-system` instead of `milestone: phase-1-foundation`. The naming doesn't communicate the sequential/attention-filtering purpose.

The intended purposes of milestones are:
1. **Attention filter** — hide later-phase tasks from the "next available" queue to reduce cognitive load for the human reviewing the board
2. **Coarse-grained dependency grouping** — "all of Phase 2 depends on Phase 1" without wiring individual task-to-task dependencies for every combination
3. **Sequential feature ordering** — build features one by one because a big subset of tasks relies on another big subset being finished first

**Evidence:** External feedback from a colleague: "Epics and Milestones are confusing and AI would tend to create milestones per features, not for the time axis."

### P4: Granularity Explosion (Severity: Medium)

AI decomposes work into too many tasks. 25 tasks for one feature is a project plan, not a backlog. For a solo developer, managing each task (pick, complete, log) exceeds the tracking value. Tasks should represent "things I might pick up in different sessions," not "steps within one session."

**Evidence:** 25 `observatory-react-*` tasks for a single feature redesign.

### P5: Stale Task Accumulation (Severity: Medium)

When direction shifts, `todo` tasks become stale but look valid. Nobody cleans them up because cleanup is itself overhead. The backlog slowly drifts from reality.

**Evidence:** Three layers of stale/misdirected tasks accumulated for the predictions page work.

### P6: "All Work Through Tasks" Is Too Rigid (Severity: Low)

Some work is unplanned ("fix this typo"), exploratory ("try a different approach"), or cross-cutting (touches three epics, doesn't belong to any task). Forcing it through the task system adds overhead without tracking value.

---

## Design Changes

### 1. Spike Work Type

**Problem:** No way to track exploratory work without full ceremony.

**Solution:** Add a `type` field to backlog items. Alongside the existing `task` type, introduce `spike` — a lightweight exploration item that provides session continuity without execution overhead.

**Spike behavior:**
- Named in the backlog, visible on the board
- **Has a worktree** — spikes are exploratory but still potentially breaking; isolation protects the main branch
- Has a branch and worktree path recorded
- **No review gate** — exploration doesn't need formal quality checks
- **No structured session ceremony** — no Done/Decisions/Issues template, uses auto-summary instead
- **No dependency tracking** — spikes are inherently unplanned
- **No lock enforcement** — lightweight, can be worked on across sessions without force-reclaim
- `start-session` shows: "Last time you were exploring: {spike title}"
- Can be promoted to real tasks when scope crystallizes

**Schema addition:**

```yaml
- id: "dash-spike-001"
  title: "Figure out how predictions page should work"
  type: "spike"           # "task" (default) | "spike"
  status: "in-progress"
  epic: "dashboard"
  branch: "spike/dash-spike-001"
  worktree: ".worktrees/dash-spike-001"
  created: "2026-03-21T10:00"
  notes: "Exploring React Observatory architecture"
```

**New MCP tools:**
- `backlog_add_spike(title, epic, notes?)` — creates a spike with auto-generated ID `{epic}-spike-{NNN}`
- `backlog_promote_spike(spike_id)` — completes the spike and prompts creation of real tasks from findings

**Skill changes:**
- `pick-task` — when picking a spike, creates worktree but skips lock, deps check, and doc reading
- `end-session` — when current work is a spike, auto-logs from git diff instead of structured template
- `start-session` — shows active spikes prominently: "Currently exploring: ..."
- `taskmaster` router — recognizes "explore", "spike", "figure out", "try" as spike-triggering intent

### 2. Rename Milestone → Phase

**Problem:** "Milestone" sounds like a feature goal. AI creates feature-milestones instead of temporal phases.

**Solution:** Rename throughout the system. The word "phase" naturally communicates sequential ordering and temporal scope.

**Changes:**
- Schema: `milestones:` → `phases:`
- All MCP tools renamed:
  - `backlog_add_milestone` → `backlog_add_phase`
  - `backlog_update_milestone` → `backlog_update_phase`
  - `backlog_milestone_status` → `backlog_phase_status`
  - `backlog_advance_milestone` → `backlog_advance_phase`
- Skills updated: all references to "milestone" become "phase"
- Viewer updated: milestone column/filter becomes phase
- `context.active_milestone` → `context.active_phase`

**Init skill reframing:**

Current: "Define your project milestones"
Proposed: "Do you want to separate now-work from later-work? Phases let you say 'build all of this first, then move on to that.' Example: Phase 1 is foundation work, Phase 2 is polish."

**Stronger temporal grounding:**
- Tool descriptions explicitly state: "Phases are temporal attention scopes for sequential ordering, NOT feature groupings — features belong in epics"
- Consider making `target_date` required (or strongly encouraged) to anchor phases in time
- Init skill asks "what needs to be built first before you can do the rest?" not "define your phases"

**Schema change:**

```yaml
phases:                             # was: milestones
  - id: "foundation"
    name: "Phase 1: Foundation"
    status: "active"
    order: 1
    target_date: "2026-04-01"      # strongly encouraged
    start_date: "2026-03-01"
    description: "Core infrastructure before feature work"
```

### 3. Task Anchors

**Problem:** Tasks don't declare what they touch. AI can implement against the wrong target without any system-level warning.

**Solution:** Optional `anchors` field on tasks — a list of glob patterns and/or URLs that declare the expected target files/systems.

**Schema addition:**

```yaml
- id: "dash-009"
  title: "Fix predictions table sorting"
  anchors:
    - "src/observatory/**"
    - "localhost:5173"
```

**Behavior:**
- `pick-task` displays anchors prominently: "This task is anchored to `src/observatory/`. Expected at `localhost:5173`."
- If the AI starts editing files outside anchor paths, the system warns: "You're editing `predictions.html` but this task is anchored to `src/observatory/` — is that right?"
- Anchors are optional — not all tasks have clear file targets
- Glob patterns for files, plain strings for URLs/systems

**Implementation approach:**
- Anchor check happens at pick-task time (display) and could be enforced via a hook that checks edited file paths against task anchors
- Alternatively, the pick-task skill simply includes the anchors in its context output, relying on the AI to respect them

### 4. Task Budget per Epic

**Problem:** AI creates too many tasks, turning the backlog into a project plan instead of a focused work queue.

**Solution:** Soft cap of 5-8 non-archived tasks per active epic. Warning, not a hard block.

**Behavior:**
- `backlog_add_task` checks active (non-archived) task count for the epic
- If count exceeds 8: return a warning in the tool response: "This epic has {N} active tasks. Consider grouping related work into fewer, coarser tasks — or link a plan document (docs.plan) for detailed steps."
- The task is still created — this is friction, not enforcement
- The init skill and task-creation guidance should reinforce: "Tasks should be things you might pick up in different sessions, not steps within one session"

### 5. Staleness Decay

**Problem:** Todo tasks go stale when direction shifts but nobody cleans them up.

**Solution:** Track when tasks were last referenced. Flag stale tasks during `start-session`.

**Schema addition:**

```yaml
- id: "auth-007"
  last_referenced: "2026-03-15T10:00"  # updated by any tool that touches this task
```

**Behavior:**
- Any MCP tool that reads or modifies a task updates `last_referenced`
- During `start-session`, if a `todo` task hasn't been referenced in 14+ days (or 5+ sessions, whichever is trackable), flag it:

  ```
  Stale tasks (not referenced in 14+ days):
    auth-007  Add SAML support          — stale since Mar 7
    api-012   GraphQL migration          — stale since Mar 1
  Still relevant? Can archive individually or sweep all.
  ```

- User confirms (resets `last_referenced`) or archives
- Prevents slow accumulation of zombie tasks

### 6. Auto-Summary for Lightweight Sessions

**Problem:** The end-session ceremony (Done/Decisions/Issues template, user review, approval) is heavy for sessions where you just explored something or made one small fix.

**Solution:** Two summary modes — auto (default for spikes and light sessions) and structured (for execution tasks).

**Auto-summary format:**

```markdown
### 2026-03-21 — auto
Files changed: 4 | +127 -43
Commits: "fix predictions sorting", "add filter dropdown"
Tasks touched: dash-009
```

**Structured summary format (unchanged):**

```markdown
### 2026-03-21 — Auth: Refresh Token Setup
**Done:**
- Implemented JWT refresh endpoint
- Added token rotation logic

**Decisions:**
- Store refresh tokens in Redis with 7-day TTL

**Issues:**
- None

**Tasks touched:** auth-003
```

**Behavior:**
- Spikes always use auto-summary
- Tasks default to structured but can fall back to auto if the session was light (e.g., only 1-2 commits, single file changed)
- User can always request the full structured format regardless of mode
- The changelog becomes a mix of auto and structured entries — both are valid

### 7. Observe Untracked Work

**Problem:** Forcing all work through tasks is too rigid. Some work is ad-hoc and doesn't need tracking.

**Solution:** `start-session` observes git history since last session and reports untracked commits — informational, not judgmental.

**Behavior:**
- During `start-session`, compare `git log` since last session timestamp against tasks that were `in-progress`
- Report commits that don't map to any tracked task's branch:

  ```
  Last session: 4 commits outside tracked tasks
    - fix typo in README
    - bump dependencies
    - hotfix deploy script
    - experiment with new layout
  ```

- No judgment, no "you should have created a task"
- The backlog tracks planned work; git tracks actual work. They don't have to match.

---

## Updated Data Model

Full schema with all proposed changes:

```yaml
meta:
  project: "My Project"
  updated: "2026-03-21"

context:                              # auto-regenerated on every save
  active_epic: "auth"
  active_phase: {id, name, stats, target_date, start_date}  # was: active_milestone
  in_progress: [{id, title, type, epic, branch, locked_by}]  # type added
  exploring: [{id, title, epic}]      # new: active spikes
  blocked: [{id, title, epic, blockers}]
  recent_completed: [{id, title, completed}]
  next_up: [{id, title, priority, epic}]
  stale: [{id, title, last_referenced}]  # new: flagged stale tasks
  untracked_commits: [...]            # new: commits outside tasks since last session
  stats: {total, done, in_progress, in_review, todo, blocked, archived, spikes}

epics:
  - id: "auth"
    name: "Authentication System"
    status: "active"
    description: "..."
    max_tasks: 8                      # new: optional per-epic override of budget
    tasks:
      - id: "auth-003"
        type: "task"                  # new: "task" (default) | "spike"
        title: "Implement refresh token rotation"
        status: "in-progress"
        priority: "P1"
        phase: "foundation"           # was: milestone
        anchors:                      # new: target file/system declarations
          - "src/auth/**"
          - "localhost:3000/api/auth"
        last_referenced: "2026-03-21" # new: staleness tracking
        # ... all existing fields unchanged

      - id: "auth-spike-001"         # new: spike example
        type: "spike"
        title: "Explore OAuth2 PKCE flow options"
        status: "in-progress"
        branch: "spike/auth-spike-001"
        worktree: ".worktrees/auth-spike-001"
        created: "2026-03-21T10:00"
        notes: "Comparing auth0 vs keycloak vs custom"

phases:                               # was: milestones
  - id: "foundation"
    name: "Phase 1: Foundation"
    status: "active"
    order: 1
    target_date: "2026-04-01"
    start_date: "2026-03-01"
    description: "Core infrastructure and auth before feature work"
  - id: "polish"
    name: "Phase 2: Polish"
    status: "planned"
    order: 2
    target_date: "2026-05-01"
    description: "UI refinement, performance, edge cases"
```

---

## Updated Hierarchy

```
Epic (WHAT — thematic grouping)
  └── Phase (WHEN — sequential ordering, attention filter)
       └── Task (execution — full lifecycle, worktree, review gate)
       └── Spike (exploration — worktree, auto-log, promote when ready)

Untracked work (observed by start-session, not in backlog)
```

**Epic** = thematic axis. What you're building. Owns tasks and spikes.
**Phase** = temporal axis. Sequential ordering + attention filtering. "All of this before all of that." Replaces milestone with clearer naming.
**Task** = execution work unit. Defined scope, full lifecycle, worktree isolation, review gate, dependency tracking, anchors.
**Spike** = exploration work unit. Unknown scope, worktree isolation, auto-logging, no review gate, no dependencies, promotable to tasks.
**Untracked** = ad-hoc work. Not in the backlog. Git commits observed and reported during start-session.

---

## Migration Path

For existing projects using Taskmaster v1:

1. **Schema migration:** Automated tool `backlog_migrate_v2` that:
   - Renames `milestones:` → `phases:` and `milestone:` → `phase:` on tasks
   - Renames `context.active_milestone` → `context.active_phase`
   - Adds `type: "task"` to all existing tasks (default, no behavior change)
   - Adds `last_referenced: {current_date}` to all tasks
   - Preserves all other fields

2. **Tool aliases:** Keep old tool names as aliases for one major version:
   - `backlog_add_milestone` → routes to `backlog_add_phase`
   - `backlog_milestone_status` → routes to `backlog_phase_status`
   - `backlog_advance_milestone` → routes to `backlog_advance_phase`

3. **Skill updates:** All skills updated to use new terminology. No aliases needed since skills are loaded fresh each session.

---

## Priority & Sequencing

| # | Change | Impact | Effort | Rationale |
|---|--------|--------|--------|-----------|
| 1 | Spike work type | High | Medium | Addresses the biggest pain: ceremony during exploration. Enables the rest. |
| 2 | Task anchors | High | Medium | Prevents the most expensive failure mode (wrong-target implementation). |
| 3 | Rename → Phase | Medium | Low | Naming fix that prevents ongoing AI confusion. Low effort, do alongside #1. |
| 4 | Auto-summary | Medium | Low | Natural companion to spikes — reduces end-session friction. |
| 5 | Task budget | Medium | Low | Soft cap prevents granularity explosion. Simple warning in add_task. |
| 6 | Staleness decay | Medium | Medium | Self-cleaning backlog. Good but not urgent. |
| 7 | Observe untracked | Medium | Low | Informational improvement to start-session. Do last. |

**Suggested implementation order:** 1 + 3 together (spike type requires schema changes anyway, rename phases at same time), then 2, then 4 + 6, then 5 + 7.

---

## Open Questions

1. **Spike worktree branch naming:** `spike/{id}` or `feature/{id}`? Separate prefix makes spikes visually distinct in `git branch` output. Leaning toward `spike/` prefix.

2. **Phase target_date — required or encouraged?** Making it required strengthens the temporal grounding but adds friction when you genuinely don't know when a phase ends. Current lean: strongly encouraged, with a warning if omitted but not a hard requirement.

3. **Anchor enforcement level:** Should anchor violations be warnings (AI sees and can override) or hook-enforced blocks? Warnings are lighter and sufficient if the AI respects them. Hooks are heavier but catch the case where the AI ignores the warning. Start with warnings, escalate to hooks if needed.

4. **Task budget number:** 5-8 is a rough range. Should the cap be configurable per epic (`max_tasks` field) or a global setting? Per-epic is more flexible. Global is simpler. Could default to 8 with per-epic override.

5. **Staleness threshold:** 14 days? 5 sessions? Session count is more meaningful but harder to track (requires counting sessions in PROGRESS.md). Calendar days is simpler. Could use `last_referenced` timestamp and a configurable day threshold.

6. **Spike → Task promotion UX:** When promoting a spike, should it auto-create tasks from the spike's notes? Or just mark the spike done and prompt the user to create tasks manually? Manual is simpler and avoids AI hallucinating task breakdowns. Could offer both.

---

## Visual Reference

An interactive visual exploration of these concepts is available at:
`plugins/taskmaster/docs/taskmaster-redesign.html`

Open in a browser to explore:
- Pain points with severity ranking
- Ceremony cost comparison (current vs proposed flows)
- Hierarchy redesign side-by-side
- Work modes feature matrix
- All ideas with expandable implementation details
