<!-- User intent: make the Taskmaster mods look right ("beautiful cards") in the Claude desktop app's Code tab, not only in the terminal — same surfaces, desktop-native look — and pin down what Taskmaster itself needs to run there. -->

# Taskmaster mods on the desktop surface — Design Spec

**Date:** 2026-10-08
**Status:** Draft for review (Sections 1–3 approved in chat 2026-10-08; option A, approach 1)
**Builds on:** `docs/specs/2026-10-05-tui-mods-design.md` (the "TUI spec"). Everything there stands unless this spec says otherwise.
**Scope:** rename `rr-tui` → `rr-mods`; teach `$.rr` the `desktop` surface; taskmaster-mods draws with it on desktop; document running Taskmaster in the desktop app.
**Prototype:** https://claude.ai/artifact/Xt7nkD1Uz267erYJDoWPw3 (intended look; only its SVG parts are exact, §5.4).
**Platform:** Claude Code mods, d.ts of build 2.1.289 (early access). Desktop facts below are read from the d.ts, not yet probed live (§3).

---

## 1. Problem

The mods were built and tuned in Windows Terminal only. Every taskmaster-mods test mounts `surface: 'terminal'`, and the card look was decided against terminal screenshots. The mods API also draws on Claude Code Desktop (the desktop app's Code tab), where:

- terminal idioms look wrong: `─` rules, keycaps and chips faked with padded tinted Text, `●▲◆ⓘ` glyphs, painted full-width grounds;
- better primitives exist: native buttons (`variant`, a native close control) and `Svg`.

The user wants the same surfaces to look native and well-made on desktop, and wants to know what Taskmaster needs to work in the desktop app at all.

## 2. Goals and non-goals

### Goals
1. On desktop, the band, review queue pane and handovers pane draw with desktop-native treatments (§5) while keeping their content and behaviour (TUI spec §6).
2. The RR knowledge stays in one place: `$.rr` decides the per-surface look, so any future mod gets desktop for free.
3. Terminal output is unchanged, byte for byte.
4. A written, verified checklist for running Taskmaster (and the mods) in the desktop app (§9).

### Non-goals
- A kanban board inside a mod. The viewer opens in the desktop app's Browser pane instead (§9).
- A richer desktop review card (option B). Possible follow-up once this lands.
- Mobile and VS Code. They keep today's behaviour (taskmaster-mods' existing mobile handling stays; VS Code gets the terminal trees, which its table accepts).
- Any Taskmaster server or lifecycle change.

## 3. Platform facts this design rests on

From the d.ts (`mods/taskmaster-mods/.claude-plugin/types/claude-code/index.d.ts`, line numbers of build 2.1.289). **Unverified live** unless marked; S0 (§8) verifies the ones marked ★.

| Fact | Where | Consequence |
|---|---|---|
| Every `ui.render` event carries `e.surface` (`terminal \| desktop \| mobile \| vscode`) and `e.viewport` | 9647, 10304 | One hook draws per surface; branch on `e.surface` |
| Desktop's element table = terminal's minus `Raster`/`Image`, plus `Svg` | 3796 | A tree holding `Svg` fails validation on terminal: only ask for desktop trees when drawing for desktop |
| Box is "a flex div", Text "a styled span" on desktop; Ink's prop set only (no radius, font size, shadow) | 12129, 938–983 | Cards draw square in the app's font; size/radius only via `Svg` |
| Raw colours: "what a surface draws for one it does not know is its own" | 1670 | ★ hex on Box/Text must be seen on desktop |
| Button on desktop is always native, even with `plain`; `variant: 'primary' \| 'secondary'`; `role: 'dismiss'` = native close control | 1094, 1106, 1117 | Wrapper tints around buttons are dropped on desktop |
| `Svg`: `source` ≤ 131072 chars, `alt` required, `width`/`height` CSS px, `isInteractive` = sandboxed frame; scripts and event attributes stripped | 12192–12219 | Signals, pills, steps, keycaps, rules as SVG strings; presses go on an enclosing Button |
| `Client` modules draw with the terminal table (no `Svg`), in cells, even on desktop | 1399 | Not used for the look |
| `AbovePrompt` (band) and `Pane` are placed on desktop | 10179, 10236 | ★ both actually appear |
| Viewport columns/rows are px ÷ the code font's metric on remote surfaces | 10314–10340 | Width math in cells still works approximately; no hard px |
| `$.ui.mount({ surface: 'desktop' })` validates the tree and presses, never paint | 44, 15584 | Look is verified by eye (S4) |
| Polarity: `$.config.list()` theme row is the CLI theme | — | ★ whether it follows the desktop app's appearance |

## 4. Rename `rr-tui` → `rr-mods`

One commit before any desktop code (like `717ed2d`, the taskmaster-tui rename): folder `mods/rr-tui` → `mods/rr-mods`; `plugin.json` name; taskmaster-mods' `dependencies`, `.claude-plugin/types/rr-tui/`, test stub; the `PluginState` key `'rr-tui'` → `'rr-mods'`; plugin descriptions ("for terminal mods" → "for mods"); the TUI spec, its plan and the SDD ledger wording going forward (history stays). `$.rr` and `/rr-gallery` keep their names. A polarity choice saved under the old plugin name resets once. Worktrees under `.worktrees/` are left alone.

## 5. `rr-mods` on desktop

### 5.1 Contract change

- New type `RrSurface = 'terminal' | 'desktop'`.
- Every tree method and `button` / `buttonProps` take an optional `surface` (default `'terminal'`). With `'terminal'` or no `surface`, output is exactly today's.
- `tokens()` and `polarity()` stay surface-free: desktop uses the same RR table. (The `RrTokens.svg` field floated in chat is dropped: `$.rr` builds every SVG itself from the table, so consumers never need SVG colours.)
- `buttonProps({ key, surface: 'desktop', primary? })` returns `{ hotkey?, variant }` — `variant: 'primary'` when `primary`, else `'secondary'` — with no `plain` and no `hover` (the native button has its own colour-only hover).
- `button({ …, surface: 'desktop' })` returns a neutral wrapper (`flexDirection: 'row'`, no background, no border), so the consumer's keyed-wrapper markup is the same on both surfaces.
- Two new methods, so consumers stop drawing glyphs themselves:
  - `steps({ count, current, done, cap, surface? })` — the queue strip. Terminal: today's `●○ +N` text, moved out of `draw.tsx` unchanged. Desktop: an SVG segmented strip.
  - `checkbox({ checked, surface? })` — terminal: today's `☐`/`☑` text, moved unchanged. Desktop: an SVG box (outline / success fill with a tick).

### 5.2 Desktop treatments

All follow the user's UI rules: full-perimeter borders only, never a coloured side rail; surface stepping, never shadows; hover changes colour only.

| Element | Desktop |
|---|---|
| `surface({ level })` | Box with the level's background and a full `border-default` border; the active card keeps `round` + `border-strong`. Pane roots: whether `page` paints RR `bg-page` or leaves the app's ground is decided at S0 from screenshots. |
| `label` | Unchanged (uppercase `signature-text` Text). |
| `signal({ kind, word, detail? })` | SVG shape (circle success, triangle warning, diamond critical, ring-i info) ~11 px in the tone colour, then the word and detail as Text. |
| `chip({ text, tone, strength })` | SVG pill: rounded tint fill (12 or 24), full tone outline, small tone dot, label in a monospace face. States only, never a button. |
| `button` / `buttonProps` | Native button, `primary` for the card's one main action (`done`, `yes`, `copy` in handovers), `secondary` for the rest. |
| `keycap({ key, tone })` | SVG keycap: rounded outline in `border-strong`, key letters in `foreground-bold`. |
| `rule({ width })` | SVG hairline in `border-default`, width from `width` cells × the cell metric (S0 measures the px per cell). |
| `steps`, `checkbox` | As §5.1. |
| `row`, disclosure `▸ ▾` | Unchanged for now; S4 may promote them. |

### 5.3 SVG rules

- Built as strings by `$.rr` from the RR table for the active polarity; no `currentColor`, no CSS variables (the SVG frame does not follow the app theme).
- Every text value placed in an SVG is XML-escaped (`& < > " '`); chip labels can carry server text (gate names).
- Always `alt` (the word the shape stands for); `isInteractive` is never set.
- Pill width = label length × the monospace advance + padding, computed in `$.rr` (a guessed advance, tuned at S4); sizes stay well under the 131072-char cap.

### 5.4 Fidelity

The prototype shows the intended look. Only the SVG parts are exact. Box/Text cards will be square and use the app's font, and the native button style is the app's. The final look is set in S4 against real screenshots and recorded in the tuning log (§10).

### 5.5 `/rr-gallery` on desktop

The gallery draws every element through the `surface` it is rendered on, so tuning happens there first. Its polarity buttons (`1` dark, `2` light, `3` survivalist) work the same.

### 5.6 Polarity

`auto` keeps reading the `theme` row. If S0 shows it does not follow the desktop app's appearance, `auto` on desktop is documented as "set polarity explicitly" and the user sets `dark` or `light` in the plugin settings. No new detection is built without a reliable signal.

## 6. taskmaster-mods on desktop

- Each render hook passes `e.surface` to `$.rr` (`'desktop'` when `e.surface === 'desktop'`, else `'terminal'`).
- `register.tsx:494` stops casting `$.ui.resolve(e)` to the terminal table; the hook uses the table for its surface, so `Svg` is reachable on desktop and refused on terminal by type.
- `draw.tsx` swaps its own glyph drawing for `rr.steps` and `rr.checkbox`, and marks the primary action (`done`, `yes`, handovers `copy`) with `primary: true`.
- Checklist items stay Buttons (`1`…`9`); on desktop each is a row of the `checkbox` SVG and a native button carrying the item text.
- Pane close uses a `role: 'dismiss'` button on desktop (the terminal keeps `esc close` dim text).
- Layout, content, ordering, actions, confirmation and session binding: unchanged (TUI spec §6).

## 7. Testing

- **Terminal regression:** the existing rr-mods and taskmaster-mods suites pass unchanged; any terminal tree change is a failure.
- **rr-mods desktop:** every method, every tone/kind/level, every polarity, mounted on `desktop`; each validates against the desktop table. SVG checks: `alt` present, under the size cap, XML-escaping of `& < > " '`, no `currentColor`/`var(`. A desktop tree never reaches a terminal mount.
- **taskmaster-mods desktop:** band, review card (normal, confirm row, refused, queue clear) and handovers pane (collapsed, expanded) mounted on `desktop` with demo and fixture data; presses (`d`, `y`, `a`, item toggles, dismiss) behave as on terminal.
- **Paint:** by eye in the desktop app (S4); `$.ui.mount` cannot check it.

## 8. Build order

| Step | What | Who |
|---|---|---|
| S0 | **Baseline.** A throwaway probe mod reports what desktop actually gives: `e.surface`, `e.viewport` (cells and isFullscreen), the theme row, whether hex Box/Text colours, `round` borders, `AbovePrompt` and `Pane` render. Then screenshots of today's band, `/tm-review` and handovers pane on desktop (demo source). Decides the ★ facts in §3, pane-root ground (§5.2) and px per cell (§5.2 rule). | Claude preps, user runs |
| S1 | Rename (§4). | Claude |
| S2 | `$.rr` desktop (§5), with tests. | Implementation agent (opus) |
| S3 | taskmaster-mods desktop (§6), with tests. | Implementation agent (opus) |
| S4 | Live desktop tuning in `/rr-gallery` then the real panes; decisions into §10. | User + Claude |
| S5 | Desktop-app setup doc (§9), verified in a real Local session. | Claude, user confirms |

If S0 shows mods do not render on desktop in the app's bundled Claude Code, the work stops after S1 and the finding is reported.

## 9. Running Taskmaster in the desktop app

From the 2026-10-08 spike (local install facts verified; app behaviour from code.claude.com/docs/en/desktop and /plugins; not yet run live):

- **Local sessions only.** Cloud sessions run remotely and cannot reach the stdio `tm` server or the local `.taskmaster` store. SSH/WSL sessions need the plugin installed on that side.
- **Plugin:** the app reads the same `~/.claude` as the CLI; `taskmaster@gruku-tools` is already installed there.
- **`tm` server:** `uv run …backlog_server.py`; the app inherits the user PATH (not PowerShell profiles), and `uv`, Python and git are on the user PATH. Cold start is covered by the mods' retry (TUI plan Task 3b).
- **Hooks:** `.sh` scripts, so they need a POSIX shell; they fail open, so a missing shell means silently no gates or resurfacing. Verify they fire.
- **Mods:** no `--plugin-dir` in the app. Either `CLAUDE_CODE_PLUGIN_DIRS` in the session's environment editor (whether the app honours it, and its separator on Windows, are verified at S0), or install `rr-mods` then `taskmaster-mods` from the gruku-tools marketplace (TUI plan Task 5).
- **Viewer:** `backlog_open_viewer` serves on localhost, which the app's Browser pane opens.
- **Version:** the app bundles its own Claude Code (2.1.289 on 2026-10-08); mods need a build with the API used here.

S5 turns this into a user-facing section of the Taskmaster README (or a linked doc) once each line is verified.

## 10. Tuning log

Decisions made live in S0/S4, newest last. (Empty.)

## 11. Open items

1. Do hex colours, `round` borders and painted Box backgrounds render on desktop as on terminal? (S0)
2. Does the theme row follow the desktop app's appearance? (S0; §5.6)
3. Px per cell on desktop, for the rule and pill widths. (S0)
4. Does the app honour `CLAUDE_CODE_PLUGIN_DIRS`, and with which separator? (S0)
5. Does a native button show its `hotkey`? If not, the label carries no key hint on desktop and the keys still work. (S0/S4)
