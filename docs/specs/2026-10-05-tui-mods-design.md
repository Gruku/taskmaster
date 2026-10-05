<!-- User intent: design for Taskmaster's in-terminal UI as Claude Code mods — a review queue that clears the "Waiting on human" backlog, ambient current-task awareness per session, and a Reality Reprojection layer that explores the design system at the lowest TUI level. -->

# Taskmaster TUI mods — Design Spec

**Date:** 2026-10-05
**Status:** Approved 2026-10-05 (prototype decisions folded in; RR token gaps fixed upstream first, §5.2)
**Scope:** two Claude Code mods: `rr-tui` (Reality Reprojection for the terminal) and `taskmaster-tui` (Taskmaster surfaces). No Taskmaster server or lifecycle change.
**Platform:** Claude Code mods, v2.1.289 (early access; the build's `claude-code.d.ts` is authoritative).

---

## 1. Problem

Taskmaster is used agent-first: the agent works in the backlog, the user reads handovers (often pasted to Telegram) and rarely opens the viewer. Two frictions follow:

1. **Review pile-up.** Agents finish work, pass `review-gate`, deploy or push to dev, then move the task to `in-review` with a `human_action` describing the live check ("Live check on dev … ask Alexandr to confirm"). On the work board that column holds 16 tasks while `Done` holds 0. There is no fast way to walk the column, do the checks, and close tasks.
2. **No ambient state in the terminal.** Which task this session is on, what stage it's in, and what is waiting on the user are only visible by asking the agent or opening the viewer.

The user also wants this as a hands-on exploration of the Reality Reprojection (RR) design system at the lowest level of a TUI.

## 2. Goals and non-goals

### Goals

1. A **review queue** that walks `in-review` tasks one at a time, shows exactly what the user must check, and closes, returns or skips each with one key.
2. **Per-session current task** shown above the prompt, including when it moves to `in-review`.
3. A **handovers** view with copy (Telegram-ready) and resume.
4. A **reusable RR layer** for terminal mods, tuned visually in a gallery pane.
5. Board and terminal always agree: the mods read and write through the same `tm` MCP tools as everything else.

### Non-goals

- No change to Taskmaster's lifecycle, server rules or skills. `in-review` + `human_action` already is the sign-off state.
- No kanban board in the terminal (the viewer covers that).
- No resolving decisions inside the mod in v1 (decision options live in markdown bodies; the decision skill owns resolution).
- No always-on sidebar. Panes open on demand.

## 3. Platform facts this design rests on

Verified live with throwaway probe mods on 2026-10-05 (Windows Terminal, dark theme), or read from the mods docs and the build's types.

| Fact | Consequence |
|---|---|
| Hex colours render for `Text` `color`/`backgroundColor` and `Box` `borderColor`/`backgroundColor`; `Box` background fills the box | RR tokens map 1:1 as truecolour hex |
| `COLORTERM` is unset in Windows Terminal (`WT_SESSION` is set) | Never auto-detect colour depth from `COLORTERM`; survivalist is opt-in |
| `$.config.list()` `theme` row reads `"dark"` | Polarity follows the Claude Code theme |
| `$.ui.status` is always prefixed `⚠ <plugin>:` in yellow, under the prompt, above the user's statusLine | Status line is for faults only |
| The band (`AbovePrompt`) is shared by all mods; a tree replaces what later mods draw unless it includes `await next(e)` | Our band always includes `next(e)` |
| Band digit hotkeys fire from an empty prompt; letter hotkeys only while the band or a pane has focus | Band entry points use digits |
| Mods never read the keyboard; Up/Down move between controls; the focused `Button` draws in inverse (engine-owned) | Lists are one `Button` per row; selection styling is not RR-tintable |
| `$.mcp.call('plugin:taskmaster:tm', tool, args)` works from a separate mod; results are untyped `content` text | Data layer parses text/JSON per tool |
| A noun added in `engine.create` must be methods only; calls are async; it can return plain-data trees built with `h('Box', …)` | `$.rr` returns tokens and finished Box/Text trees; consumers draw their own Buttons |
| A dependent mod must reload after its provider reloads | Load/reload order matters in development |
| `$` can't be passed as a value or read without calling | Helpers live in the same file or take plain data |
| `/clear`, `/resume`, `/branch` reset `$.state` and change the session id; `session.start` doesn't refire; `classic.SessionStart` does with `source` | Session binding re-hydrates in `classic.SessionStart` |
| `$.store` is one JSON store per plugin shared by every session on the machine | Per-session keys; read-before-write |

## 4. Architecture

```
rr-tui            provides $.rr  (tokens + Box/Text trees, gallery pane)
  ▲ dependency
taskmaster-tui    status line · band · review queue pane · handovers pane
  │ $.mcp.call
plugin:taskmaster:tm   (unchanged MCP server)
```

- **`rr-tui`** owns everything visual that isn't interaction: colour tokens per polarity, and finished trees for surfaces, labels and signals. Reusable by any future mod.
- **`taskmaster-tui`** owns data, session binding, interaction and Taskmaster semantics. It draws Buttons itself (press handlers must stay in its own environment) and asks `$.rr` for everything else.
- **Development home:** both mods live in this repo under `mods/rr-tui/` and `mods/taskmaster-tui/` and load through `CLAUDE_CODE_PLUGIN_DIRS` (watched, hot-reloaded).
- **Shipping (decided 2026-10-05):** Taskmaster must work properly as the sole installed plugin. `rr-tui` and `taskmaster-tui` ship as separate, optional plugins from the claude-tools marketplace; `taskmaster-tui` depends on `rr-tui` and `taskmaster`, and Taskmaster depends on neither. Folding was dropped: on 2.1.289 a plugin with an unmet `dependencies` entry is disabled entirely, so a fold would switch Taskmaster off for anyone without `rr-tui`. A pytest guard keeps Taskmaster's manifest free of `dependencies` and its `hooks.json` free of `modules`.

## 5. `rr-tui`

### 5.1 Contract (`$.rr`)

Methods only, all async. Declared in `mods/rr-tui/types/index.d.ts` (`Rr`, `RrTokens`, `RrPolarity`, …).

| Method | Returns |
|---|---|
| `tokens()` | `RrTokens`: resolved hex for the active polarity |
| `polarity()` | `'dark' \| 'light' \| 'survivalist'` |
| `surface({ level, children, width?, padX? })` | Box tree; `level` is `raised \| overlay \| recessed` |
| `label({ text })` | Technical-voice section label (uppercase, `signature-text`) |
| `signal({ kind, word, detail? })` | Shape + word + optional detail: ● success, ▲ warning, ◆ critical, ⓘ info |
| `row({ cells, emphasis? })` | One-line row of text cells with RR spacing |
| `rule()` | A thin divider (`border-default`; `border-subtle` is invisible on overlay in dark) |
| `button({ treatment, tone })` | Wrapper `Box` props (`backgroundColor` / `borderStyle` + `borderColor`, `paddingX`) for a consumer-drawn `Button` (§5.4) |
| `keycap({ key, tone })` | Key letter on a solid tone block, as a finished Text tree |
| `chip({ text, tone, strength })` | Non-interactive state chip (`strength` 12 or 24) |

`children` and `cells` are plain-data trees or strings, so a consumer can nest its own Buttons inside an `rr` surface.

### 5.2 Token mapping

Source of truth: the RR Design System artifact's `project/tokens.json`. A generated `mods/rr-tui/hooks/tokens.ts` holds the table (regenerated by a small script, never hand-edited).

- **Grounds and surfaces:** `bg-page` is the terminal's own background (never painted). `surface-raised`, `surface-overlay`, `surface-recessed` paint as `Box` backgrounds. Surface stepping replaces shadows.
- **Text:** `foreground-bold` for titles and values, `foreground-default` for body, `foreground-subtle` for metadata.
- **Signature:** `signature` for focus-adjacent accents and section labels (`signature-text`). Spent surgically, per RR.
- **Semantic:** `color-success|warning|critical|info` for the shape glyph, `-subtle` for tinted backgrounds. Colour never travels without its shape and word.
- **Borders:** `border-default` full perimeter, `single` style; `round` reserved for the active surface. Never a coloured side rail (the platform has no per-side borders anyway).
- **Translucent tokens** (`signature-dim`, `-subtle` with alpha) are pre-composited onto their ground in the generated table, since the terminal has no alpha.
- **Voices:** one monospace face, so Declaration = uppercase + bold, Narrator = plain, Technical = dim/subtle. No italic.
- **Token gaps** (references §6: `signature-text` contrast in light and on dark overlay, `border-subtle` invisible on dark overlay, `surface-recessed` vs README) are fixed **upstream in the RR artifact's `tokens.json` before `rr-tui` is built**; the generated table then carries no local overrides. The `⚠` status-line yellow is the engine's and stays out of scope.

### 5.3 Polarity

`userConfig.polarity`: `auto` (default) | `dark` | `light` | `survivalist`. `auto` reads the `theme` row: names containing `light` map to light, everything else to dark. `survivalist` uses RR's survivalist values (value only, no hue) and must stay fully legible: hierarchy by bold/dim and shapes alone.

### 5.4 Button treatments

`Button` takes no colour, so colour comes from a wrapper `Box` around a `plain` Button (verified live 2026-10-05: tinted and bordered wrappers render, the button stays pressable). Rows of wrappers set `alignItems="flex-start"` so a bordered sibling doesn't stretch tinted ones to three rows. Each treatment has one job (user's choice from the probe, tunable in `/rr-gallery`):

| Treatment | Job | Where |
|---|---|---|
| Outline (`round` border in the tone colour) | The single primary action of a card (`done`) | Panes only — 3 rows tall |
| Chip 12% (tone `-subtle` composited on the ground) | Secondary actions (`back to agent`, `skip`, `open`) | Panes and band |
| Chip 24% (double-strength tint) | States, not actions: `refused` critical, `confirm done?` warning, `signed off` success | Card status line |
| Keycap (key letter, dark ink on solid tone) | Key legends (`d done  a back  s skip  o open`) | Pane footer, band hints |

Tones: `success` done, `warning` back to agent / pending, `critical` refused, `signature` neutral navigation. Tone always travels with a word; survivalist renders all four by weight and border only.

### 5.5 `/rr-gallery`

A pane (opened by command) drawing every `$.rr` element in every relevant state with sample data, plus buttons `1` dark, `2` light, `3` survivalist to flip polarity live. This is the design surface: the look is tuned here against screenshots, and decisions are recorded back into this spec.

#### Tuning log

- 2026-10-05: Panes paint their own ground — new surface level `page` (`bg-page` for the active polarity) on pane roots; the band still never paints. (Claude Code paints pane backgrounds itself, ~`#262626` in dark, lighter than `bg-page`, so unpainted RR surfaces stepped the wrong way and `overlay` vanished.)
- 2026-10-05: Button labels can't be coloured (no colour prop on `Button`); the engine draws them in the terminal's default foreground. `/rr-gallery` shows three label variants (a chip, b `variant="primary"`, c `›` glyph button + our own `foreground-bold` label) for the user to pick.
- 2026-10-05: Survivalist chip buttons get a value-only ground one surface step from the ground they sit on, so they read as buttons without hue. "Hue-free" means channels within 2: survivalist keeps RR's warm D2 temperature.
- 2026-10-05: `classic.SessionStart` can fire before a session is bound (`$.config.list` unavailable); it never throws and leaves the publish to `session.start`.
- 2026-10-05: Verified live: under `auto`, `/theme` → light switches the gallery to light.

## 6. `taskmaster-tui`

### 6.1 Surfaces

**Status line — faults only.**
- `◆ tm offline` when `tm` calls fail or time out; `ⓘ tm reply unreadable` when parsing fails. Cleared (`undefined`) otherwise.

**Band — current task + needs-you count.** Always composed with `await next(e)` so other mods keep their band. Yields entirely while `e.props.hasSurvey`. Respects `e.props.maxRows` and draws to `e.props.bodyColumns`.

```
TASK  tm-audit-030  Agent tool-use audit fixes            FULL · review-gate:pass
      IN PROGRESS → next: record merge
▲ 16 waiting on you   1 review   2 handovers
```

- Task row: shown only while this session is bound to a task (§6.3). Second line = status + next outstanding gate (from `backlog_task_pipeline`).
- When the bound task is `in-review`, the row becomes:
  ```
  REVIEW  tm-audit-030  waiting on you: <human_action, truncated>
          [d done] [a back to agent]
  ```
  `d`/`a` work when the band has focus; same actions as the queue (§6.2).
- Needs-you line: shown when the review count > 0 or open decisions exist; open decisions count in "waiting on you". `1` (digit hotkey, works from an empty prompt without focus) opens the review queue; `2` opens handovers. How the band gets keyboard focus for letter keys is engine-defined (Ctrl+X Tab or click).
- `[-]` collapses the band to one summary line; `[+]` restores it.
- Hidden entirely when there is no bound task and nothing waiting.

**Review queue pane — `/review` or band `1`.** Opened with `focus: true, closeOnEscape: true`. One card at a time:

```
REVIEW  3 of 16 · 2 done this pass
unified-chat-022  ◆ Critical · 1h
Unified chat pre-build gets the full supervisor toolset; cookbook owns the order
FULL · review-gate:pass · no branch
─────────────────────────────────────────────
Live check on dev (needs unifiedChatGenerate + unifiedChatBuild granted):
first unified message stays on intent/brief and does not build; …
─────────────────────────────────────────────
d done   a back to agent   s skip   o open in prompt   Esc close
```

- Order: priority (Critical → Low), then oldest first. Priority glyphs: ◆ Critical, ▲ High, ⓘ Medium, `·` Low. Items: `in-review` tasks; then P0/P1 open issues; then open decisions (show title, `o` fills the prompt to resolve via the decision skill).
- `human_action` is the body, shown in full (scrolls if long).
- `d` → inline confirm row `confirm done? y / n` (focus starts on `n`, so Enter cancels) → `backlog_complete_task(id, done: "Signed off in review queue")`. On server refusal (unpassed blocking gate, open linked bug) the card shows the refusal text as a `◆` signal and stays.
- `a` → an `Input` "note for the agent" → `backlog_update_task(id, status, in-progress)`, clear `human_action`, record the note with `backlog_note` → close the pane and `$.prompt.fill` "Back to <id>: <note>" (not submitted).
- `s` → next card; `o` → `$.prompt.fill("Look at <id>")` and the pane stays open. Only "back to agent" closes the pane.
- After the last card: "Queue clear" with the pass tally.

**Handovers pane — `/handovers` or band `2`.** On demand. Last 5 open handovers, newest first (`superseded` hidden), one Button per row (Up/Down to move). Footer `5 of 23 · superseded hidden`.
- `c` copy → `$.ui.copy` the Telegram-ready text: tldr + next action + absolute file path; toast on success, toast with the path on `no-clipboard`.
- `r` resume → `$.prompt.fill("Resume from handover <id> (<path>)")`; the pane stays open.

### 6.2 Actions and their safety

| Action | Tool | Server enforcement relied on |
|---|---|---|
| done | `backlog_complete_task` | refuses unpassed blocking gates and open `found_in` bugs; clears `human_action`; closes handovers; changelog entry via `done` text |
| back to agent | `backlog_update_task` (status) + `backlog_update_task` (`human_action` "") + `backlog_note` | legal `in-review → in-progress` transition |
| resolve decision | none in v1 — prompt fill to the decision skill | skill owns it |

Every write is one explicit key plus confirmation (`y` for done). No bulk actions in v1.

### 6.3 Session binding

- **Set** when this session's own `tool.call` hook sees, after `await next(e)` succeeds: `backlog_pick_task`, `backlog_claim`, or `backlog_update_task` moving a task to `in-progress`. The task id is read from the call's input, never the result.
- **Kept** when the task moves to `in-review` (the band shows the review form).
- **Cleared** on `backlog_complete_task` to `done`, archive, or a move back to `todo`.
- Stored in `$.state` (`taskmaster-tui.binding`) and mirrored to `$.store` under `binding:<session id>`.
- Re-hydrated in `classic.SessionStart` for `source` `clear` / `resume` / `fork`, using that event's `session_id` and the previous id where available. Stale store keys (>30 days) are pruned at `session.start`.
- **Fallback:** before any binding, a task id in the branch or worktree name is shown dimmed and marked `inferred`. Nothing else is guessed; never another session's task.
- Needs-you data is project-wide.

### 6.4 Data layer

| Need | Tool | Format |
|---|---|---|
| Review queue list | `backlog_continuity_items(action_class: "review")` (+ `"decide"`) | JSON |
| Card detail | `backlog_get_task(id)` (lazily, per shown card) | markdown `**field:** value` lines |
| Stage / next gate | `backlog_task_pipeline(id)` | markdown |
| Handovers | `backlog_handover_list(format: "json", status: "open")` | JSON |

- One module (`hooks/tm.ts`) owns calls and parsers; every parser is pure and unit-tested against captured real responses (fixtures captured from the claude-tools and CodeMaestro backlogs during build).
- Every call is wrapped in a 3 s timeout (the API has none) and aborts on `next.signal` where it runs inside a hook.
- **Refresh triggers:** `session.start`, `turn.complete` (main agent only, `e.agentId` unset), and after any `mcp__plugin_taskmaster_tm__*` write the session makes. Single-flight: one refresh at a time, later triggers collapse into one trailing refresh. Never on a timer. Never awaited by a turn.
- Results live in `$.state` so drawings redraw on write.

## 7. Failure handling

- `tm` unreachable or timed out: status line `◆ tm offline`; band keeps the task row from the local binding but drops stage and needs-you lines; panes show "Taskmaster unreachable" with the reason.
- Unparseable reply: treated as no data, status `ⓘ tm reply unreadable`, raw text to the debug log via `$.ui.log(…, { to: 'debug' })`.
- Write refused by the server: shown on the card as a `◆` signal with the server's text; nothing retried automatically.
- Clipboard unavailable: toast shows the path to copy by hand.
- Pane not placed (narrow, unasked): only user-initiated opens are used, so this shouldn't occur; if `isPlaced` is false, toast "widen the terminal".
- Any `ui.render` hook that lacks data returns `next(e)` (band) or a plain "Loading…" (pane); never throws.

## 8. Testing

`claude plugin test`, `claude plugin validate --strict`, and `tsc -p` on both mods.

- **rr-tui:** token resolution for dark, light, survivalist (including pre-composited alpha); survivalist trees carry no hue; every element validates on `terminal` and `desktop` (`$.ui.mount`).
- **taskmaster-tui:**
  - parsers against captured fixtures (including the native-store variants);
  - binding set/kept/cleared across simulated `tool.call` sequences; re-hydration after `classic.SessionStart({ source: 'clear' })` with a mocked store;
  - band hidden when nothing to show; composes `next(e)`; yields under a survey;
  - review queue: order, `d` → confirm → `complete_task` called with the right args, server refusal shown, `a` path, `s`, `o`;
  - handovers: copy text format with absolute path, `no-clipboard` path;
  - refresh single-flight and timeout via `mock.clock`.
- **Manual:** live session screenshots in dark, light and survivalist; narrow and wide; fullscreen (docked pane) and normal (inline).

## 9. Build order

0. Fix the RR token gaps upstream in the RR Design System artifact's `tokens.json` (§5.2).
1. `rr-tui` tokens + elements + `/rr-gallery`; visual tuning loop with the user.
2. `taskmaster-tui` surfaces against fixture data (no `tm`).
3. Data layer + parsers against real backlogs; binding.
4. Write actions (done, back to agent) behind confirmation.
5. Ship `rr-tui` and `taskmaster-tui` as optional marketplace plugins; verify Taskmaster still works as the sole plugin.

Tracked as one Taskmaster epic, one task per step, each through the review-gate.

## 10. Open items (resolve during build, not assumed)

- Exact argument names of `backlog_complete_task`, `backlog_update_task`, `backlog_note`, `backlog_pick_task`, `backlog_claim` (read from the server signatures).
- Whether the native-store routing returns the same text shapes as the legacy handlers for every tool above (capture fixtures from both).
- `classic.SessionStart` payload on `resume`: confirm it exposes the resumed session id needed to look up the stored binding.
- `rr-tui` shipping home: its own marketplace entry in claude-tools, or bundled beside Taskmaster.
- Behaviour when two mods' band hotkeys collide on `1`/`2` (later button wins per docs) — pick less contested digits if `you-should-know` or others use them.
