<!-- User intent: bring every taskmaster-mods surface up to the bar set by CodeMaestro's cm-restore (chips, badges, stepped cards, fixed slots), add a Taskmaster-tuned restore pane and a Session tab, give all panes a real two-way phase selector, and make the band small but unmistakably not transcript text — all in the Reality Reprojection design system. -->

# Taskmaster mods UX overhaul — Design Spec

**Date:** 2026-10-11
**Status:** Design approved in conversation 2026-10-11; written spec awaiting user review
**Scope:** `mods/taskmaster-mods` (band, review, handovers, new session and restore panes, kit, scanner) and `mods/rr-tui` (static recipes only). No Taskmaster server change.
**Builds on:** `docs/specs/2026-10-05-tui-mods-design.md` (its §3 platform facts and §6.2 action safety still hold).
**Reference implementation:** `C:\Users\gruku\Files\Work\CodeMaestro\.claude\skills\cm-restore\` (CodeMaestro work repo — borrow patterns, never its design system).

---

## 1. Problem

1. **The band blends into the transcript.** Today it is loose lines on the transcript's ground: the handover notice (blue text, full path, "Resume:" line) runs straight into the `TASK` line and reads like the end of Claude's reply; `1: review` reads as text, not a key.
2. **The phase filter goes one way.** `f` cycles all → active → other phases with waiting tasks. Reaching the previous phase takes N-1 presses, empty and done phases can't be picked, and every change throws away the review cursor.
3. **Handovers have no phases**, and paging is blind `n`/`p` over a 30-item window.
4. **Review is a one-card stepper** with no overview, no back, and no bulk action.
5. **No restore flow.** After a burst of sessions closes, nothing in Taskmaster shows which were unfinished or reopens them. cm-restore does this well for CodeMaestro but reads handover `.md` files and guesses task ids with a regex.

## 2. Goals and non-goals

### Goals

1. One visual language across band and panes: own ground, `◆ TASKMASTER` title, RR keycaps for every action, tinted badges for every status, stepped surfaces for cards, fixed control slots that dim instead of vanish.
2. A small band (≤ 2 lines) that is never mistaken for transcript text, plus a full **Session** tab.
3. A shared, two-way, fully controllable **phase strip** in review, handovers and restore.
4. Review as a **paged card list** with bulk done / to-agent.
5. Handovers with phases, a detail card, fresh-session launch, and bulk close.
6. A **restore** pane: cm-restore's core, with status, tasks and phases from tm.

### Non-goals

- No Taskmaster server, schema or skill change. Everything goes through existing `plugin:taskmaster:tm` tools (`backlog_query` for joins).
- No cross-project restore (this project's transcripts only).
- No undo for done/close (the y/n confirm is the guard).
- No change to the handover guard (`handover-guard.ts`), which a parallel session owns.

## 3. Decisions taken in brainstorming

| # | Decision | Chosen |
|---|----------|--------|
| D1 | Where restore lives | Pane in taskmaster-mods (`tm-restore`), not a separate plugin |
| D2 | Phase selector | Tab strip with full controls (§5) |
| D3 | Review layout | Paged card list, focused card expands, bulk bar |
| D4 | Restore + tm | Task + phase on cards; fresh-from-handover resume; status from tm; band keycap instead of toast |
| D5 | Band handover | Best of: written here > resumed from > newest open for bound task |
| D6 | Kit placement | Split: rr-tui keeps static recipes; `hooks/kit.tsx` owns interactive primitives |
| D7 | Band size | Two lines max with own fill; the full panel becomes the Session tab |
| D8 | Check ticking | `t` + digit (digits now belong to phases) or Enter on the check chip |

## 4. Architecture

```
rr-tui ($.rr)                       taskmaster-mods
  tokens(), surfaceProps(),           hooks/kit.tsx      interactive primitives (§4.1)
  label/signal/keycap/chip            hooks/phases.ts    tab-strip model, pure (§5)
  + badgeProps(tone), tint()          hooks/draw.tsx     band + review + handovers trees
                                      hooks/session.tsx  Session tab tree
                                      hooks/restore/     model.ts, draw.tsx, launch.ts
                                      scanner/scan.py    transcript facts (§10.1)
```

- `rr-tui` gains only static, data-returning recipes: `badgeProps({tone, strength})` (tinted fill + ink) and `tint(fg, bg, alpha)`. No interactive element crosses the noun boundary (2026-10-05 spec §3: a noun can't hand out Buttons).
- Each render fetches `rr.tokens()` once and passes the token object down. Kit functions are synchronous after that.
- New panes register like the existing two: `requestId` `tm-session`, `tm-restore`; commands `/tm-session`, `/tm-restore [days]`.

### 4.1 Kit (`hooks/kit.tsx`)

Ported in spirit from cm-restore (`chip`, `badge`, `tint`, card), restyled with RR tokens:

| Primitive | What it draws |
|-----------|---------------|
| `keycap(key, label, onPress, {tone, dim})` | Filled RR keycap glyph + plain label, one Button. Dim = present but inert; pressing a dim keycap toasts why. |
| `badge(label, tone)` | Tinted fill + bold ink (`badgeProps`). Never a left rail. |
| `panel({title, health, collapse}, children)` | Full-perimeter rounded border, raised fill across the whole box, title set into the top border in the signature tone, health signal top-right. |
| `card({selected, focused}, collapsedRow, expandedBody)` | Surface raised; selected = overlay; hover one step up (colour only, no motion). |
| `toolbar(title, slots)` | Fixed slots + spacer + page counter; inert slots dim. |
| `pager(page, pages, onTurn)` | `‹ p` / `n ›` keycaps + `n / total`. |
| `selectionBar(actions, onAll, onNone, extra)` | Primary bulk actions with counts, ghost all/none(/unfinished). |
| `rule()` | Subtle dashed separator. |
| `helpFooter(items)` | One subtle line of key hints. |

Hard rules (user preferences): no coloured left rails, no motion on hover, no box-shadows; surface stepping only.

## 5. Phase strip (shared by review, handovers, restore)

```
[‹ h]  All 31 │▐ patch-1-9 · active 12 ▌│ release-2 4 │ patch-1-8 ✓ 0 │ +3 ▾  [l ›]   [◐ done]
```

- **Tabs are real buttons** (click / Enter). `‹` `›` keycaps at both ends and `h` / `l` step both ways; they dim at the ends, no wrap. `0` = All; `1`–`9` = visible tabs in order, numbers shown on focused tabs.
- **Order:** All, active phase, planned phases by `order`, done phases newest first. Restore and handovers add a trailing **no task** tab.
- **Counts** are per pane: review = waiting on you; handovers = open (or open + closed when `◐ closed` is on); restore = sessions in scope.
- **Empty and done phases** are dimmed but selectable. An empty selection shows "Nothing waiting in <phase> · done <date>" with a `[0 All]` keycap.
- **Overflow:** tabs that don't fit collapse into `+N ▾`; pressing it (or `f`) opens an overlay list (name, status, counts, dates), ↑↓ move, Enter pick, Esc close.
- **`◐ done`** shows/hides done phases; default hidden when more than 4 done phases exist; the selected phase always stays visible.
- **Memory:** `$.store` key `phase:<pane>:<repoRoot>`. Migration: existing `phase:<repoRoot>` becomes `phase:review:<repoRoot>` on first read. A remembered phase that no longer exists falls back to All with a toast.
- **Model:** `phases.ts` is pure — `orderPhases`, `visibleTabs(width)`, `step(dir)`, `jump(n)`, `fallback` — and unit-tested without UI.
- **Data:** one `backlog_query` per pane open / refresh returns `id, name, status, order, count` per phase (row shape kept under the 80-char cell limit, as today's `phaseSql`). Handover and restore counts join through `handover_tasks` and task ids.

## 6. Band (always visible, AbovePrompt)

```
 ◆ TASKMASTER  IN PROGRESS  unified-chat-027  U5 brief approval: approveBriefVe…     [3] session ›
 ↳ HANDOVER  written here · 027 brief approval merged locally…  [c] copy    [1] 50 waiting  [2] 9  [4] 3 restore
```

- **Own ground:** both lines share a raised fill across the full width, one blank line above. `◆ TASKMASTER` is a signature-tinted chip. Badges and keycaps as §4.1.
- **Line 1:** tag, task status badge, id (subtle), title (bold, truncates first), `[3] session ›`. When the bound task is ready for done, a `[d] done` keycap appears here; otherwise `d`/`a` live in the Session tab.
- **Line 2:** the band handover (D5) with tag `written here` / `resumed from` / `task`, one-line tldr, `[c] copy`; then the counts `[1] waiting`, `[2] handovers`, `[4] restore`.
- **Collapse rules:** no handover → counts move up and the band is one line; no task → line 1 is tag + counts; narrow → title truncates, then labels shrink to key + count. Zero counts dim, never vanish. `[4]` only exists while a qualifying burst is pending (§10.4).
- **Copy** (`c`) puts the full handover text on the clipboard in the Telegram-paste format the existing copy uses; a toast confirms.
- **Faults:** tm offline shows a critical `offline` signal on line 1 and dims the counts; detail stays in the faults-only status line.
- Key map across band and panes: `1` review, `2` handovers, `3` session, `4` restore (`3` is freed from the old copy notice, which this replaces).

## 7. Session tab (`3`, `/tm-session`)

```
╭─ ◆ TASKMASTER · SESSION ──────────────────────────────────── ● tm ok ─╮
│ TASK      IN PROGRESS  unified-chat-027  U5 brief approval: approve…   │
│           full · phase-b · review-gate:pass ✓ · ready for done         │
│           human: confirm Brief card copy          [d] done  [a] agent  │
│ ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ │
│ HANDOVER  WRITTEN HERE  H-0412 · 3h ago                                │
│           027 brief approval merged locally (desktop 5d82384d4, no…    │
│           Next: run end-session to close 027, then Phase B of 009      │
│           [c] copy  [h] in handovers  [w] new tab  [i] decisions·blk  │
│ ┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄┄ │
│ SESSION   resumed from H-0398 · part of 16:14 burst (3 others open)    │
│ [1] 50 waiting on you   [2] 9 handovers   [4] 3 unfinished · restore   │
╰────────────────────────────────────────────────────────────────────────╯
```

- `panel` with sections TASK / HANDOVER / SESSION, each optional; empty sections say so in subtle text ("No task bound to this session").
- TASK: status badge, lane, phase, gates and pipeline from `backlog_task_pipeline`, `human_action`, `d` done (y/n) and `a` to agent (note input) — the existing `actions.ts` flows.
- HANDOVER: the D5 handover; `c` copy, `h` open in handovers (focused, its phase selected), `w` fresh session in a new tab (§10.3 launcher), `i` decisions + blockers.
- SESSION: "resumed from" (§10.5) and burst membership from the last restore scan.

## 8. Review pane (`1`, `/tm-review`)

```
 REVIEW                                         [‹ p]  [n ›]  1 / 4   [u]
 <phase strip>
 12 waiting on you · newest 1d ago
 2 BLOCKED  1 ACTION  9 IN REVIEW
 [Done 2]  [To agent 2]                                    all · none
 ┌ ■ BLOCKED  tm-audit-031  Sign off store migration              2d ┐
 │   human: verify prod row counts                                    │
 │   [☐ 1 rows match] [☐ 2 backup taken]                              │
 │   [d done] [a agent] [o open] [v viewer] [c copy check] [i more]   │
 └────────────────────────────────────────────────────────────────────┘
   □ ACTION   tm-audit-044  Pick cache size                         5d
 j/k move · s select · h/l phase · 0 all · 1-9 jump · p/n page · u refresh
```

- Sort: BLOCKED (critical) > ACTION (has `human_action`, warning) > IN REVIEW (info), then newest. 5 per page; phase change resets to page 1 and focuses the first card.
- Only the focused card expands (lazy `backlog_get_task` detail as today). Checks are chips; tick with `t` + digit or Enter on the chip.
- Bulk: nothing selected by default. `Done N` → y/n → `backlog_complete_task` one at a time → one toast summarising successes and naming refusals; refused cards stay. `To agent N` → one shared note → `backlog_update_task` each.
- `s` skip is removed (the list replaces the stepper).
- Loading: busy line. Offline: critical signal + `[Retry]`.

## 9. Handovers pane (`2`, `/tm-handovers`)

```
 HANDOVERS                                  [‹ p]  [n ›]  1 / 3   [◐ closed]  [u]
 <phase strip, + "no task" tab>
 9 open · newest 3h ago
 [Close 2]                                                        all · none
 ┌ □ H-0412  3h ago  migration verified, rows match          feat/x · 2 tasks ┐
 │   Next: run backlog_validate on CodeMaestro, then sign off tm-audit-031     │
 │   IN REVIEW tm-audit-031 Sign off store migration          patch-1-9        │
 │   DONE      tm-audit-029 Port row counter                  patch-1-9        │
 │   [r resume here] [w new tab] [c copy] [i decisions·blockers] [x close]     │
 └─────────────────────────────────────────────────────────────────────────────┘
```

- Phases come from linked tasks via `handover_tasks`; a handover spanning several phases counts in each; no linked task → **no task** tab.
- Focused card: full tldr, next action, each linked task with live status badge and phase. `i` decisions + blockers (`backlog_handover_get` sections, as today). `r` fills the prompt ("Resume from handover H-xx (path)"), `w` launches a fresh session in a new tab, `c` copies full text, `x` closes (y/n, `backlog_handover_update_status`).
- Bulk `Close N` with y/n. `◐ closed` adds closed handovers (dimmed); superseded stay hidden.
- Fetch up to 100; 5 per page; keys as review. Arriving from band/session `h` focuses that handover and selects its phase.

## 10. Restore pane (`4`, `/tm-restore [days]`)

```
╭─ ◆ TASKMASTER · RESTORE ─────────── [‹ p] [n ›] 1 / 3  [◐ compact] [u] ─╮
│ <phase strip, + "no task" tab>                                          │
│ 4 closed together · today 16:14–16:31 · 12m ago · 1 closed since       │
│ 1 INTERRUPTED  2 WIP  1 HANDED OFF                                      │
│ [Resume 3] [Brief 3]                              all · unfinished · none│
│ ┌ ■ HANDED OFF  U5 brief approval                         378k ctx ┐   │
│ │   closed 16:31 · master · IN PROGRESS unified-chat-027 · phase-b │   │
│ │   Next: run end-session to close 027, then Phase B of 009        │   │
│ │   via [◐ fresh from H-0412 | --resume]  [Resume] [Brief] [Copy]  │   │
│ └──────────────────────────────────────────────────────────────────┘   │
│ Running now · 2 · open in other terminals                               │
╰─────────────────────────────────────────────────────────────────────────╯
```

Kept from cm-restore: burst-first paging (page 1 = latest closing burst + anything closed after; then 5 per page newest first; within a page INTERRUPTED > WIP > HANDED OFF > DONE, then newest), default ticks (unfinished + resumable + not yet opened; explicit toggle wins), Running now section, `◐ compact`, Copy cmd, staggered launches, per-item busy flags, ctx colouring (≥ 400k warning, ≥ 700k critical).

### 10.1 Scanner (`scanner/scan.py`)

Port of cm-restore's scanner, run with `$.process.run([python, '-I', scan.py, ...])` (transcripts exceed the mod runtime's 4 MiB read limit). Scope: transcripts whose cwd is under this repo root. Cache by mtime + size in `$TEMP/tm-restore-scan-cache.json` with its own cache version. Output is **transcript facts only**: session id, cwd, branch, first prompt, last prompt, last reply, last event (interrupt or not), prompt count, ctx tokens, cost, closed time, running (pid file + liveness), `task_id`s from the session's `plugin:taskmaster:tm` tool-call arguments, handover ids from `backlog_handover_create` results, and whether end-session ran (with prompts after it or not). Removed: `.md` handover parsing, the `-NNN` task regex, the `cm-end-of-day` badge. `digest` subcommand kept for Brief.

### 10.2 Status from tm

After a scan, one `backlog_query` resolves the scanned task ids (status, lane, title, phase) and handover ids (status). Classification, first match wins: **DONE** (bound task done, or handover closed, or end-session ran with no prompts after) → **HANDED OFF** (an open handover) → **INTERRUPTED** (last event an interrupt) → **WIP**. A session's "bound task" is the last task id it touched.

### 10.3 Resume mode and launcher (`restore/launch.ts`)

- Per card, handed-off sessions get a toggle: **fresh** (new session with prompt "Resume from handover H-xx (path)") or **--resume** (`claude --resume <id>`). Default fresh when ctx > 400k, else --resume. Sessions without a handover always --resume.
- Launcher ported from cm-restore: Windows `wt.exe -w 0 nt -d <cwd> --title … pwsh -NoExit -EncodedCommand <utf16le base64>`; macOS iTerm2 / WezTerm / Terminal.app; fallback copy the command. 700 ms stagger. Shared with handovers `w` and Session `w`.

### 10.4 Band hook

`session.start` runs a background 3-day scan. If the newest burst has ≥ 2 sessions, ≥ 1 unfinished, and is < 48 h old, the band shows `[4] N restore`. `x` on the keycap hides it for that burst (`$.store` `restore-hidden:<burstEpoch>`). No toast.

### 10.5 "Resumed from"

- `--resume`: before launching, store `resumed-from:<sessionId>` = handover id; the resumed session reads it on `session.start`.
- fresh: the band's prompt hook recognises a first prompt matching `Resume from handover <id>` and records it for the session.
- The `r` key in handovers records the same way.

### 10.6 Brief

`$.model.complete` with a tag-structured prompt (state / next / blocker) over the scanner's `digest`, cached in `$.store` as `brief:<id>:<closedEpoch>`. New userConfig `briefModel`, default `claude-haiku-5-5`.

## 11. Failure handling

| Case | Behaviour |
|------|-----------|
| tm unreachable | Band: `offline` signal, counts dim. Panes: critical signal + `[Retry]`; restore still lists transcript facts with status `?` and resume works. |
| Scanner fails / no Python | Restore shows "Scan failed: <reason>" in the pane with `[u]`; band shows no `[4]`. |
| Bulk action partly refused | One toast: "3 done · 1 refused: tm-audit-031 (<server wording>)"; refused stay selected. |
| Remembered phase gone | Fall back to All + toast. |
| Launcher unavailable | Copy the command, toast "Copied — paste into a new terminal". |
| Demo mode | Every pane renders from `demo.ts`; writes are simulated as today. |

## 12. Testing

- `claude plugin test` (rr stub, `tests/fixtures/rr-stub.ts`) for: kit primitives; `phases.ts` (order, step both ways, jump, overflow, done toggle, fallback, store-key migration); band width tiers and collapse rules on both surfaces (terminal, desktop); Session sections present/absent; review sort, focus expand, `t`+digit, bulk confirm and partial refusal; handovers phase join, no-task tab, closed toggle, bulk close; restore classification, burst paging, default ticks, resume-mode default, launcher argv/quoting (ported tests), band keycap rule.
- New pytest for `scan.py` against small fixture transcripts (interrupt, end-session with/without trailing prompts, tm task/handover calls, running pid, cache hit).
- `claude plugin validate --strict` and `tsc -p` for both mods. Smoke: `env -u ANTHROPIC_API_KEY claude -p --model haiku --plugin-dir <rr-tui> --plugin-dir <taskmaster-mods> "Reply with the single word ok."`.
- Bump `DEMO_SEED`; add demo sessions, bursts, phases with handovers, and a session handover so every surface is visible without tm.

## 13. Build order (each stage ends with a live check with the user)

1. **Kit + phases:** rr-tui `badgeProps`/`tint`; `kit.tsx`; `phases.ts` + store migration; gallery entries.
2. **Band + Session tab.**
3. **Review list.**
4. **Handovers.**
5. **Restore:** scanner + tests, tm resolution, pane, launcher, band keycap, resumed-from.

Work in a worktree off `feat/database-native-foundation`; rebase onto the parallel handover-guard work rather than overwrite it. The installed desktop copy (`~/.claude/plugins/marketplaces/local-desktop-app-uploads/`) is re-synced only after the user signs off. No push without asking.

## 14. Open items (resolve during build)

1. Whether `backlog_handover_list` JSON already carries `task_ids`, or the phase join must go through `backlog_query` alone.
   - Resolved 2026-10-11 (plan Task 0.2): `task_ids` present on every entry in claude-tools and CodeMaestro; phase and no-task queries ran in both → Stage 4 reads `TmHandover.taskIds`.
2. Overlay mechanics for the `+N ▾` phase list: a second Pane render vs an in-pane absolute Box — probe before building.
   - Resolved 2026-10-11 (plan Task 0.2): the absolute overlay and the Select list both passed the probe (clean paint, Tab and press work; Select is a ↑↓ list) → defaults kept, OVERLAY_PLACEMENT = 'swap' and OVERLAY_LIST = 'buttons', because later stages' verified pane tests assume them and the buttons list keeps the 0–9 picks; flipping either is a follow-up once Stage 5 is green.
3. Whether band hotkeys `c`/`d` collide with typing in the prompt (today's band keys behave; confirm for `c`).
   - Resolved 2026-10-11 (plan Task 2.7): the user checked the Stage 2 band live and signed it off; band keys do not fire while typing in the prompt.
4. Desktop surface: confirm the panel's title-in-border renders; fall back to a title row if not.
   - Resolved 2026-10-11 (plan Task 0.2): terminal border title sits in the top border correctly; desktop not probed → PANEL_TITLE = { terminal: 'border', desktop: 'row' }.
5. Keycap face: an RR filled keycap laid over the engine's `k: ` prefix (`overlay`), the engine's accent `k:` (`accent`), or a label-less glyph Button.
   - Resolved 2026-10-11 (plan Task 0.2): variant A (overlay) reads as a filled key cap followed by a word and presses → KEYCAP_FACE = 'overlay'.
6. Hotkeys: the engine accepts one digit or one lowercase letter only (probed 2026-10-11: `[`, `/`, `R` and space fail the render), and arrows scroll a focused pane.
   - Resolved 2026-10-11: keys remapped: `[`/`]` → `h`/`l`, `/` → `f`, `R` → `u`, space → `s`, ↑↓ → `j`/`k` plus the Tab ring (plan Stage 0 key map).

## 15. Visual rework (2026-10-12)

**Status:** approved by the user 2026-10-12 (HTML prototype, round 4). **Plan:** `docs/superpowers/plans/2026-10-12-mods-visual-rework.md`.
**Why:** at the Stage 4 live check the panes read as noise: every key a filled keycap, no hierarchy, cramped lists, the Session tab clipped at the right edge.
**Prototype (the visual source of truth):** `docs/specs/assets/2026-10-12-mods-visual-rework/` — `Main.dc.html` (the band in its final form and its states, the sidebar drawn open, and the **component sheet**, the canonical element vocabulary), `Session.dc.html`, `Review.dc.html`, `Handovers.dc.html`, `Restore.dc.html`, and `gen.py`, the generator that holds every element's exact shape, ink and label in readable form. Every prototype row is a literal 116-column terminal row.

### 15.1 Supersedes

| Section | Superseded by |
|---------|---------------|
| §4.1 kit table (keycap, panel, card, toolbar, pager, selection bar, footer) | §15.2 vocabulary (`hooks/vocab.tsx`); `kit.tsx` keeps `kitOf`, `badge` (the band chip only), `fit`, `row`, `label`, `signal`, `rule`; the y/n row is the vocabulary's `askRow` |
| §5 phase strip (tabs, `+N ▾`, `0`–`9` on the strip) | §15.4 phase picker |
| §6 band layout and key map (`1`–`4`, counts on line 2, `x hide`) | §15.5 band |
| §7–§10 presentation (panels, cards, keycaps, help lines) | §15.6 lists and §15.7 per screen; the behaviour, data and flows of §7–§10 stand |
| §3 D7 navigation by band digits | §15.8 sidebar |
| The key map in §6 and the Stage 0 table | §15.9 keys |

Everything else (data, tm calls, D5, launcher, scanner, classification, bursts, faults, testing) stands.

### 15.2 One vocabulary (decision 1)

Every screen is built from the component sheet's elements and no others: FRAME, BAND, SIDEBAR, TOOLBAR, SUMMARY, BULK, SECTION, BADGE, ITEM, CARD, FIELD, SELECTION, KEY 1–5, LABELS, FOOTER, EMPTY, OFFLINE. Inks map onto RR tokens only: bold `fg.bold`, default `fg.default`, subtle `fg.subtle`, disabled `fg.disabled`, faint `border.strong`; rules `border.default`; the focused card's box `border.focus`; signature words `signatureText`; status words `tone.*`; the one filled key `keycap.signature`; the field and sidebar ground `surface.overlay`; the chosen row `tint24.overlay.signature`. Badges are a glyph and an upper-case word in their tone (`▲ BLOCKED`, `◆ ACTION`, `● IN REVIEW`, `✓ DONE`, `○ OPEN`, `↳ RESUMED FROM`, …), never a fill. The only tinted chip left is the band's `◆ TASKMASTER`.

### 15.3 The keycap ladder and labels (decisions 2, 3)

1. **Primary**: one filled key per context, the focused card's first action (review `d mark task done`, handovers `r resume handover here`, restore `r resume session`) and the Session tab's `d mark task done`. The band has none.
2. **Secondary**: the key letter bold in signature ink, no fill, then the label.
3. **Nav / meta**: never capped. They appear only in the one dim footer row per pane (`j/k move · s select · … · m sidebar · esc close`).
4. **Disabled**: the whole key dim; a press toasts why.
5. **Destructive**: the letter in warning ink, always last in its row; it asks y/n.

Every action label names its subject (`c copy handover`, `d mark task done`, `r resume session`, `b mark 2 tasks done`) and sits next to what it acts on. A bulk label carries its count and is disabled at 0.

### 15.4 Phases: a picker, no strip (decision 4)

Row 1 of a list pane starts with `‹ [ <chosen phase>  <count> ▾ ] ›`. `h`/`l` (or a click on `‹`/`›`) step both ways without wrapping; `f` (or a click on the field) opens the dropdown. The field's value is the chosen phase with its count (`Patch 1.9 · active  3`, `All  7`). The open list shows every shown phase with its count, the chosen one highlighted and done phases dim; `0`–`9` pick only while it is open; its last row is the `◐ show done phases` toggle. Order, counts, the `◐ done` default, per-pane memory and the fallback are §5's and stay.

### 15.5 Band (decision 5, direction C)

Subject text on the left, keys in one fixed right column (35 cells while the band is at least 80 cells wide: the prototype's 34 plus the leading cell of the key face, S6). Narrower, the labels shorten to `done`, `sidebar`, `copy`, `retry` before the subject text drops below 12 cells.

- Line 1: the `◆ TASKMASTER` chip, the task's glyph badge, its id (subtle), its title (bold); keys `d mark task done` (only when the bound task is ready for done, never when inferred) and `m open sidebar`.
- Line 2: `↳ resumed from <handover id> · <age> · <tldr>` (likewise `written here`, `for this task`) with `c copy handover`.
- States: **no handover**, line 2 reads `↳ no handover yet for this task` with no key; **nothing bound**, one line `no task bound to this session` with `m open sidebar`; **tm offline**, `▲ tm offline · <reason>` with `r retry tm connection` (a handover written here keeps its line 2 and `c`); **connecting**, `Connecting to Taskmaster…` with `m`; **confirm**, line 2 asks `mark <id> done?` with `y yes` / `n no`; **refused**, line 2 is `▲ REFUSED` and the server's words.
- No counts on the band. The waiting count lives in the sidebar's Review entry, the restore burst in its Restore entry. When the band shows (its visibility rule is §6's) is unchanged.
- Deviation from the prototype: the offline line names the failure's reason instead of "last ok 2m ago", because no last-good time is kept (follow-up).

### 15.6 Lists (decision 7)

Review, Handovers and Restore use the Session tab's round-1 layout: a fixed label column (selection mark 2 cells, badge 15 cells) with content from cell 19; each item 2–3 lines (title and age; dim context; the detail: `human`, `next`, the bound task); a dim `┄` rule between collapsed items; **3 items a page**; collapsed items show no keys; the focused item sits in a `border.focus` box with its action row under a blank line. Restore groups a page into UNFINISHED / HANDED OFF / IDLE, then RUNNING NOW, each under a section row; its burst page keeps the whole burst (§10).

### 15.7 Per screen

- **Frame:** `╭─ ◆ TASKMASTER · <PANE> ─ … ─ ● tm ok ─╮` on the page ground; its first row is the Taskmaster tab row (§15.11), not the engine's. Every row fits `bodyColumns`; the Session clipping bug is fixed by fitting every row, pinned by a width test.
- **Row order in a list pane:** toolbar (picker · toggles · pager · `u`), summary sentence and tallies, bulk row, blank, the list, blank, footer.
- **Session:** toolbar `this session · <branch> · started <age> ago`; sections TASK / HANDOVER / SESSION in the label column; actions under what they act on (`d mark task done`, `a send task to agent`; `c copy handover`, `h show in handovers`, `w resume in new tab`, `i handover decisions·blockers ▸`; `4 show burst in restore` once a burst is known).
- **Review / Handovers / Restore:** as their prototype artboards, with §15.9's labels and keys.

### 15.8 Sidebar and navigation (decision 6), with the feasibility decisions

**What the API allows (claude-code.d.ts 2.1.296).** A `Pane` is placed by the surface: docked beside the transcript in the fullscreen layout from 110 columns, else inline above the prompt; the dock's side is the engine's. A plugin's panes share one region, "one shown, the rest tabs", and the engine draws a tab row while more than one is open. No surface hosts a second, left-docked plugin region, and the `AbovePrompt` band cannot open a region of its own over the transcript.

**S1. The sidebar is an overlay inside the Taskmaster pane, not a region of its own.** It is a `position: "absolute"` box at the pane body's top-left (34 cells, `surface.overlay` ground, strong border) drawn over the pane's content, as the prototype draws it over the conversation. Absolute overlays passed the Stage 0 probe (Task 0.2: clean paint, Tab and press work). Reason: it is the closest faithful form the API can host; a separate `tm-nav` pane would be a tab or would replace the content, never a sidebar.

**S2. One Taskmaster pane at a time.** Opening any Taskmaster pane first closes the plugin's other open Taskmaster panes (`$.ui.panes()`, then `$.ui.close`), so the engine never draws its tab row. The sidebar and the plugin's own tab row atop every pane (§15.11) are the navigation surfaces.

**S3. `m` opens the sidebar.** In a pane, `m` toggles it. On the band, `m open sidebar` opens the last Taskmaster pane shown (Session at first) with the sidebar already open. The sidebar lists Review, Handovers, Session and Restore with counts (Review: the waiting total and a `▲ blocked ◆ action` note; Handovers: the open total and the newest age; Restore: the unfinished count of the pending burst and its time), the current pane marked; `1`–`4` jump, Tab + Enter pick, and picking the current pane only closes the sidebar. Restore is listed dim ("not built yet") until the restore pane ships.

**S4. Esc closes the innermost layer.** While the sidebar or the phase list is open, the person's Esc (`ui.close`, origin `person`) is refused for the pane and closes that layer instead; `m` and `f` close them too. To be confirmed live (plan Task A0). If a refusal does not hold, `SIDEBAR_ESC = 'pane'` and the hints say `m close` / `f close`.

**S5. The phase dropdown is an absolute overlay** two cells in, under the toolbar, over the list (`MENU_PLACEMENT = 'absolute'`; `'swap'`, which replaces the list while open, is the fallback). Stage 0 chose swap only because the Stage 1–4 tests assumed it; the rework rewrites those tests.

**S6. How a key letter is drawn.** The engine paints a hotkey Button as `k: label`. Action keys keep Stage 0's overlay face: a 3-cell glyph ` k ` laid over the engine's `k: ` (filled for the primary, a bold signature letter for secondary, warning ink for destructive, disabled ink when dim), so a key reads ` k label`. Footer keys are label-less hotkey Buttons drawn under the footer's text (`FOOTER_KEYS = 'under'`), so the footer reads as plain dim text while every listed key works; the fallback `'inline'` draws them as the engine's own `k: word`. Pager arrows, the picker's `‹ ▾ ›`, toggles and `u` are clickable Buttons without hotkeys; their keys live in the footer. To be confirmed live in Task A0 (the paint, and whether Tab stopping on the hidden footer keys is acceptable).

**S7. Tab, not ↑↓.** A focused pane scrolls on the arrows and walks its Buttons on Tab, so the sidebar and dropdown hints read `Tab move · enter open` where the prototype says `↑↓ move`.

### 15.9 Keys

Hotkeys stay `/^[a-z0-9]$/`.

| Where | Keys |
|-------|------|
| Band | `d` mark task done · `m` open sidebar · `c` copy handover · `r` retry tm connection (offline) · `y` / `n` |
| Every pane footer | `m` sidebar · `esc` close (the engine's) · `u` refresh (restore: rescan) |
| List pane footer | `j` / `k` move · `s` select (restore: tick) · `h` / `l` phase · `f` phases · `p` / `n` page |
| Sidebar | `1` Review · `2` Handovers · `3` Session · `4` Restore |
| Phase list (open) | `0`–`9` pick |
| Session | `d` mark task done (primary) · `a` send task to agent · `c` copy handover · `h` show in handovers · `w` resume in new tab · `i` decisions·blockers · `4` show burst in restore |
| Review card | `d` mark task done (primary) · `a` send task to agent · `o` open task · `v` task in viewer · `i` task detail · `t` tick check 1-9 · `c` copy live check; bulk `b` mark N tasks done · `g` send N tasks to agent |
| Handovers card | `r` resume handover here (primary) · `w` resume in new tab · `c` copy handover · `i` decisions·blockers · `x` close handover (destructive); bulk `b` close N handovers |
| Restore card | `r` resume session (primary) · `v` switch resume mode · `i` brief session · `c` copy resume command; bulk `b` resume N sessions · `g` brief N sessions; footer `z` compact |

While a y/n is up, the footer drops `p/n page`, so `n` can only answer no.

### 15.10 Constraints (unchanged)

RR via `$.rr` only and no hex in taskmaster-mods; no coloured left rails, no hover motion, no shadows; hotkeys `/^[a-z0-9]$/`; `hooks/handover-guard.ts` untouched; every new file opens with a `// User intent:` header; the validator's rules (no variable `h` in .tsx, `$`-taking functions at file top, every `$.state` key in `types/index.d.ts`, atom sources consts in the same file, `$` never crosses an import).

### 15.11 Tab row (2026-10-12, user request at Stage D check)

Every Taskmaster pane (Session, Review, Handovers; Restore in Stage E) draws a tab row as the first row inside its frame, above the toolbar, followed by one blank row: `☰ m  Review 5 │ Handovers 6 │ Session │ Restore`. `☰` opens or closes the sidebar; the `m` beside it is a signature-coloured hint drawn as text, not a hotkey (the footer owns `m`). Then one tab per pane in sidebar order, separated by a faint `│`: a plain Button with no hotkey (digits stay the sidebar's, the ticks' and the phase list's), clickable, Enter- and Tab-reachable, that opens its pane in place of the current one (`goTo`: still one Taskmaster pane at a time). A tab reads its pane's name and, where the sidebar shows one, its count in subtle ink (Review waiting, Handovers open, Restore unfinished once built). The current tab is bold signature text on the tint24 signature ground; others default ink; Restore is dim and toasts the sidebar's reason while unbuilt. Hover steps the background only. Narrow, the row gives way in this order: counts, then short names (Rev, Hand, Sess, Rest), then the `m` hint, then the tabs' padding; it is never wider than its room. A note page (review's `a` / bulk `g`) keeps its single key and draws no tab row. The sidebar (`m`, band `m`) is unchanged; the tab row is an always-visible shortcut beside it.
