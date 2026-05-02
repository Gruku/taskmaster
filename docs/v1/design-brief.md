# Taskmaster — Design Brief

## What it is

Taskmaster is a task and project-memory system that lives inside an AI coding assistant. Developers use it to plan, track, and **remember** their work across sessions, weeks, and months — without leaving the conversation where the work happens.

It's a kanban board, a worklog, and something more ambitious: **a project that gets measurably easier to work on the longer you use it.** Each session leaves behind structured artifacts — handovers, lessons, issues, recaps — that the next session reads automatically. Knowledge compounds; tokens don't.

## Who uses it

Solo developers and small teams pair-programming with an AI. Technical, terminal-native, allergic to heavyweight PM tools. They care about: focus, momentum, not losing context between sessions, and the AI not forgetting hard-won lessons.

Two pain points the design must speak to directly:
1. *"I just wrote a 300k-token handoff message at 2am and have nowhere to put it."*
2. *"The same mistake gets corrected three weeks in a row because nothing learns."*

## What it does (the jobs-to-be-done)

1. **Captures work as structured tasks** — ID, title, priority, size, epic, phase, dependencies.
2. **Groups tasks two ways** — **Epics** (themes, long-lived) and **Phases** (sprints, only one active).
3. **Moves tasks through a lifecycle** — `todo → in-progress → in-review → done → archived`. The `in-review` step is mandatory; a human must confirm.
4. **Suggests what to work on next** — considers active phase, dependencies, priority, staleness.
5. **Isolates work** — each task gets its own git branch and worktree.
6. **Logs every session** — auto-summarized: Done / Decisions / Issues / Tasks touched.
7. **Audits untracked work** — scans for `TODO`/`FIXME` and reconciles with the backlog.
8. **Runs quality gates** — code review, tests, build, spec adherence, before "done".
9. **Tracks dependencies and blockers**.
10. **Surfaces health signals** — stale tasks, overloaded epics, broken dep graphs, phase progress.
11. **Handovers** — a long session ends with a markdown artifact: tldr, next-action, decisions, blockers, "where I'd start tomorrow." Lives on disk; only the latest one's frontmatter loads at session start. Auto-offered when a session is big or a task is mid-flight.
12. **Lessons** — project-scoped, structured knowledge ("always read `auth/session.ts` before editing the login flow"). Reinforced when applied; promoted to "core" after repeated use; auto-retired if not reinforced. Three tiers of loading — digest at session start, full bodies for "core" lessons, trigger-matched bodies when picking a task that touches relevant files.
13. **Issues** — bugs as first-class entities with severity, repro, components, impact. Distinct from tasks: an issue is a *broken thing*, a task is a *unit of work*. One issue can spawn many fix attempts; one task can close many issues.
14. **Recap** — a compact diff of *the project* since you last looked, separate from "what *you* did last session." Driven by snapshot files; runs as a PreCompact hook so context survives compaction.
15. **Auto Mode** — a state machine that drives the existing skills end-to-end (pick → spec-review → tests → implement → review-gate → end). Three entry points: single task, whole epic, whole phase. Epic/phase modes dispatch fresh subagents per task so the orchestrator never accumulates per-task detail.

## The data model the UI must express

### Structural
- **Phases** (timeline, only one active, optional target date)
- **Epics** (collections under phases, status: active/planned/done)
- **Tasks** (atomic unit — links to specs, plans, handovers, lessons, issues, branches)
- **Dependencies** between tasks

### Narrative
- **Handovers** — chronological, indexed (last 30) + archived. Each has tldr, next-action, kind, linked tasks.
- **Lessons** — project knowledge base. Each has kind (pattern / anti-pattern / gotcha), tier (active / core / retired), reinforce count, triggers (file globs, title matches), examples.
- **Issues** — bug ledger. Severity (P0–P3), components, status (open / investigating / fixed / wontfix / duplicate), repro, location, related tasks.
- **Sessions & changelog** — append-only log of past work.
- **Recap** — diff of project state since last session.
- **Auto-mode state** — current cursor in a batch run, completed/pending/failed task list.

### Cross-cutting
- Tasks ↔ handovers ↔ lessons ↔ issues all link to each other. The web of links *is* the project memory.

## The core moments / flows

The design should make each of these feel obvious, low-ceremony, and rewarding:

1. **Orientation** — "I just opened my project. What changed since I left? What's blocking? What should I do?" → dashboard + recap + last session + open issues + suggested task.
2. **Picking work** — task selected → workspace prepared → relevant lessons surface → linked handovers/issues shown.
3. **In-flight awareness** — what's mine, what's locked, what's blocked, what's awaiting review.
4. **Quality gate** — "verify this before I claim victory."
5. **Wrapping up** — log what happened. If the session was big or a task is mid-flight, offer a **handover**.
6. **Capturing a lesson** — "this keeps happening" → write it down once, have it surface automatically when it's relevant again.
7. **Logging a bug** — quick capture with severity/repro, optionally spawn a fix task.
8. **Looking back** — replay session history; find when a decision was made and why.
9. **Auto-running a batch** — kick off an epic, watch tasks complete one by one, get a single epic-level handover at the end.
10. **Backlog grooming** — adding, archiving, reorganizing, advancing phases, reviewing retired lessons.

## The three lenses the UI should support

Taskmaster's data lives along three axes, and a good design treats them as **peers**, not as nav items buried under a "More" menu:

- **Structural lens** — phases / epics / tasks / dependencies. The kanban view.
- **Temporal lens** — sessions, handovers, recap, changelog. The project diary.
- **Knowledge lens** — lessons, issues, accumulated truths about this codebase.

The user should be able to ask "what do I know about auth?" and pivot from the auth epic → auth-tagged lessons → handovers that touched auth → open auth issues.

## Tone / personality

- **Disciplined but not bureaucratic.** Nudges good habits (review gates, handovers when sessions are big) without ceremony.
- **Calm and focused.** Actively narrows the surface — one active phase, suggested next task, soft caps everywhere.
- **Built for flow.** Glance, not context switch.
- **A trustworthy archive.** Handovers, lessons, issues, and changelog are append-only or reinforced — treated as a permanent, growing record. The UI should *feel* like a living archive, not a scratchpad.
- **Compounding.** Visually communicate that the project is "getting smarter" — reinforce counts, promotion to core, retired lessons, recap deltas. Progress isn't just tasks-done; it's knowledge-accrued.

## Constraints worth knowing (but not designing around)

- The data lives in plain files (a `backlog.yaml` index plus per-artifact markdown files in `.taskmaster/`). Whatever the UI looks like, it reflects that filesystem. Markdown bodies are diffable, git-trackable, human-readable.
- It's a browser-based local kanban paired with a conversational AI driver. The dual nature stays — but the visual form is fully open.
- Strict token budgets govern what loads when. The UI may show everything; the AI side is rationed (start-session ≤2k tokens, etc.). Design can be lavish; the data layer is disciplined.
- Multi-session, multi-month timelines. Six months in, a project might have 100 tasks, 50 lessons, 200 handovers, 30 issues. The UI must scale gracefully into archive territory.

## What I'd love the design team to explore

1. **The front door.** "What's going on and what should I do?" — make this feel like the obvious entry point, not a buried query. It should fold structural state, temporal recap, and knowledge cues into one calm view.
2. **Three lenses as peers.** How do structural / temporal / knowledge views relate? Tabs? Layers? A unified graph? A spatial metaphor? The wrong answer is "kanban with extra tabs."
3. **The compounding feeling.** How do you visualize a project getting smarter — reinforce counts, lesson tiers, retired knowledge, recap deltas — without it feeling gamified or noisy?
4. **Handovers as a ritual.** End-of-session handovers should feel meaningful, not like a form. How does the UI invite the user to write one when it matters, and stay out of the way when it doesn't?
5. **Lessons surfacing.** A lesson is most valuable the moment it's relevant — when picking a task that touches the right files. How does the UI make that "the lesson found you" moment feel almost magical?
6. **Issues vs tasks.** Two distinct lifecycles, deeply linked. How do you make the distinction obvious without doubling the cognitive load?
7. **Auto-mode.** Watching a batch run is a new mode — orchestrator-as-spectator. What does "10 tasks running, 3 done, 1 failed, agent on task 5" look like, calmly?
8. **The archive.** Six months of handovers and retired lessons. How do you make that feel like a library worth visiting, not a graveyard?
