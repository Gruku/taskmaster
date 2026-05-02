# Taskmaster v3 — Agent teams integration (proposal)

> Status: Draft proposal for v3 sibling
> Date: 2026-05-02
> Origin: First real use of Claude Code's experimental agent teams feature on the `astrolabes` `expeditions-003` design spec. The protocol that worked is documented in `~/.claude/projects/C--Users-gruku-Files-Claude-game-of-many/memory/agent_teams.md`. This doc proposes how to fold that capability into v3 *without* making teams the default.

This is a sibling to `design-v3-narrative-continuity.md`, not an edit. The five v3 additions (folder layout, handovers, lessons, issues, recap, auto-mode) are independent of this proposal — agent teams plug in as an *additional capability layer* rather than a sixth pillar. Adopt independently or skip entirely.

---

## Table of Contents

- [Background and Cost Model](#background-and-cost-model)
- [Where Teams Pay Off in v3](#where-teams-pay-off-in-v3)
- [What NOT to Do](#what-not-to-do)
- [Proposed Skills](#proposed-skills)
- [Built-in Protocols](#built-in-protocols)
- [Subagent Definitions as Team Roles](#subagent-definitions-as-team-roles)
- [Token Budget Discipline](#token-budget-discipline)
- [Compatibility and Detection](#compatibility-and-detection)
- [Build Order](#build-order)
- [Open Questions](#open-questions)

---

## Background and Cost Model

Agent teams (Claude Code v2.1.32+, experimental, `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1`) let one *lead* session spawn multiple *teammates*, each with its own context, sharing a task list and communicating via `SendMessage`. Token cost scales linearly with teammate count — a 4-teammate team costs ~5× a single Claude session for the same wall-clock work.

Empirical evidence from one real use (`expeditions-003` design spec, 4 teammates, position-paper → cross-rebuttal → synthesis):

- Spec quality measurably higher than a solo Opus pass would have produced. Specifically: caught a frame-level error in the original brief, found an internal contradiction inside one teammate's own paper, surfaced an exploit no single lens would have considered, forced explicit resolution of an irreconcilable engineer/QA disagreement.
- Cost: ~5× the tokens. The synthesis is slower, not faster.
- Net: worth the cost on this kind of work; would have been pure waste on CRUD.

**The cost-justification test:** *can the lead name what a solo Opus would have missed?* If yes, form a team. If no, don't.

This test is the load-bearing piece of any v3 integration. Without it, teams become a default and the token bill explodes.

## Where Teams Pay Off in v3

v3 already has a clear notion of *task complexity* via spec-review and the auto-mode state machine. Teams plug into specific stages where multi-lens evaluation produces materially better output:

### 1. `spec-review --team` (high-value)

The existing `taskmaster:spec-review` skill is single-agent adversarial review — one Opus reviewer challenges a proposed plan or spec. For high-stakes / high-uncertainty tasks (mark with `priority: critical` or `complexity: high` in `tasks/<id>.md` frontmatter), a team review with 3–4 lenses (engineer / QA / skeptic / domain-expert) produces a substantively different review than a single reviewer.

**Trigger:** user says "team review", or `spec-review` detects `priority: critical` *and* the task is type `feature` (not `bugfix` or `chore`) and offers to escalate. Default remains single-agent.

### 2. `taskmaster:design-team` (new skill)

The gap closed by this experiment is *pre-spec design*. A task in `todo` with high uncertainty needs a design pass before its plan is written. v2 has no skill for this — closest is `spec-review`, which assumes a spec already exists.

**Trigger:** user explicitly asks ("design this with a team"), OR `pick-task` detects high-uncertainty signals (no spec yet + estimate=L + no obvious prior pattern in lessons) and offers it as an option.

**Output:** a new spec at `tasks/<id>.md` Spec/Plan section, populated from the team's synthesis. The position papers and rebuttals are saved as supporting artifacts (see §Token Budget) and referenced from the task's `related_handovers` or a new `team_artifacts` field.

### 3. `taskmaster:debug-team` (new skill)

For ambiguous bugs (issues with `severity: P0` or `P1` and no clear repro) — the canonical "competing hypotheses" use case Claude's docs explicitly call out. Spawn 3–5 teammates, each pursuing a different hypothesis, debate, converge.

**Trigger:** user says "debug team", OR `taskmaster:issue` on a P0 issue offers it as an option. Default to single-agent investigation; team only when the user opts in or `issue` heuristics fire.

### 4. Cross-cutting refactors as team work

Auto-epic currently runs tasks sequentially with subagent isolation. For *cross-cutting* refactors — same change touching auth + api + ui — a team with one teammate per domain plus a coordinating lead is a better fit than serial subagent dispatch. Teammates share the task list, claim work, coordinate file boundaries via direct messages, avoid stomping on each other.

This requires the cross-cutting refactor to be *modeled* in v3 — currently each task has one branch, one worktree, one author. A "team task" would have a parent task with sub-tasks per domain, all synchronized to one worktree (or coordinated worktrees with merge protocol).

This is the biggest design surface and probably the most valuable v3 addition if shipped, but also the most expensive to build correctly. **Defer to v3.5 or later.** §1–§3 above are achievable in v3.

## What NOT to Do

Lessons from the experiment that should explicitly *not* land in v3:

- **Do not make teams the default for any existing skill.** `pick-task` should not auto-form a team. `spec-review` should not auto-escalate. `auto-epic` should not run as a team. Teams are an *opt-in* capability layer.
- **Do not let auto-mode form teams.** Auto-mode runs subagents because they're cheap and fast. A team inside auto-mode would 5× the auto run's cost while breaking the orchestrator's per-task summary discipline.
- **Do not author one-off prompts every time.** Teammate role prompts must be *durable* — see §Subagent Definitions as Team Roles. Otherwise every team formation is a 200-line spawn-prompt-writing exercise, which kills the cost/value math.
- **Do not skip the cost-justification test.** Every team formation should require either explicit user intent ("design this with a team") or a heuristic match (priority + estimate + missing-spec signals). If the skill doesn't gatekeep, users will form teams reflexively and the token bills will compound.

## Proposed Skills

### `taskmaster:design-team`

```
Inputs:
  - task_id (required) — the task to design
  - protocol (optional, default: position-paper-rebuttal-synthesis)
  - team_size (optional, default: 4) — capped at 5
  - role_set (optional) — names of teammate roles to use
                          (default: pick from project's `team-roles/` config)

Flow:
  1. Cost-justification gate. Show:
     - Task ID + title + estimate
     - Estimated team token cost (~5× solo)
     - "What would a solo Opus miss?" — user must answer in one line.
  2. Lead writes brief at .taskmaster/teams/<task-id>/brief.md
     - Self-contained context, references task body + lessons + related issues.
     - Lists 3-5 questions the team must take a position on.
  3. TeamCreate `<task-id>-design`. Spawn N teammates with role-specific prompts.
  4. Round 1: each teammate writes position paper to .taskmaster/teams/<task-id>/positions/<role>.md
  5. Round 2: lead reads all papers, identifies friction points, dispatches
     rebuttal prompts that cite specific peer claims.
  6. Round 3: lead synthesizes into tasks/<id>.md Spec/Plan section.
     - Open questions that didn't resolve land in spec verbatim, not papered over.
     - [LEAD CALL] inline tags where lead overruled team consensus.
  7. Cleanup: TeamDelete after spec is written.
  8. Output: spec written, team artifacts archived under .taskmaster/teams/<task-id>/.

Token footprint:
  - Lead context: ~3-5k for the brief + papers + synthesis (papers loaded once).
  - Teammate contexts: 4× ~2k = 8k spawn cost, plus per-teammate work.
  - Total: ~15-25k for a full design pass.
```

### `taskmaster:debug-team`

```
Inputs:
  - issue_id (required) — issue to investigate
  - team_size (optional, default: 4)

Flow:
  1. Cost-justification gate.
  2. Lead writes a short investigation brief: known repro, known facts, current
     hypotheses if any.
  3. TeamCreate `<issue-id>-debug`. Spawn teammates each with one hypothesis.
  4. Rounds:
     - Round 1: each teammate independently explores their hypothesis,
       writes findings.
     - Round 2: cross-debate via SendMessage — teammates challenge each other's
       findings.
     - Round 3: lead synthesizes a root-cause writeup, updates ISS-* with
       resolution.
  5. Cleanup.

Token footprint similar to design-team.
```

### `spec-review --team` (extension to existing skill)

```
New flag: --team [size]

When set:
  - Forms a 3-4 teammate review team (engineer / QA / skeptic by default,
    plus domain-expert if task has tags matching a configured role).
  - Each teammate produces a review note in parallel.
  - Lead synthesizes review.md combining their findings.
  - No rebuttal round — review is single-pass.

When not set: existing single-agent flow.
```

## Built-in Protocols

The position-paper → rebuttal → synthesis protocol is one shape, not the only shape. The skill should ship with named protocols and let advanced users select:

### `position-paper-rebuttal-synthesis` (default for design-team)
- Round 1: independent papers (300-500 words each)
- Round 2: cross-rebuttals (~150 words each, friction-seeded by lead)
- Round 3: lead synthesizes
- **Best for:** decision-heavy specs with multiple valid answers.

### `hypothesis-tournament` (default for debug-team)
- Round 1: independent hypothesis investigations
- Round 2: each teammate must try to *disprove* each other's hypothesis
- Round 3: surviving hypothesis → root cause; lead synthesizes
- **Best for:** ambiguous bugs, root-cause analysis.

### `pair-then-merge`
- Phase 1: teammates pair off, each pair produces a draft
- Phase 2: pairs review each other's drafts
- Phase 3: lead picks one draft to ship, or synthesizes both
- **Best for:** refactor planning where two independent designs are useful baselines.

### `parallel-review` (used by `spec-review --team`)
- Single pass — each teammate reviews from their lens, lead synthesizes
- No rebuttals
- **Best for:** time-bounded reviews, PR review, cheaper than full design.

### `open-debate`
- No structured rounds; teammates DM each other ad-hoc
- Lead intervenes when consensus seems near or stuck
- **Best for:** open-ended brainstorms, creative ideation
- **Caveat:** highest token risk; gate behind explicit user intent.

Each protocol is implemented as a small orchestrator function in the skill — it knows the round structure and delegates spawn/dispatch/sync to the team primitives.

## Subagent Definitions as Team Roles

The doc explicitly notes: a teammate spawned with a `subagent_type` referencing a project/user-scope subagent definition inherits the definition's `tools` allowlist, `model`, and the body becomes additional system prompt. (Caveat: `skills` and `mcpServers` frontmatter is *not* applied — teammates load skills/MCP from their session settings, not the definition.)

This means project-specific team roles can be authored once at `.claude/agents/<role>.md` (or under taskmaster's project config at `.taskmaster/team-roles/`) and reused across both subagent delegation and team formation. Authoring path:

```
.taskmaster/team-roles/
├── designer.md          # game/UX designer role
├── engineer.md           # software engineer / architect role
├── qa.md                 # adversarial QA / playtester role
├── skeptic.md            # devil's advocate role (must cite prior art)
├── security.md           # security review role
└── frontend.md           # frontend specialist (uses frontend-design skill)
```

Each file:
```yaml
---
name: skeptic
model: opus            # or sonnet
description: Devil's advocate. Challenges assumptions in the brief itself, not just refines the design. Required to cite ≥2 prior projects/games and what they did right or wrong.
tools: ["*"]            # or restricted set
---

(Body becomes additional system prompt.)

You are a devil's advocate. Your role:
- Challenge assumptions baked into the *brief*, not just the design.
- Cite at least two prior projects, games, or systems and what they did
  right or wrong.
- Required to disagree with the brief, not just refine it.
- Don't be performatively contrarian; pick attacks that hold up.
```

The skill loads these definitions, picks N for a given team based on the role-set spec, and uses them as the `subagent_type` parameter when spawning. Users can override per-team or hand-author one-off teammates if needed.

**Default role-set selection:**
- `design-team` → designer + engineer + qa + skeptic
- `spec-review --team` → engineer + qa + skeptic
- `debug-team` → 3-5 hypothesis-named teammates (built dynamically from the issue's investigation brief)

Project authors can override defaults in `.taskmaster/config.yaml`:

```yaml
team_roles:
  default_design_set: [game-designer, backend-engineer, qa, skeptic]
  default_review_set: [engineer, qa, security]
```

## Token Budget Discipline

Teams break the v3 token-budget contract if not gated. The skill should enforce:

| Surface | Soft target | Notes |
|---|---|---|
| Lead's brief | ≤2k | Self-contained, references rather than embeds context |
| Per-teammate spawn prompt | ≤500 | Role + brief reference + protocol-step instructions |
| Per-teammate position paper | ≤500 words | Hard ceiling; over-runs trigger recut request |
| Per-teammate rebuttal | ≤150 words | Hard ceiling; tighter formats actually hold |
| Lead synthesis (final spec) | ≤3k | Concise; open questions go in their own section |
| Total team artifacts on disk | uncapped | Lives in `.taskmaster/teams/<task-id>/`, gitignored or tracked at user choice |

Empirical observation from the experiment: the 150-word rebuttal ceiling held; the 500-word position-paper ceiling did *not* hold for one teammate (designer ran 780). Designer's case was justified — evocative event examples carry rebuttal-quality value — but unjustified overrun should trigger a recut request automatically.

## Compatibility and Detection

The `CLAUDE_CODE_EXPERIMENTAL_AGENT_TEAMS=1` flag is required and disabled by default. v3 should:

1. **Detect at skill-init time.** `init-taskmaster --v3-features=teams` should check the env var and warn if missing, with a one-line snippet to add to `~/.claude/settings.json`.
2. **Fall back gracefully when teams unavailable.** If a user invokes `design-team` without the flag, fall back to `spec-review` and inform the user. Don't error.
3. **Detect Claude Code version.** Teams require v2.1.32+. Warn if user's `claude --version` is older.
4. **Detect terminal capability for split-pane.** On Windows / Windows Terminal, force `teammateMode: in-process` or warn that split-pane is unavailable. (The experiment ran on Windows in-process; the protocol works fine without panes.)
5. **No effect on v2 backlogs.** This whole feature set is opt-in. v2 users don't see any of these skills until migration to v3.

## Build Order

If folded into v3:

1. **`taskmaster:design-team` skill + position-paper-rebuttal-synthesis protocol.** Highest-value, exercises the full team primitive set, validates the integration design.
2. **`.taskmaster/team-roles/` config + role-loading.** Required for §1 to scale; without this every team formation is bespoke prompt-writing.
3. **`spec-review --team` flag + parallel-review protocol.** Smaller surface, reuses §1 infrastructure.
4. **`taskmaster:debug-team` + hypothesis-tournament protocol.** Different flow but same primitives.
5. **`pair-then-merge` and `open-debate` protocols.** Optional, ship if real demand emerges.
6. **(Deferred to v3.5+)** Cross-cutting refactor team support — requires task-decomposition modeling that doesn't exist in v3 yet.

## Open Questions

1. **Cleanup discipline.** TeamDelete fails if any teammate is still active. The skill must shut down all teammates first (`SendMessage` with `shutdown_request`) and wait. What's the failure mode if a teammate refuses shutdown? Force-cleanup escape hatch?

2. **Resume-after-compaction.** If the lead session compacts mid-team (long synthesis), do teammates survive? The doc says "no session resumption with in-process teammates" — `/resume` doesn't restore them. Skill should write enough state to disk that a fresh lead can pick up from round 2 (papers exist on disk; lead can re-spawn rebuttal teammates if needed).

3. **Cost transparency.** Do we surface live token estimates per teammate during the run, or just a final summary? Live is more honest; final keeps the lead's UI cleaner.

4. **User-as-teammate.** A real user wearing one of the lenses (e.g. "I'll be the QA, you're the designer") changes everything — fewer agents, more user input. Worth designing for in v4 or skipping?

5. **Cross-project role library.** `team-roles/` is per-project. Should there be a user-scope library at `~/.claude/team-roles/` for portable role definitions (like the user's "skeptic" archetype)? Probably yes; mirrors existing CLAUDE.md global/project split.

6. **Persistence vs ephemerality.** Should team artifacts (papers, rebuttals) persist after the spec lands, or be archived/deleted? Kept = audit trail and learning data for lessons. Deleted = leaner repo. Default proposal: keep, gitignore by default, opt-in to commit.

7. **Lessons capture from team runs.** A team run that catches a non-obvious issue (e.g. "QA flagged auto-resolve feed exploit no other lens saw") is a lesson candidate. Should the skill prompt to extract a lesson at synthesis time? Promising but adds friction; defer to a follow-up iteration.

---

## Appendix: Concrete artifact from the experiment

Reference implementation (informal) lives at `C:/Users/gruku/Files/Claude/game-of-many/`:
- Brief: `docs/superpowers/specs/2026-05-02-expeditions-003-design-brief.md`
- Position papers: `docs/superpowers/specs/exp003-positions/{role}.md`
- Rebuttals: `docs/superpowers/specs/exp003-positions/{role}-rebuttal.md`
- Synthesized spec: `docs/superpowers/specs/2026-05-02-expeditions-003-branch-events.md`

The synthesized spec includes a §12 "What the team got that solo-Opus would have missed" — useful for future cost-justification discussions.
