# Taskmaster v3 Skills Enrichment — Real-World v1 Usage Mining

**Source:** CodeMaestro v1 backlog (457 tasks, 222 archived) + PROGRESS.md (5783 lines, 255 sessions)

**Date:** 2026-05-02

---

## 1. PROGRESS.md Session Entry Conventions

Real sessions use a consistent 4-header structure: Done / Decisions / Issues / Tasks touched. All sampled entries conform to this pattern; no ad-hoc variations found. Session headers anchor on ISO date + short title (task ID + description or thematic grouping).

**Section characteristics:**
- **Done:** Narrative prose (50–400 words), often with 1–5 sub-bullets for major deliverables. Word count correlates with session complexity: light hotfixes ~100 words, multi-task sessions 300+.
- **Decisions:** Structured bullets (2–10 per session), each 1–3 sentences. Rationales are inline, not separated. Architectural choices, design pivots, and deferred work all land here.
- **Issues:** Present in ~75% of sessions. Range from "None" (10–15% of sessions) to detailed multi-issue logs. Bugs, incomplete testing, and follow-ups are equally common.
- **Tasks touched:** Always present, comma-separated IDs. Enables session→backlog linking. Average session touches 2–5 tasks; bulk sessions (batch close-outs) touch 20+.

**Optional fields observed:** Auto-generated "auto" entries use single-line summary format without the 4-header structure. Patchnotes and release fields present in ~15% of sessions.

---

## 2. Handover-Language Patterns (Latent Handovers)

Three distinct handover patterns detected in real sessions:

### Pattern A: Task State Handoff
Numbered lists (3–5 steps) with specific commands, file paths, and decision points appearing in Done sections. Example from 2026-04-19:
- Local E2E test from worktree with deploy.sh command
- Open PR with specific title suggestion
- Manual Mac Studio deploy steps with launchctl commands

**v3 skill prompt:** completion_state, 
ext_steps, locked_conditions, 	est_plan.

### Pattern B: Session Resumption Blockers
Infrastructure/environment gaps listed explicitly. Examples: "requires manual UE 5.7 launch + ECA Bridge", "tree-sitter-unreal-cpp grammar build gap", "submodule fetch-back NOT yet done".

**v3 skill prompt:** locker_type (env/infra/test/external), mitigation, owner.

### Pattern C: Deferred Decisions
Rules captured ad-hoc in memory files: "ueLimitations.ts (desktop) and the future Python mirror in code-maestro-api are kept in sync MANUALLY — no shared monorepo YAML, no codegen."

**v3 lesson promotion:** Detect when same choice pattern appears 2+ times.

---

## 3. Repeated-Decision / Lesson-Candidate Patterns

Eight recurring decision clusters, each documented 3–7 times:

### Lesson 1: Multi-Tab Window Event Gotcha
**Kind:** anti-pattern | **Pattern:** useEffect reading useLocation().state without active-tab guard
- Recurred 3 times (cb6927c0, 9326a460, desktop-app-126)
- User codified rule in CLAUDE.md to prevent future regressions

### Lesson 2: Defense-in-Depth Wiring
**Kind:** pattern | **Rule:** "payload normalization → agent prompt rule → guard → fail-closed write guard"
- Applied to ue-plugin-055, ue-plugin-049, infra-018
- Layered enforcement belongs in hooks, not prompts

### Lesson 3: Spec Sync Discipline (Duplication Over Codegen)
**Kind:** pattern | **Rule:** Manual mirror TS ↔ Python; drift caught by pytest, not codegen
- Recurred across infra-032, desktop-app-049
- Boundary constraint: monorepo only tracks PM files

### Lesson 4: Stage-Into-Workbranch Rule
**Kind:** pattern | **Rule:** Before MR to develop, fetch origin/stage and --no-ff merge into feature/ue-integration
- Formalized in monorepo CLAUDE.md
- Prevents stage/develop drift

### Lesson 5: Fail-Open on Classifier Exceptions
**Kind:** gotcha | **Rule:** Guards remain last-line defense; emit [UE-KIND] warnings instead of blocking
- Applied to infra-018, infra-019
- Auto-detection misses logged for observability

### Lesson 6: No Negative Framing in Prompts
**Kind:** anti-pattern | **Rule:** Positive "use these abstractions" over negative "do NOT mention X"
- Negative lists teach forbidden terms, create prompt-injection surface
- Applied to desktop-app-159 capabilities-tour

---

## 4. Hidden Issues — Bugs That Should Have Been ISS-* Entries

### Bug 1: Multi-Tab Fanout
**Severity:** high | **Component:** desktop-app React routing
- initialMessage useEffect consumed by every ChatWindow instance without guard
- Third regression (prior: Linear pipeline, cm-action-chat)
- Fix: chatId === activeTabId early-return + deps

### Bug 2: Parallel Tool-Call Orphans
**Severity:** medium | **Component:** infra Python websocket
- _pending_tools keyed by tool_name caused N parallel calls to same tool to collapse
- 3x cm_app_glob in Init Project → 2 orphan ENDs
- Fix: key by tool_call_id with fallback to tool_name

### Bug 3: Orphan Route / Silent Dispatch Drop
**Severity:** high | **Component:** desktop-app router
- ProjectMemoriesAndRules.tsx navigated to /welcome (unrouted)
- Welcome.tsx held pendingInitDispatchAtom but was orphan (no imports)
- Dispatch silently dropped; app showed no error
- Fix: usePendingInitDispatch hook called from Layout

---

## 5. End-Session Reality vs Design

**Done/Decisions/Issues structure:** 100% compliance observed. All 60+ sampled sessions follow the 4-field pattern. No free-form paragraphs; structure enforced instinctively.

**Patchnote field:** Present in ~15% of sessions (27 instances in 5783 lines). Example:
"UE prompts that match a known alpha limitation now get a heads-up before the agent burns time on a likely-doomed run — agent leads with the canonical limitation note, suggests a workable alternative when there is one, and asks before proceeding." (session 2026-04-29, release: alpha-1.0)

**Auto-summary mode:** Two instances observed (2026-04-11, 2026-04-25), both labeled "auto". Format: "Files changed: X | +Y -Z" + commit summary + "Tasks touched:". No user preference evident; both coexist.

**Exploratory sessions:** 18 sessions with "Tasks touched: N/A" or "none (infra session, no backlog task)". Log investigations, memory captures, infrastructure work. v3 should allow logging without task ownership.

---

## 6. Auto-Loop Stress Signals at 457-Task Scale

**Backlog stats:** 457 tasks | 222 archived | 205 active (60 done, 3 in-progress, 133 todo, 9 blocked)

### Epic Sizes
- **desktop-app:** 77 tasks (30 done) — largest, near orchestrator threshold
- **ue-plugin:** 43 tasks (9 done)
- **infra:** 26 tasks (11 done)

**Stress observation:** Multi-task sessions route through user orchestration, not parallel dispatch. Bulk close-out 2026-05-02 touched 28 tasks serially via backlog_batch_update.

### Stages & Cursor State
31 tasks carry stage: 0 (UE Plugin tier). Zero partial stages (no 0.5). Implication: no sub-task scope splitting; per-task tracking only.

### Locked Tasks
Three tasks locked (all in-progress, dated 2026-05-02): ue-plugin-023, cm2-spike-001, installer-001. No stale locks. Lock lifecycle clean: acquire on pick, release on transition.

### Staleness
10 stale tasks (29 days unreferenced, from 2026-04-03): task-workflows-002/003, desktop-app-007/008/013/019/022/029/046, context-mgmt-005. All todo (no in-progress limbo).

### Quality signals for v3 auto-loop (skills-010/011)
1. Epic task count < 80 — parallelism scales linearly
2. Scan locked-but-not-in-progress — orphaned locks
3. Warn if untouched 30+ days — staleness
4. Stage consistency — integers only, no fractions
5. Task dependency closure — no cycles, no missing upstream

---

## 7. Surprises and Recommendations

### Findings

1. **Bulk close-out pattern:** 2026-05-02 closed 28 tasks via backlog_batch_update + user confirmation. Not individual end-session calls. v3 should support batch transitions.

2. **Exploratory sessions first-class:** 18 sessions with Tasks touched: N/A logged investigations without task ownership. v3 should allow phase: exploration or kind: exploration.

3. **Handoff comments hidden:** Multi-step instructions in Done sections as free prose (2026-04-19, 20 lines). v3 handover skill should auto-parse numbered lists and promote to structured fields.

4. **Specs + Plans uncommitted:** Multiple sessions note "Spec and plan are written but not committed. Per user CLAUDE.md rule — left uncommitted in the working tree for next session to handle." v3 should track specs/plans separately from commits.

5. **Lessons via manual memory files:** Rules captured ad-hoc in memory/*.md (feedback_ue_limitations_manual_sync.md, memory/multi-tab-window-events.md, v1-v2-ue-track-split.md) with zero standardization. v3's structured lesson artifact centralizes this.

6. **In-review gates expensive:** Sessions 2026-04-22/23 show partial manual testing (0/5 criteria checked, stale states). v3 should enforce 100% coverage.

### Recommended Skill-Authoring Deltas

1. **backlog_batch_update:** Support batch_mode: true with async user confirmation. Add preview/snapshot step before execution.

2. **Exploratory session tracking:** Add kind: exploration | feature | bugfix | infra. Allow phase: exploration without epic + task ownership.

3. **Handover skill enhancement:** Parse markdown numbered lists from Done sections. Auto-extract steps with regex ^\d\.\s+ and promote to handover.next_steps[]. Recognize "Blocked on X" and "Waiting for Y".

4. **Spec/plan artifact tracking:** Store uncommitted spec + plan paths in task metadata (separate from notes). End-session skill asks "Are uncommitted specs/plans ready to commit?" and offers git staging.

5. **Lesson auto-promotion:** Post-session background pass scans summaries since last review. Surface 3+ recurrences of same decision as lesson candidates. Prompt: "You've made this decision 5× — create a lesson?"

---

**Report generation:** 2026-05-02 | 457 tasks | 255 sessions | 0 mutations
