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
[‹ []  All 31 │▐ patch-1-9 · active 12 ▌│ release-2 4 │ patch-1-8 ✓ 0 │ +3 ▾  [] ›]   [◐ done]
```

- **Tabs are real buttons** (click / Enter). `‹` `›` keycaps at both ends and `[` / `]` step both ways; they dim at the ends, no wrap. `0` = All; `1`–`9` = visible tabs in order, numbers shown on focused tabs.
- **Order:** All, active phase, planned phases by `order`, done phases newest first. Restore and handovers add a trailing **no task** tab.
- **Counts** are per pane: review = waiting on you; handovers = open (or open + closed when `◐ closed` is on); restore = sessions in scope.
- **Empty and done phases** are dimmed but selectable. An empty selection shows "Nothing waiting in <phase> · done <date>" with a `[0 All]` keycap.
- **Overflow:** tabs that don't fit collapse into `+N ▾`; pressing it (or `/`) opens an overlay list (name, status, counts, dates), ↑↓ move, Enter pick, Esc close.
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
 REVIEW                                         [‹ p]  [n ›]  1 / 4   [R]
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
 ↑↓ move · space select · [ ] phase · 0 all · 1-9 jump · p/n page · R refresh
```

- Sort: BLOCKED (critical) > ACTION (has `human_action`, warning) > IN REVIEW (info), then newest. 5 per page; phase change resets to page 1 and focuses the first card.
- Only the focused card expands (lazy `backlog_get_task` detail as today). Checks are chips; tick with `t` + digit or Enter on the chip.
- Bulk: nothing selected by default. `Done N` → y/n → `backlog_complete_task` one at a time → one toast summarising successes and naming refusals; refused cards stay. `To agent N` → one shared note → `backlog_update_task` each.
- `s` skip is removed (the list replaces the stepper).
- Loading: busy line. Offline: critical signal + `[Retry]`.

## 9. Handovers pane (`2`, `/tm-handovers`)

```
 HANDOVERS                                  [‹ p]  [n ›]  1 / 3   [◐ closed]  [R]
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
╭─ ◆ TASKMASTER · RESTORE ─────────── [‹ p] [n ›] 1 / 3  [◐ compact] [R] ─╮
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
| Scanner fails / no Python | Restore shows "Scan failed: <reason>" in the pane with `[R]`; band shows no `[4]`. |
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
2. Overlay mechanics for the `+N ▾` phase list: a second Pane render vs an in-pane absolute Box — probe before building.
3. Whether band hotkeys `c`/`d` collide with typing in the prompt (today's band keys behave; confirm for `c`).
4. Desktop surface: confirm the panel's title-in-border renders; fall back to a title row if not.
