<!-- User intent: the step-by-step build plan for the desktop mods spec (2026-10-08) — rr-tui renamed rr-mods, $.rr taught the desktop surface, taskmaster-mods drawn natively in the Claude desktop app's Code tab, and a verified desktop-app runbook — written so a zero-context executor can implement each task through its own review gate. -->

# Taskmaster mods on the desktop surface Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the band, `/tm-review`, `/tm-handovers` and `/rr-gallery` look native in the Claude desktop app's Code tab (SVG pieces, native buttons, a native close control) while the terminal output stays byte for byte what it is today, and write a verified runbook for running Taskmaster in the desktop app.

**Architecture:**
- `rr-tui` is renamed `rr-mods` first (Task 1). Then `$.rr` learns an optional `surface` (`'terminal'` default, `'desktop'`) on every tree and button method. Terminal answers are today's, untouched. Desktop answers are Box/Text trees holding `Svg` leaves that `$.rr` builds as escaped strings from the RR table for the active polarity, plus native-button props (`variant`, no `plain`, no `hover`).
- Two new `$.rr` methods, `steps` and `checkbox`, take the last glyph drawing out of `taskmaster-mods`.
- `taskmaster-mods` binds the site's surface once, in its `rrOf($, surface)` adapter, so every `$.rr` call of a drawing carries it. `draw.tsx` branches on `ui.surface` only where the desktop differs in structure: the native close control, the checklist rows, and native-button widths.
- Terminal output is pinned before any desktop code by fingerprint tests captured on the unchanged code (Tasks 2a and 3, Step 1).

**Tech Stack:** Claude Code mods API, d.ts of build 2.1.289 (TypeScript/TSX hooks modules, `claude plugin test`, `claude plugin validate --strict`, `tsc -p`); Python 3.11+ stdlib for the token generator.

**Spec:** `docs/specs/2026-10-08-desktop-mods-design.md` (approved: sections 1–3 approved in chat 2026-10-08, option A, approach 1). It builds on `docs/specs/2026-10-05-tui-mods-design.md` (the "TUI spec") and its plan `docs/superpowers/plans/2026-10-05-tui-mods.md`. Executors read the spec section each task names. Live prototype of the intended look: https://claude.ai/artifact/Xt7nkD1Uz267erYJDoWPw3. Only its SVG parts are exact; their shapes and sizes are copied into `hooks/svg.ts` below.

**Who runs what (model guidance):**
- **Implementation tasks** (1, 2a, 2b, 3) run on **opus** subagents, one fresh reviewer per task.
- **Tasks 0, 4 and 5** need the user in the desktop app. The orchestrator runs them itself, not a subagent.
- **Order:** 0 → 1 → 2a → 2b → 3 → 4 → 5.
- **Gates:** Task 0 can stop the work (see its stop rules). Task 2a reads two numbers that Task 0 records.

## Global Constraints

Copied from the spec. Every task meets them.

- Terminal: "Terminal output is unchanged, byte for byte." (§2.3) "The existing rr-mods and taskmaster-mods suites pass unchanged; any terminal tree change is a failure." (§7) "With `'terminal'` or no `surface`, output is exactly today's." (§5.1)
- UI rules (§5.2): "full-perimeter borders only, never a coloured side rail; surface stepping, never shadows; hover changes colour only."
- SVG rules (§5.3):
  - "Built as strings by `$.rr` from the RR table for the active polarity; no `currentColor`, no CSS variables (the SVG frame does not follow the app theme)."
  - "Every text value placed in an SVG is XML-escaped (`& < > " '`); chip labels can carry server text (gate names)."
  - "Always `alt` (the word the shape stands for); `isInteractive` is never set."
  - "Pill width = label length × the monospace advance + padding, computed in `$.rr` (a guessed advance, tuned at S4); sizes stay well under the 131072-char cap."
- Contract (§5.1):
  - "`tokens()` and `polarity()` stay surface-free."
  - "`$.rr` and `/rr-gallery` keep their names."
- Behaviour (§6): "Layout, content, ordering, actions, confirmation and session binding: unchanged (TUI spec §6)."
- Other surfaces (§2): "Mobile and VS Code. They keep today's behaviour (taskmaster-mods' existing mobile handling stays; VS Code gets the terminal trees, which its table accepts)." No Taskmaster server or lifecycle change.
- Fidelity (§5.4): "Only the SVG parts are exact." Box/Text cards draw square in the app's font.
- Platform: "Claude Code mods, d.ts of build 2.1.289 (early access)." Validator rules, all probe-verified:
  - a function taking `$` is declared at the top of its file;
  - every `$.state` key is declared in the manifest's `PluginState`;
  - never name a variable `h` in a `.tsx` file;
  - noun methods return plain data only;
  - a Button can't cross a noun, so consumers draw their own Buttons;
  - never read `$.noun.method` without calling it.
- Repo rules:
  - Every new file starts with a 1–3 line header comment stating the user intent. When a file's reason for being changes, its header changes in the same edit.
  - Commits use plain `git add <paths>` + `git commit -m`. **Never push.**
  - Stay on branch `feat/database-native-foundation`.
  - Never `rm -rf`, never edit `~/.claude/settings.json`. The throwaway probe in Task 0 lives outside the repo and is never committed.

## Review Focus

These are the five inputs and failure modes the spec implies but no test of its §7 list would exercise, most likely first. Each has a pinning test inside the owning task.

1. **A narrow desktop pane.** Desktop columns are px ÷ the code font's metric, and a docked Code-tab pane is often narrow. Native buttons take more cells than plain terminal buttons. Expected: the confirm row never shows `yes` without `no`, focus starts on `no`, and the question is still there at 72, 34, 28 and 20 columns. Test: Task 3 `desktop.test.ts` "a narrow desktop pane never shows yes without no".
2. **A queue far larger than the strip, and degenerate strip inputs.** CodeMaestro has 355 in review. Expected:
   - the desktop strip draws 10 segments and a `+345` label;
   - an empty queue draws no SVG at all (the terminal's empty text);
   - `current` past the strip marks no segment current;
   - `done` past `current` never colours more segments than were passed;
   - NaN or a negative count draws nothing and never throws.

   Tests: Task 2a `desktop.test.ts` "steps: +N past the cap …", and Task 3 `desktop.test.ts` "a 355-item queue reaches the strip whole".
3. **Polarity flips while a desktop pane is open.** SVG colours are baked into strings, so a stale SVG would keep the old polarity's colours on the new ground. Expected: after the flip, every SVG is rebuilt in the new polarity's colours. Test: Task 2b, gallery desktop test (the unchecked box's stroke follows `pol-light` and `pol-dark`).
4. **VS Code and mobile.** VS Code's element table accepts `Svg`, so a slip like `e.surface !== 'terminal' ? 'desktop' : …` would pass validation and silently give VS Code the desktop look. Expected:
   - VS Code gets the terminal trees: no Svg, `esc close`, plain hotkey buttons;
   - mobile keeps its one-line text.

   Test: Task 3 `desktop.test.ts` "VS Code draws the terminal trees; mobile keeps its one line".
5. **Survivalist on desktop.** Survivalist has no hue, and its success and signature tones are the same grey (`#f5f3ed`). Expected:
   - every colour inside every desktop SVG is a ground value;
   - in the strip, the current segment still differs from the done ones.

   Tests: Task 2a `desktop.test.ts` "survivalist SVGs carry no hue" and the survivalist case in "steps".

---

## Facts this plan relies on (checked 2026-10-08 while writing it)

| Fact | Where it is used |
|---|---|
| Baseline:<br>• `claude plugin test mods/rr-tui`: **24 pass**.<br>• `claude plugin test mods/taskmaster-mods`: **196 pass**.<br>• `python -m unittest discover -s mods/rr-tui/scripts -p "test_*.py"`: **12 OK**.<br>• `gen_tokens.py --check`: exit 0.<br>• `claude plugin validate --strict mods/rr-tui`: ✔ (lists `state writes: rr-tui.override, rr-tui.polarity`). | Expected counts in every task. |
| In a `ui.render` hook, `if (e.surface === 'mobile') return …` narrows `e`, even across an `await`. After it, `$.ui.resolve(e)` is the terminal/desktop/vscode union, and it assigns to `Pick<Elements['terminal' \| 'desktop' \| 'vscode'], 'Box' \| 'Text' \| 'Button' \| 'Input'>` with no cast. (`tsc` probe in the scratchpad against the laid 2.1.289 types.) | Task 3 removes the three `as unknown as Ui` casts (`register.tsx:494, 533, 573`). |
| `ButtonProps.label` is a string (d.ts ~1066). | The terminal checklist puts the `rr.checkbox` glyph into the label as text (Task 3). |
| The global JSX factory `h` accepts a string tag (d.ts 14582). | `kit.ts` builds `h('Svg', {…})` as plain data, as it already builds `h('Box', …)`. |
| `Svg` props: `source` (≤ 131072 chars), `alt` (required), `width`/`height` in CSS px, `isInteractive` (d.ts 12192–12219). The desktop table has `Svg`; the terminal table does not (d.ts 3796). | `hooks/svg.ts` and `tests/fixtures/svg.ts`. |
| `Button` has `variant: 'primary' \| 'secondary'` and `role: 'dismiss'`. A desktop always draws a native button, even with `plain` (d.ts 1094–1117). | `buttonProps({ surface: 'desktop' })`; the panes' close control. |
| Testing kit: `ui.drawn()` "rejects with the refusal when the surface could not draw what the chain returned"; `find`/`findAll` return `{ type, key, props, text, children }` (d.ts 15395–15420, 15017). | Svg checks read `props`; the "a forced desktop tree is refused on the terminal" test. |
| `.claude-plugin/types/` in each mod is engine-laid and git-ignored (`*`). `taskmaster-mods/.claude-plugin/types/rr-tui/index.d.ts` is a symlink to `mods/rr-tui/types/index.d.ts`. | Task 1 re-lays the types instead of renaming them. |
| The SDD ledger `.superpowers/sdd/2026-10-05-tui-mods/progress.md` is git-ignored. | Task 1 appends a line and does not commit it. |
| Survivalist table: `tone.success` = `tone.signature` = `#f5f3ed`, `foreground-subtle` `#a09c95`, `foreground-disabled` `#66645f`, `border-strong` `#4d4c48`. All are ground values. | The desktop strip uses `fg.subtle` for done segments in survivalist. |
| `queueDots(n, total, cap = 10)` lives in `taskmaster-mods/hooks/model.ts:317`. `draw.tsx:394` draws it as `<Text color={t.signatureText}>{dots}</Text>`. The checklist glyphs are inline at `draw.tsx:519`. | Task 2a copies `queueDots` into `kit.stepsText`. `taskmaster-mods` keeps `queueDots` for the strip's cell budget. |
| The prototype's SVG sizes:<br>• signal: 11 px, viewBox 12;<br>• pill: 20 px tall, `rx 9.5`, dot at (11,10) r 3.5, text at x 20, 11 px monospace;<br>• strip: 18×8 segments, 3.5 apart (5 segments = 104 px);<br>• checkbox: 14 px;<br>• keycap: 18 px tall, `rx 4`, 9.5 px letters, 30 px wide for `ESC`. | `hooks/svg.ts`. |
| Old TUI plan Task 5 (marketplace entries, `tests/test_plugin_standalone.py`) is **not done**. Nothing in `claude-tools` names `rr-tui` yet. | The rename touches only this repo. The runbook says the marketplace route is not available yet. |

## Spec ambiguities resolved in this plan

1. **The terminal gallery gains two sections.** `/rr-gallery` shows the new `steps` and `checkbox` elements on both surfaces, so the gallery's terminal tree grows two sections. "Byte for byte" is enforced on every pre-existing `$.rr` answer (Task 2a golden) and on every taskmaster-mods surface (Task 3 golden), not on the gallery tree.
2. **The checkbox inside a terminal Button.** A terminal check item's glyph sits inside a Button label, which must be a string. `draw.tsx` therefore reads the text of the terminal `rr.checkbox` answer. On desktop it places the SVG beside a native button.
3. **`steps` arguments.** `done` does not change the terminal strip (`●○ +N` is unchanged). On desktop it colours the passed segments: done, then skipped. `cap` is optional, with default 10, as `queueDots` has.
4. **Pane roots on desktop.** A desktop `surface`/`surfaceProps` at level `page` gets **no** border: it is a ground, not a card. Whether it paints `bg-page` is the constant `DESKTOP_PAINTS_PAGE`, set from S0.
5. **Pill marker.** Every chip tone gets the spec's "small tone dot" (a circle), signature included. The word carries the meaning, as in the prototype.
6. **Width budgets on desktop.** Consumers keep their terminal cell estimates for `$.rr` pieces. A native button counts as label + `NATIVE_BUTTON_CELLS` (4), the same rule `tests/fixtures/measure.ts` applies to a chrome Button. SVG sizes follow the prototype. S4 checks for overflow by eye.
7. **Desktop check items.** On desktop, a check item's native button carries the whole item text. The terminal's manual wrapping is not repeated there; the desktop wraps its own labels.
8. **The close control.** The native close control (`role: 'dismiss'`, label `close`) is drawn in every desktop pane state, loading and unreachable included, so a desktop pane can always be closed.

## File structure

```
C:/Users/gruku/.claude/dev-mods/desktop-s0/probe-desktop/      Task 0 — throwaway, outside the repo, never committed
  .claude-plugin/plugin.json  hooks/hooks.json  hooks/register.tsx  tsconfig.json

mods/rr-mods/                       (Task 1: git mv from mods/rr-tui)
  .claude-plugin/plugin.json        name rr-mods; description "for mods"
  types/index.d.ts                  Rr contract: RrSurface, surface? on every tree/button method, steps, checkbox,
                                    terminal | desktop button props; PluginState 'rr-mods'           (Task 1, 2a)
  hooks/svg.ts            NEW       SVG string builders, escapeXml, named size constants              (Task 2a)
  hooks/kit.ts                      per-surface element builders, stepsText, DESKTOP_PAINTS_PAGE      (Task 2a)
  hooks/register.tsx                rr.steps / rr.checkbox hooks (2a); gallery draws its surface (2b)
  hooks/gallery.ts                  every element through `surface`; steps and checkbox sections      (Task 2b)
  hooks/polarity.ts, hooks/tokens.ts, scripts/*   rename only                                         (Task 1)
  tests/fixtures/golden.ts  NEW     canonical fingerprint of a tree                                   (Task 2a)
  tests/fixtures/svg.ts     NEW     svgFaults / svgProps / svgsOf                                     (Task 2a)
  tests/fixtures/desktop-consumer.tsx NEW  a plugin drawing every $.rr element through the noun       (Task 2a)
  tests/fixtures/inputs.ts          + probePane()                                                     (Task 2a)
  tests/terminal.test.ts    NEW     terminal golden pins                                              (Task 2a)
  tests/desktop.test.ts     NEW     pure desktop kit tests                                            (Task 2a)
  tests/desktop-noun.test.ts NEW    noun + gallery mounted on the desktop                             (Task 2a, 2b)
  tests/noun.test.ts                gallery validation test narrowed to the terminal                  (Task 2b)

mods/taskmaster-mods/
  .claude-plugin/plugin.json        dependencies ["rr-mods"]; description                              (Task 1)
  hooks/rr.ts                       + RrSurface                                                        (Task 3)
  hooks/draw.tsx                    Ui carries its surface; uiOf; rr.steps / rr.checkbox; primary;
                                    native close; desktop check rows; native button widths              (Task 3)
  hooks/register.tsx                RR_POLARITY 'rr-mods' (1); rrOf passthroughs (2a); rrOf($, surface),
                                    uiOf, close handlers, no casts (3)
  tests/fixtures/rr-stub.ts         name rr-mods (1); steps/checkbox (2a); desktop answers (3)
  tests/fixtures/golden.ts  NEW     same fingerprint as rr-mods'                                       (Task 3)
  tests/terminal.test.ts    NEW     terminal golden pins                                               (Task 3)
  tests/desktop.test.ts     NEW     band / review / handovers mounted on desktop, VS Code, mobile       (Task 3)

docs/specs/2026-10-08-desktop-mods-design.md   §3 ★ results, §10 tuning log, §11 answers (Tasks 0, 4); §9 verified (5)
docs/specs/2026-10-05-tui-mods-design.md, docs/superpowers/plans/2026-10-05-tui-mods.md   rename wording (Task 1)
docs/runbooks/desktop-app.md        NEW (Task 5)
README.md                           one link line (Task 5)
```

## Conventions for every task

- **Shell.** Commands are written for Git Bash with absolute forward-slash paths. `$REPO` means `C:/Users/gruku/Files/Claude/taskmaster`; write it out in full, and never `cd`.
- **Mod checks**, per mod (after Task 1, the folders are `mods/rr-mods` and `mods/taskmaster-mods`):
  ```bash
  claude plugin test C:/Users/gruku/Files/Claude/taskmaster/mods/<mod>
  claude plugin validate --strict C:/Users/gruku/Files/Claude/taskmaster/mods/<mod>
  npx -y -p typescript@5.6.3 tsc -p C:/Users/gruku/Files/Claude/taskmaster/mods/<mod>
  ```
- **Engine types.** `tsc` needs the engine-laid `.claude-plugin/types/`. Lay or refresh them with:
  ```bash
  env -u ANTHROPIC_API_KEY claude -p --model haiku --plugin-dir C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods --plugin-dir C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods "Reply with the single word ok."
  ```
  The engine writes a `.gitignore` inside that folder. Never commit it.
- **Test runner output.** `claude plugin test` prints `(pass)`/`(fail)` per test, then `N pass / M fail`. A failing `toEqual` prints the received value: that is how the golden fingerprints are read in Tasks 2a and 3.
- **A failing check.** Fix the code, not the test, unless the test contradicts this plan or the spec. If the terminal golden fails, the terminal output changed. That is never acceptable: find the change and undo it.
- **Hot reload.** After saving `rr-mods`, re-save any `taskmaster-mods` file so it reloads, or its `$` lacks the new noun methods.
- **Live sessions** with real Taskmaster data must start in a project with a backlog (`C:\Users\gruku\Files\Claude\claude-tools` or `C:\Users\gruku\Files\Work\CodeMaestro`). In this repo use `taskmaster-mods` Data source = `demo`; the repo's project `tm` server is a phantom (`CONNECTION_CLOSED`).

---

### Task 0: S0 baseline — probe the desktop surface (orchestrator + user, no shipped code)

**Spec:** §3 (★ rows), §5.2 (pane-root ground, rule px per cell), §5.6, §8 S0 and the stop rule, §11 items 1–5.

**Files:**
- Create (outside the repo, never committed):
  - `C:/Users/gruku/.claude/dev-mods/desktop-s0/probe-desktop/.claude-plugin/plugin.json`
  - `C:/Users/gruku/.claude/dev-mods/desktop-s0/probe-desktop/hooks/hooks.json`
  - `C:/Users/gruku/.claude/dev-mods/desktop-s0/probe-desktop/hooks/register.tsx`
  - `C:/Users/gruku/.claude/dev-mods/desktop-s0/probe-desktop/tsconfig.json`
- Modify: `docs/specs/2026-10-08-desktop-mods-design.md` (§3, §10, §11)

**Interfaces:**
- Consumes: today's `mods/rr-tui` and `mods/taskmaster-mods` (S0 runs before the rename).
- Produces, all recorded in spec §10 and §11 and read by Task 2a:
  - **px per cell** on desktop, which becomes `DESKTOP_PX_PER_CELL`;
  - **pane-root ground** (paint `bg-page` or leave the app's ground), which becomes `DESKTOP_PAINTS_PAGE`;
  - whether hex Box/Text colours, `round` borders and painted Box backgrounds render;
  - whether the theme row follows the app's appearance;
  - whether a native button shows its `hotkey`;
  - the `CLAUDE_CODE_PLUGIN_DIRS` route and separator.

- [ ] **Step 1: Write the probe**

`C:/Users/gruku/.claude/dev-mods/desktop-s0/probe-desktop/.claude-plugin/plugin.json`:

```json
{
  "name": "probe-desktop",
  "version": "0.0.1",
  "description": "Throwaway S0 probe: what the desktop Code tab actually draws (surface, viewport, theme row, colours, borders, Svg, native buttons).",
  "author": { "name": "gruku" }
}
```

`C:/Users/gruku/.claude/dev-mods/desktop-s0/probe-desktop/hooks/hooks.json`:

```json
{ "modules": ["./register.tsx"] }
```

`C:/Users/gruku/.claude/dev-mods/desktop-s0/probe-desktop/tsconfig.json`:

```json
{ "extends": "./.claude-plugin/types/tsconfig.json" }
```

`C:/Users/gruku/.claude/dev-mods/desktop-s0/probe-desktop/hooks/register.tsx`:

```tsx
// User intent: throwaway S0 probe for the desktop-mods plan — one band row and one pane showing what the desktop Code tab
// actually draws (surface, viewport, theme row, hex colours, borders, Svg, native buttons), so the spec's open items are
// answered from screenshots before any desktop code is written. Never shipped, never committed.
import type { Register, RenderNode } from 'claude-code'

const PANE = 'probe-desktop'
const RULER_PX = 600
const GUESS_PX_PER_CELL = 8
// A 600 px ruler: a tick every 10 px, a tall one every 50 px, numbers every 100 px.
const RULER =
  `<svg xmlns="http://www.w3.org/2000/svg" width="${RULER_PX}" height="22" viewBox="0 0 ${RULER_PX} 22">` +
  Array.from({ length: RULER_PX / 10 + 1 }, (_, i) =>
    `<rect x="${Math.min(i * 10, RULER_PX - 1)}" y="0" width="1" height="${i % 5 === 0 ? 10 : 5}" fill="#5e79e6"/>`,
  ).join('') +
  Array.from({ length: RULER_PX / 100 }, (_, i) =>
    `<text x="${i * 100 + 2}" y="21" font-family="Consolas, monospace" font-size="10" fill="#a09c95">${i * 100}</text>`,
  ).join('') +
  '</svg>'
const line = (px: number): string =>
  `<svg xmlns="http://www.w3.org/2000/svg" width="${px}" height="2" viewBox="0 0 ${px} 2"><rect width="${px}" height="2" fill="#d14343"/></svg>`
const SHAPES =
  '<svg xmlns="http://www.w3.org/2000/svg" width="70" height="12" viewBox="0 0 70 12">' +
  '<circle cx="6" cy="6" r="5.5" fill="#3a9a5b"/><path d="M24 1 29.5 11H18.5Z" fill="#c4881d"/>' +
  '<path d="M42 .5 47.5 6 42 11.5 36.5 6Z" fill="#d14343"/>' +
  '<circle cx="60" cy="6" r="5" fill="none" stroke="#5b8fc7" stroke-width="1.4"/><path d="M60 5.2v3.6M60 3.3v.2" stroke="#5b8fc7" stroke-width="1.4"/></svg>'
const PILL =
  '<svg xmlns="http://www.w3.org/2000/svg" width="132" height="20" viewBox="0 0 132 20">' +
  '<rect x=".5" y=".5" width="131" height="19" rx="9.5" fill="#1f3a28" stroke="#3a9a5b"/><circle cx="11" cy="10" r="3.5" fill="#3a9a5b"/>' +
  '<text x="20" y="14" font-family="JetBrains Mono, Consolas, monospace" font-size="11" fill="#f5f3ed">review-gate pass</text></svg>'

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    try {
      await $.command.register({ name: PANE, description: 'S0 probe: what the desktop surface draws' })
    } catch {
      // registered by an earlier load of this module
    }
    return next(e)
  })

  on('command.run', { command: PANE }, async $ => {
    const opened = await $.ui.open({ id: PANE, title: 'desktop probe', focus: true, closeOnEscape: true })
    return { text: `probe pane placed: ${opened.isPlaced}` }
  })

  on('ui.render', { component: 'AbovePrompt' }, async ($, e, next) => {
    const below = await next(e)
    if (e.props.hasSurvey) return below
    const { Box, Text } = $.ui.resolve(e)
    return (
      <Box flexDirection="column">
        <Box flexDirection="row" columnGap={1} backgroundColor="#1d1d1b">
          <Text color="#8a9eeb" bold>
            probe band
          </Text>
          <Text color="#a09c95">{`surface=${e.surface} bodyColumns=${e.props.bodyColumns} maxRows=${e.props.maxRows}`}</Text>
        </Box>
        {below}
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: PANE }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const theme = (await $.config.list()).find(row => row.key === 'theme')?.value
    const cols = e.props.bodyColumns
    const pressed = (key: string) => () => $.ui.toast(`probe: pressed ${key}`)
    let vector: RenderNode[] = []
    if (e.surface === 'desktop') {
      const { Svg } = $.ui.resolve(e)
      vector = [
        <Text color="#a09c95">{`600 px ruler; the red line under it is bodyColumns x ${GUESS_PX_PER_CELL} px:`}</Text>,
        <Svg source={RULER} alt="600 px ruler" width={RULER_PX} height={22} />,
        <Svg source={line(cols * GUESS_PX_PER_CELL)} alt="bodyColumns times the guessed px per cell" width={cols * GUESS_PX_PER_CELL} height={2} />,
        <Box flexDirection="row" columnGap={1}>
          <Svg source={SHAPES} alt="success warning critical info" width={70} height={12} />
          <Svg source={PILL} alt="review-gate pass" width={132} height={20} />
        </Box>,
      ]
    }
    return (
      <Box flexDirection="column" rowGap={1}>
        <Text>{`surface=${e.surface}`}</Text>
        <Text>{`viewport=${JSON.stringify(e.viewport ?? null)}`}</Text>
        <Text>{`bodyColumns=${cols} placement=${e.props.placement}`}</Text>
        <Text>{`theme row=${JSON.stringify(theme)}`}</Text>
        <Text>{'0123456789'.repeat(Math.ceil(cols / 10)).slice(0, cols)}</Text>
        {vector}
        <Box backgroundColor="#272725" paddingX={1}>
          <Text color="#8a9eeb">hex #8a9eeb text on a painted #272725 box</Text>
        </Box>
        <Box borderStyle="round" borderColor="#5e79e6" paddingX={1}>
          <Text>round border #5e79e6</Text>
        </Box>
        <Box borderStyle="single" borderColor="#d14343" paddingX={1}>
          <Text color="#3a9a5b" bold>
            single border #d14343, bold #3a9a5b text
          </Text>
        </Box>
        <Box key="buttons-box" flexDirection="row" columnGap={1}>
          <Button key="primary" hotkey="p" variant="primary" label="primary (p)" onPress={pressed('primary')} />
          <Button key="secondary" hotkey="s" variant="secondary" label="secondary (s)" onPress={pressed('secondary')} />
          <Button key="plain" plain hotkey="k" label="plain (k)" onPress={pressed('plain')} />
          <Button key="hover" hotkey="u" label="hover bold (u)" hover={{ bold: true, color: '#f5f3ed' }} onPress={pressed('hover')} />
          <Button
            key="close"
            role="dismiss"
            label="close probe"
            onPress={() => {
              void $.ui.close({ id: PANE })
            }}
          />
        </Box>
      </Box>
    )
  })
}
```

- [ ] **Step 2: Check the probe loads, validates and type-checks**

Run:
```bash
claude plugin validate --strict C:/Users/gruku/.claude/dev-mods/desktop-s0/probe-desktop
env -u ANTHROPIC_API_KEY claude -p --model haiku --plugin-dir C:/Users/gruku/.claude/dev-mods/desktop-s0/probe-desktop "Reply with the single word ok."
npx -y -p typescript@5.6.3 tsc -p C:/Users/gruku/.claude/dev-mods/desktop-s0/probe-desktop
```
Expected:
- `validate` ends with ✔ Validation passed.
- The `-p` run prints `ok` and lays `.claude-plugin/types/`.
- `tsc` prints nothing.

If `tsc` flags a prop, fix the probe. It must type-check, because it shows the real API.

- [ ] **Step 3: Ask the user to open a desktop Local session with the mods (one message, exact steps)**

Send the user these steps:

1. Close every terminal Claude Code session that loads `mods\` (so nothing holds the folders open).
2. In the Claude desktop app, open the **Code** tab and start a new session with environment **Local**, folder `C:\Users\gruku\Files\Claude\taskmaster`.
3. Open that Local environment's **environment editor** (environment variables) and add:
   `CLAUDE_CODE_PLUGIN_DIRS` = `C:\Users\gruku\.claude\dev-mods\desktop-s0\probe-desktop;C:\Users\gruku\Files\Claude\taskmaster\mods\rr-tui;C:\Users\gruku\Files\Claude\taskmaster\mods\taskmaster-mods`
   Then start (or restart) the session.
4. **Check:** a `probe band · surface=… bodyColumns=…` row should sit above the prompt, and `/probe-desktop` should be a command.
5. **If not, narrow it down in this order:**
   - a. Set the variable to the probe path alone (no separator) and restart. If the probe now loads, the separator is wrong: try the full list joined with `,`.
   - b. If even the single path does not load, the app ignores the editor's variable. Fall back to a user-level variable. In PowerShell:
     `[Environment]::SetEnvironmentVariable('CLAUDE_CODE_PLUGIN_DIRS', '<the ;-joined list above>', 'User')`
     Then quit the desktop app fully (tray icon → Quit), reopen it, and start a new Local session.
     This also affects every terminal `claude` while set. Remove it after S0 with:
     `[Environment]::SetEnvironmentVariable('CLAUDE_CODE_PLUGIN_DIRS', $null, 'User')`
   - c. If nothing loads, stop and tell me (stop rule below).
6. In the session, run `/probe-desktop`, then screenshot the whole pane:
   - with the app's appearance dark;
   - again after switching the app's appearance to light (app settings), without touching `/config`.
7. In the probe pane:
   - click each button (primary, secondary, plain, hover bold);
   - click into the pane and press `p`, `s`, `k`, `u` on the keyboard;
   - screenshot the toasts;
   - press the native close control (it closes the pane, or not).
8. Set taskmaster-mods' **Data source** to `demo`. Try `/config` in the desktop session first. If the app has no `/config`, set it in any terminal `claude` session (`/config` → taskmaster-mods → Data source → demo; the app reads the same `~/.claude`), then start a new desktop session.
9. Screenshot today's look:
   - the band above the prompt;
   - `/tm-review`;
   - `/tm-handovers`;
   - `/rr-gallery` after pressing `1`, `2`, `3` and `0`.
10. Paste all screenshots into this chat. Also say which route from step 5 worked, and the app version and Claude Code version it reports (Settings → About, or `/status` in the session).

- [ ] **Step 4: Read the answers off the screenshots**

For each item, write the finding as one line:

| Item | Read off |
|---|---|
| Mods render on desktop (stop rule) | Band row present with `surface=desktop`; `/probe-desktop` opens a pane. |
| `AbovePrompt` and `Pane` placed (§3 ★) | Band row, pane. |
| Hex Box/Text colours, `round` border, painted Box background (§11.1) | The three test boxes: colours as written, round corners or not, filled background or not. |
| Theme row follows the app appearance (§11.2) | `theme row=` before and after the appearance switch. |
| px per cell (§11.3) | `ppc = (px where the pane's inner width ends on the ruler) / bodyColumns`. Cross-check: the red line is `bodyColumns × 8` px; if it ends exactly at the pane edge, 8 is right. Round to 0.5. |
| Native button shows its `hotkey` (§11.5) | Whether `p:` / `s:` appears on the native buttons beside the `(p)` written in the label; whether the keys pressed them (toasts). |
| `variant` and `role: 'dismiss'` | Primary vs secondary look; the close control at the trailing edge and whether it closed the pane. |
| Pane-root ground (§5.2) | In the `/tm-review` and `/rr-gallery` screenshots, does today's painted `bg-page` look right on the app's ground, or like a foreign panel? Ask the user which they prefer when it is not obvious. |
| `CLAUDE_CODE_PLUGIN_DIRS` route and separator (§11.4) | The route the user reports from Step 3, item 5. |

- [ ] **Step 5: Apply the stop rules**

- **Mods do not render on desktop** (no probe band and no probe pane under any route, or `e.surface` is never `desktop`): record the finding (Step 6), commit it, do **Task 1 only**, and report to the user. Spec §8: "the work stops after S1".
- **Hex colours on Box/Text do not render** (the app draws its own colours): record it, commit it, do Task 1, then **stop and ask the user** before Task 2a. The spec's Text treatments assume raw colours render.
- Otherwise continue.

- [ ] **Step 6: Record S0 in the spec and commit**

In `docs/specs/2026-10-08-desktop-mods-design.md`:
- Directly under the §3 table, add a block:
  ```markdown
  **S0 results (2026-10-0x, desktop app <version>, bundled Claude Code <version>):**
  - `AbovePrompt` / `Pane` on desktop: <placed | not placed>.
  - Hex on Box/Text, `round` borders, painted Box backgrounds: <what rendered>.
  - Theme row vs app appearance: <follows | does not follow (stays "<value>")>.
  ```
- In §10 (replace "(Empty.)"):
  ```markdown
  - 2026-10-0x (S0): px per cell on desktop = <n> (ruler: inner width <px> / bodyColumns <cols>); `DESKTOP_PX_PER_CELL` starts at <n>.
  - 2026-10-0x (S0): pane roots <paint RR bg-page | leave the app's ground> on desktop (<why, from the screenshots>); `DESKTOP_PAINTS_PAGE` = <true | false>.
  - 2026-10-0x (S0): native buttons <show | do not show> their hotkey; keys <do | do not> press them.
  - 2026-10-0x (S0): mods load in the app through <route> with separator `<sep>`.
  ```
- In §11, append ` → S0: <answer>` to each of items 1–5.
- If the theme row does not follow the app appearance, add to §5.6: "S0: it does not; on desktop set polarity explicitly."

Then:
```bash
git -C C:/Users/gruku/Files/Claude/taskmaster add docs/specs/2026-10-08-desktop-mods-design.md
git -C C:/Users/gruku/Files/Claude/taskmaster commit -m "docs(spec): desktop S0 baseline — <one-line summary of the findings>"
```
Update the auto-memory `claude-code-mods-facts.md` "Desktop surface" section with the probe-verified facts, marked "(S0, live)". If Step 3 used the user-level variable, remind the user to remove it.

---

### Task 1: Rename `rr-tui` → `rr-mods` (S1)

**Spec:** §4.

**Files:**
- Rename: `mods/rr-tui/` → `mods/rr-mods/` (`git mv`; the git-ignored `.claude-plugin/types/` and `scripts/__pycache__/` move with the folder).
- Modify, in `mods/rr-mods/` (every `rr-tui` string):
  - `.claude-plugin/plugin.json` (name, description);
  - `types/index.d.ts` (header, `PluginState` key);
  - `hooks/register.tsx:1,12,13,42,47`;
  - `hooks/polarity.ts:25`;
  - `hooks/tokens.ts:1-2` (regenerated);
  - `scripts/gen_tokens.py:1,3,5,6,147,148`;
  - `scripts/test_gen_tokens.py:2`;
  - `tests/fixtures/inputs.ts:1`, `tests/fixtures/world.ts:1`, `tests/kit.test.ts:1`;
  - `tests/noun.test.ts` (mount plugin name and state keys, 13 places).
- Modify, in `mods/taskmaster-mods/`:
  - `.claude-plugin/plugin.json` (`dependencies`, description);
  - `hooks/register.tsx:44`;
  - `hooks/rr.ts:1`;
  - `tests/fixtures/rr-stub.ts:2,6`.
- Modify: `docs/specs/2026-10-05-tui-mods-design.md`, `docs/superpowers/plans/2026-10-05-tui-mods.md` (wording plus a rename note).
- Append (git-ignored, not committed): `.superpowers/sdd/2026-10-05-tui-mods/progress.md`.
- Deliberately unchanged:
  - `mods/taskmaster-mods/hooks/demo.ts:114` and `tests/fixtures/replies.ts:31`: sample decision titles about a past decision, which are data, not names.
  - `docs/specs/2026-10-05-tui-mods-references.md`: history.
  - `docs/specs/2026-10-08-desktop-mods-design.md` §4: it names the old name on purpose.
  - Everything under `.worktrees/`.

**Interfaces:**
- Consumes: nothing.
- Produces, for Tasks 2a–5:
  - plugin name `rr-mods`;
  - state keys `{ plugin: 'rr-mods', key: 'polarity' | 'override' }` (`PluginState['rr-mods']`);
  - `taskmaster-mods` `dependencies: ["rr-mods"]`;
  - stub plugin name `rr-mods`;
  - folder `mods/rr-mods`.

`$.rr` and `/rr-gallery` keep their names. A polarity override saved under `rr-tui` resets once (spec §4).

- [ ] **Step 1: Confirm nothing holds the folder and the tree is clean**

Ask the user to close any Claude Code session (terminal or desktop) that loads `mods\rr-tui` (on Windows a watched folder can refuse the rename). Then run:
```bash
git -C C:/Users/gruku/Files/Claude/taskmaster status --short
```
Expected: no output.

- [ ] **Step 2: Move the folder and rewrite the names**

```bash
git -C C:/Users/gruku/Files/Claude/taskmaster mv mods/rr-tui mods/rr-mods
git -C C:/Users/gruku/Files/Claude/taskmaster grep -l 'rr-tui' -- mods ':!mods/taskmaster-mods/hooks/demo.ts' ':!mods/taskmaster-mods/tests/fixtures/replies.ts' | sed 's|^|C:/Users/gruku/Files/Claude/taskmaster/|' | xargs sed -i 's/rr-tui/rr-mods/g'
sed -i 's/rr-tui/rr-mods/g' C:/Users/gruku/Files/Claude/taskmaster/docs/specs/2026-10-05-tui-mods-design.md C:/Users/gruku/Files/Claude/taskmaster/docs/superpowers/plans/2026-10-05-tui-mods.md
```

Then edit by hand (Edit tool):
- `mods/rr-mods/.claude-plugin/plugin.json`: `"description"` becomes `"Reality Reprojection for mods: $.rr gives RR tokens and finished element trees for the active polarity; /rr-gallery tunes the look live."`
- `mods/rr-mods/types/index.d.ts` line 1: `// User intent: the $.rr contract — Reality Reprojection for terminal mods: tokens for the active polarity and finished,` becomes `// User intent: the $.rr contract — Reality Reprojection for mods: tokens for the active polarity and finished,`
- `mods/taskmaster-mods/.claude-plugin/plugin.json`: `"description"` becomes `"Taskmaster in Claude Code: review queue, this session's current task above the prompt, handovers pane, faults-only status line."`
- `docs/specs/2026-10-05-tui-mods-design.md`: after the `**Platform:**` line (line 8), add a line:
  ```markdown
  **Renamed 2026-10-08:** the RR mod `rr-tui` is now `rr-mods` (`docs/specs/2026-10-08-desktop-mods-design.md` §4); this document uses the new name.
  ```
- `docs/superpowers/plans/2026-10-05-tui-mods.md`: after the `**Spec:**` line (line 19), add the same line.

Append to the git-ignored ledger:
```bash
printf '\n- 2026-10-08: rr-tui renamed rr-mods (desktop spec §4, plan docs/superpowers/plans/2026-10-08-desktop-mods.md Task 1); entries above keep the old name.\n' >> C:/Users/gruku/Files/Claude/taskmaster/.superpowers/sdd/2026-10-05-tui-mods/progress.md
```

- [ ] **Step 3: Regenerate the token table and re-lay the engine types**

Run:
```bash
python C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods/scripts/gen_tokens.py
python C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods/scripts/gen_tokens.py --check
env -u ANTHROPIC_API_KEY claude -p --model haiku --plugin-dir C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods --plugin-dir C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods "Reply with the single word ok."
ls C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods/.claude-plugin/types
```
Expected:
- `--check` exits 0, and the `tokens.ts` header names `mods/rr-mods/...`.
- The `-p` run prints `ok`.
- `types` lists `rr-mods/`.

If a stale `rr-tui/` is listed too, remove that engine-written link (git-ignored output, not a tracked file):
```bash
rm C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods/.claude-plugin/types/rr-tui/index.d.ts
rmdir C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods/.claude-plugin/types/rr-tui
```

- [ ] **Step 4: Run every check**

```bash
python -m unittest discover -s C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods/scripts -p "test_*.py"
claude plugin test C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods
claude plugin test C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods
claude plugin validate --strict C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods
claude plugin validate --strict C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods
npx -y -p typescript@5.6.3 tsc -p C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods
npx -y -p typescript@5.6.3 tsc -p C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods
git -C C:/Users/gruku/Files/Claude/taskmaster grep -n 'rr-tui' -- mods docs/specs/2026-10-05-tui-mods-design.md docs/superpowers/plans/2026-10-05-tui-mods.md
```
Expected:
- unittest: `Ran 12 tests … OK`.
- `claude plugin test`: rr-mods **24 pass**, taskmaster-mods **196 pass**.
- Both validations ✔. rr-mods lists `state writes: rr-mods.override, rr-mods.polarity`.
- `tsc`: no output.
- The final grep prints exactly four lines: the two `**Renamed 2026-10-08:**` notes, `mods/taskmaster-mods/hooks/demo.ts:114` and `mods/taskmaster-mods/tests/fixtures/replies.ts:31`.

- [ ] **Step 5: Commit**

```bash
git -C C:/Users/gruku/Files/Claude/taskmaster add mods/rr-mods mods/taskmaster-mods docs/specs/2026-10-05-tui-mods-design.md docs/superpowers/plans/2026-10-05-tui-mods.md
git -C C:/Users/gruku/Files/Claude/taskmaster status --short
git -C C:/Users/gruku/Files/Claude/taskmaster commit -m "refactor(rr-mods): rename rr-tui to rr-mods (folder, plugin name, state keys, dependency, descriptions, docs)"
```
`status --short` must show only renames (`R  mods/rr-tui/… -> mods/rr-mods/…`) and `M` lines for the files listed above, all staged.

Orchestrator, after the commit:
- Tell the user their `CLAUDE_CODE_PLUGIN_DIRS` now names `mods\rr-mods`, and that a pinned polarity resets once.
- Update the auto-memory index entries that say `rr-tui`.

---

### Task 2a: `$.rr` on the desktop — contract, SVG pieces, per-surface kit, noun (S2, part 1)

**Spec:** §5.1, §5.2, §5.3, §5.6, §7 "rr-mods desktop".

**Files:**
- Create: `mods/rr-mods/tests/fixtures/golden.ts`
- Create: `mods/rr-mods/tests/terminal.test.ts`
- Create: `mods/rr-mods/hooks/svg.ts`
- Create: `mods/rr-mods/tests/fixtures/svg.ts`
- Create: `mods/rr-mods/tests/desktop.test.ts`
- Create: `mods/rr-mods/tests/fixtures/desktop-consumer.tsx`
- Create: `mods/rr-mods/tests/desktop-noun.test.ts`
- Modify: `mods/rr-mods/types/index.d.ts` (whole file)
- Modify: `mods/rr-mods/hooks/kit.ts` (whole file)
- Modify: `mods/rr-mods/hooks/register.tsx:1-2` (header), `:49-62` (refuse list), after `:77` (two hooks)
- Modify: `mods/rr-mods/tests/fixtures/inputs.ts` (add `probePane`)
- Modify: `mods/taskmaster-mods/hooks/register.tsx:93-108` (`rrOf` passthroughs)
- Modify: `mods/taskmaster-mods/tests/fixtures/rr-stub.ts` (terminal `steps`/`checkbox`)

**Interfaces:**
- Consumes:
  - Task 1's names;
  - S0's px per cell and pane-root decision (spec §10 entries "S0").
- Produces, for Tasks 2b and 3. In `mods/rr-mods/types/index.d.ts`:
  - `export type RrSurface = 'terminal' | 'desktop'`
  - `export type RrTerminalButtonProps = { readonly plain: true; readonly hotkey?: string; readonly hover: { readonly bold: true; readonly color: string } }`
  - `export type RrDesktopButtonProps = { readonly hotkey?: string; readonly variant: 'primary' | 'secondary' }`
  - `export type RrButtonProps = RrTerminalButtonProps | RrDesktopButtonProps`
  - `Rr` method signatures:

    | Method | Arguments | Returns |
    |---|---|---|
    | `surface` | `{ level, children, width?, padX?, surface? }` | `Promise<RrNode>` |
    | `surfaceProps` | `{ level, width?, padX?, surface? }` | `Promise<RrBoxProps>` |
    | `label` | `{ text, surface? }` | |
    | `signal` | `{ kind, word, detail?, surface? }` | |
    | `row` | `{ cells, emphasis?, surface? }` | |
    | `rule` | `{ width, surface? }` | |
    | `button` | `{ treatment, tone, on?, strength?, surface? }` | `Promise<RrBoxProps>` |
    | `buttonProps` | `{ key?, primary?: boolean, surface? }` | `Promise<RrButtonProps>` |
    | `keycap` | `{ key, tone, surface? }` | |
    | `chip` | `{ text, tone, strength, on?, surface? }` | |
    | `steps` | `{ count: number; current: number; done: number; cap?: number; surface? }` | `Promise<RrNode>` |
    | `checkbox` | `{ checked: boolean; surface? }` | `Promise<RrNode>` |

  - `hooks/kit.ts` also exports `stepsText(count: number, current: number, cap?: number): string`, `DESKTOP_PAINTS_PAGE: boolean`, and a three-overload `buttonProps`: no `surface` or `'terminal'` → `RrTerminalButtonProps`; `'desktop'` → `RrDesktopButtonProps`; `RrSurface` → `RrButtonProps`.
  - `hooks/svg.ts` exports:
    - constants `DESKTOP_PX_PER_CELL`, `MONO_ADVANCE`, `KEYCAP_ADVANCE`, `SVG_SOURCE_MAX`;
    - `escapeXml(text: string): string`;
    - `type SvgPiece = { source; alt; width; height }`;
    - the builders `signalSvg`, `pillSvg`, `keycapSvg`, `ruleSvg`, `stepsSvg` (returns `SvgPiece | null`) and `checkboxSvg`.
  - Desktop answers:
    - `button()` → `{ flexDirection: 'row' }`;
    - `buttonProps()` → `{ hotkey?, variant }`;
    - `surfaceProps` at a non-page level → the level's ground plus `borderStyle: 'single', borderColor: border.default`;
    - every SVG piece is an element `{ type: 'Svg', props: { source, alt, width, height } }`.
  - `steps` alt format: `` `${current} of ${count}, ${done} done` ``. `checkbox` alts: `'checked'` / `'unchecked'`. `keycap` alt: `` `key ${key}` ``. `rule` alt: `'rule'`. A chip's alt is its text.

- [ ] **Step 1: Pin today's terminal output (before touching any code)**

`mods/rr-mods/tests/fixtures/golden.ts`:

```ts
// User intent: pin terminal output byte for byte while desktop treatments are added beside it — a short fingerprint of a
// tree that ignores property order, so a test can say "unchanged" without storing the whole tree.
export function canon(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canon).join(',')}]`
  if (value !== null && typeof value === 'object') {
    const entries = Object.entries(value as Record<string, unknown>)
      .filter(([, v]) => v !== undefined)
      .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0))
    return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canon(v)}`).join(',')}}`
  }
  return JSON.stringify(value) ?? 'undefined'
}

/** FNV-1a over the canonical text, with its length: equal trees, equal prints. */
export function fingerprint(value: unknown): string {
  const text = canon(value)
  let hash = 0x811c9dc5
  for (let i = 0; i < text.length; i += 1) {
    hash ^= text.charCodeAt(i)
    hash = Math.imul(hash, 0x01000193) >>> 0
  }
  return `${hash.toString(16).padStart(8, '0')}:${text.length}`
}
```

`mods/rr-mods/tests/terminal.test.ts`:

```ts
// User intent: rr-mods' terminal output stays byte for byte what was tuned live while the desktop look is added beside it —
// every kit element and every noun answer, in every polarity, fingerprinted before the desktop work began.
import { describe, expect, test } from 'claude-code/testing'

import * as kit from '../hooks/kit'
import { tokensFor } from '../hooks/polarity'
import type { RrPolarity } from '../types'
import { CONSUMER } from './fixtures/consumer'
import { fingerprint } from './fixtures/golden'
import { command } from './fixtures/inputs'
import { rrWorldOf, stateWorldOf } from './fixtures/world'

const POLARITIES: readonly RrPolarity[] = ['dark', 'light', 'survivalist']
const TONES = ['success', 'warning', 'critical', 'info', 'signature'] as const
const KINDS = ['success', 'warning', 'critical', 'info'] as const
const LEVELS = ['page', 'raised', 'overlay', 'recessed'] as const
const GROUNDS = ['page', 'raised', 'overlay'] as const

/** Every terminal element the kit drew before the desktop work, called as consumers call it (no `surface`). */
function terminalElements(p: RrPolarity): unknown[] {
  const t = tokensFor(p)
  return [
    ...LEVELS.map(level => kit.surface(t, { level, children: ['x'] })),
    ...LEVELS.map(level => kit.surfaceProps(t, { level })),
    kit.surfaceProps(t, { level: 'raised', width: 40, padX: 2 }),
    kit.label(t, { text: 'review' }),
    ...KINDS.flatMap(kind => [kit.signal(t, { kind, word: kind, detail: 'why' }), kit.signal(t, { kind, word: kind })]),
    kit.row(t, { cells: ['a', 'b'], emphasis: 'strong' }),
    kit.row(t, { cells: ['a'], emphasis: 'quiet' }),
    kit.row(t, { cells: ['a', 'b'] }),
    kit.rule(t, { width: 12 }),
    kit.rule(t, { width: 0 }),
    kit.buttonProps(t, { key: 'd' }),
    kit.buttonProps(t, {}),
    ...TONES.flatMap(tone => [
      kit.keycap(t, { key: 'd', tone }),
      kit.button(t, { treatment: 'outline', tone }),
      ...GROUNDS.flatMap(on => [
        kit.button(t, { treatment: 'chip', tone, on }),
        kit.button(t, { treatment: 'chip', tone, on, strength: 24 }),
        kit.chip(t, { text: tone, tone, strength: 12, on }),
        kit.chip(t, { text: tone, tone, strength: 24, on }),
      ]),
    ]),
  ]
}

// Filled from the first run (Step 2): what each polarity drew before the desktop work. Never edited after that.
const KIT_GOLDEN: Record<string, string> = {}
const NOUN_GOLDEN: Record<string, string> = {}

describe('terminal output is pinned', () => {
  test('every kit element in every polarity draws exactly what it drew before the desktop work', () => {
    const prints = Object.fromEntries(POLARITIES.map(p => [`kit.${p}`, fingerprint(terminalElements(p))]))
    expect(prints).toEqual(KIT_GOLDEN)
  })

  test('every $.rr answer through the noun is unchanged in every polarity', { plugins: [CONSUMER] }, async ($, on) => {
    rrWorldOf(on, 'dark')
    const state = stateWorldOf(on)
    const prints: Record<string, string> = {}
    for (const p of POLARITIES) {
      state.values.set('rr-mods.override', p)
      prints[`noun.${p}`] = fingerprint(JSON.parse((await $.command.run(command('rr-probe-all'))).text ?? '{}'))
    }
    expect(prints).toEqual(NOUN_GOLDEN)
  })
})
```

- [ ] **Step 2: Run it, read the prints, fill the goldens**

Run: `claude plugin test C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods`
Expected: **2 fail**. Each failure's "Received" is an object of three prints, for example `{ "kit.dark": "1a2b3c4d:51234", … }`.

Copy each received object verbatim into `KIT_GOLDEN` and `NOUN_GOLDEN`. Run again. Expected: **26 pass**.

- [ ] **Step 3: Commit the pins on their own**

```bash
git -C C:/Users/gruku/Files/Claude/taskmaster add mods/rr-mods/tests/fixtures/golden.ts mods/rr-mods/tests/terminal.test.ts
git -C C:/Users/gruku/Files/Claude/taskmaster commit -m "test(rr-mods): pin every terminal element and noun answer before the desktop treatments"
```

- [ ] **Step 4: Write the failing desktop tests**

`mods/rr-mods/tests/fixtures/svg.ts`:

```ts
// User intent: what makes a desktop $.rr piece safe to hand the desktop — one escaped SVG document with an alt and a px size,
// no theme-dependent colour, nothing interactive — checked the same way by every test.
import { SVG_SOURCE_MAX } from '../../hooks/svg'

export type SvgFound = Readonly<Record<string, unknown>>

/** Every Svg element's props in a built or drawn tree, outermost first. */
export function svgsOf(node: unknown): SvgFound[] {
  if (node === null || typeof node !== 'object') return []
  const n = node as { type?: unknown; props?: SvgFound; children?: unknown[] }
  const own = n.type === 'Svg' && n.props !== undefined ? [n.props] : []
  return [...own, ...(n.children ?? []).flatMap(svgsOf)]
}

/** One piece read as an Svg; throws, naming what it was instead, when it is not one. */
export function svgProps(node: unknown): { source: string; alt: string; width: number; height: number } {
  const n = node as { type?: unknown; props?: Record<string, unknown> }
  if (n.type !== 'Svg' || n.props === undefined) throw new Error(`not an Svg: ${JSON.stringify(node)}`)
  return { source: String(n.props.source), alt: String(n.props.alt), width: Number(n.props.width), height: Number(n.props.height) }
}

/** What is wrong with an Svg's props under spec §5.3; empty when nothing is. */
export function svgFaults(props: SvgFound): string[] {
  const faults: string[] = []
  const { source, alt, width, height, isInteractive } = props
  if (typeof alt !== 'string' || alt.trim() === '') faults.push('no alt')
  if (typeof width !== 'number' || !(width > 0) || typeof height !== 'number' || !(height > 0)) faults.push('no px size')
  if (isInteractive !== undefined) faults.push('isInteractive set')
  if (typeof source !== 'string') return [...faults, 'no source']
  if (source.length > SVG_SOURCE_MAX) faults.push(`source is ${source.length} chars`)
  if (!source.startsWith('<svg xmlns="http://www.w3.org/2000/svg" ') || !source.endsWith('</svg>')) faults.push('not one svg document')
  if (/currentColor|var\(/.test(source)) faults.push('theme-dependent colour')
  if (/<script|\son[a-z]+=/i.test(source)) faults.push('script or event attribute')
  if (/[<>]/.test(source.replace(/<[^<>]*>/g, ''))) faults.push('unescaped < or > in text')
  if (/&(?!amp;|lt;|gt;|quot;|apos;)/.test(source)) faults.push('unescaped &')
  return faults
}
```

`mods/rr-mods/tests/desktop.test.ts`:

```ts
// User intent: pin $.rr's desktop pieces — SVG shapes built from the RR table for the active polarity, escaped, sized from
// named constants, hue-free in survivalist, degenerate strips safe — and that terminal stays the default, so the desktop
// look can be tuned live without touching the terminal.
import { describe, expect, test } from 'claude-code/testing'

import * as kit from '../hooks/kit'
import { tokensFor } from '../hooks/polarity'
import { DESKTOP_PX_PER_CELL, escapeXml, MONO_ADVANCE } from '../hooks/svg'
import { RR_TABLE } from '../hooks/tokens'
import type { RrPolarity, RrStrength } from '../types'
import { svgFaults, svgProps, svgsOf } from './fixtures/svg'

const POLARITIES: readonly RrPolarity[] = ['dark', 'light', 'survivalist']
const TONES = ['success', 'warning', 'critical', 'info', 'signature'] as const
const KINDS = ['success', 'warning', 'critical', 'info'] as const
const LEVELS = ['page', 'raised', 'overlay', 'recessed'] as const
const GROUNDS = ['page', 'raised', 'overlay'] as const
const STRENGTHS: readonly RrStrength[] = [12, 24]
const desktop = 'desktop' as const
const terminal = 'terminal' as const

function textOf(node: unknown): string {
  if (typeof node === 'string') return node
  if (node === null || typeof node !== 'object') return ''
  return ((node as { children?: unknown[] }).children ?? []).map(textOf).join('')
}

const hexes = (source: string): string[] => source.match(/#[0-9a-f]{6}/g) ?? []
const occurrences = (source: string, needle: string): number => source.split(needle).length - 1

/** Every desktop piece of every method: 4 signals, 1 rule, 5 keycaps, 30 pills, 2 strips, 2 checkboxes = 44 Svgs. */
function desktopPieces(p: RrPolarity): unknown[] {
  const t = tokensFor(p)
  return [
    ...LEVELS.map(level => kit.surface(t, { level, children: ['x'], surface: desktop })),
    kit.label(t, { text: 'review', surface: desktop }),
    ...KINDS.map(kind => kit.signal(t, { kind, word: kind, detail: 'why', surface: desktop })),
    kit.row(t, { cells: ['a'], surface: desktop }),
    kit.rule(t, { width: 12, surface: desktop }),
    ...TONES.flatMap(tone => [
      kit.keycap(t, { key: 'd', tone, surface: desktop }),
      ...GROUNDS.flatMap(on => STRENGTHS.map(strength => kit.chip(t, { text: tone, tone, strength, on, surface: desktop }))),
    ]),
    kit.steps(t, { count: 5, current: 2, done: 1, surface: desktop }),
    kit.steps(t, { count: 355, current: 2, done: 1, surface: desktop }),
    kit.checkbox(t, { checked: false, surface: desktop }),
    kit.checkbox(t, { checked: true, surface: desktop }),
  ]
}

describe('desktop kit', () => {
  test('terminal is the default: an explicit terminal surface draws exactly what no surface draws', () => {
    for (const p of POLARITIES) {
      const t = tokensFor(p)
      const pairs: readonly (readonly [unknown, unknown])[] = [
        [kit.surface(t, { level: 'raised', children: ['x'] }), kit.surface(t, { level: 'raised', children: ['x'], surface: terminal })],
        [kit.surfaceProps(t, { level: 'page' }), kit.surfaceProps(t, { level: 'page', surface: terminal })],
        [kit.label(t, { text: 'review' }), kit.label(t, { text: 'review', surface: terminal })],
        [kit.signal(t, { kind: 'info', word: 'note', detail: 'why' }), kit.signal(t, { kind: 'info', word: 'note', detail: 'why', surface: terminal })],
        [kit.row(t, { cells: ['a', 'b'], emphasis: 'quiet' }), kit.row(t, { cells: ['a', 'b'], emphasis: 'quiet', surface: terminal })],
        [kit.rule(t, { width: 7 }), kit.rule(t, { width: 7, surface: terminal })],
        [
          kit.button(t, { treatment: 'chip', tone: 'warning', on: 'raised', strength: 24 }),
          kit.button(t, { treatment: 'chip', tone: 'warning', on: 'raised', strength: 24, surface: terminal }),
        ],
        [kit.buttonProps(t, { key: 'd' }), kit.buttonProps(t, { key: 'd', primary: true, surface: terminal })],
        [kit.keycap(t, { key: 'd', tone: 'success' }), kit.keycap(t, { key: 'd', tone: 'success', surface: terminal })],
        [kit.chip(t, { text: 'refused', tone: 'critical', strength: 24 }), kit.chip(t, { text: 'refused', tone: 'critical', strength: 24, surface: terminal })],
        [kit.steps(t, { count: 5, current: 1, done: 0 }), kit.steps(t, { count: 5, current: 1, done: 0, surface: terminal })],
        [kit.checkbox(t, { checked: true }), kit.checkbox(t, { checked: true, surface: terminal })],
      ]
      for (const [bare, explicit] of pairs) expect(explicit).toEqual(bare)
    }
  })

  test('every desktop piece in every polarity is a clean SVG: alt, px size, under the cap, no theme colours, nothing interactive', () => {
    for (const p of POLARITIES) {
      const svgs = desktopPieces(p).flatMap(svgsOf)
      expect(svgs, p).toHaveLength(44)
      for (const s of svgs) expect(svgFaults(s), `${p} ${String(s.alt)}`).toEqual([])
    }
  })

  test('server text in a pill or keycap is XML-escaped; the alt keeps it raw', () => {
    expect(escapeXml(`&<>"'`)).toBe('&amp;&lt;&gt;&quot;&apos;')
    const t = tokensFor('dark')
    const raw = `gate & <b>"x"</b> 'y'`
    const pill = svgProps(kit.chip(t, { text: raw, tone: 'warning', strength: 12, surface: desktop }))
    expect(pill.alt).toBe(raw)
    expect(pill.source).toContain('gate &amp; &lt;b&gt;&quot;x&quot;&lt;/b&gt; &apos;y&apos;')
    expect(svgFaults(pill)).toEqual([])
    const cap = svgProps(kit.keycap(t, { key: '<', tone: 'signature', surface: desktop }))
    expect(cap.source).toContain('>&lt;</text>')
    expect(svgFaults(cap)).toEqual([])
  })

  test('survivalist SVGs carry no hue: every colour in them is a ground value (Review Focus 5)', () => {
    const grounds = new Set(Object.entries(RR_TABLE.survivalist).filter(([k]) => /^ground-\d+$/.test(k)).map(([, v]) => v))
    for (const s of desktopPieces('survivalist').flatMap(svgsOf)) {
      for (const hex of hexes(String(s.source))) expect(grounds.has(hex), `${String(s.alt)} ${hex}`).toBe(true)
    }
  })

  test('steps: +N past the cap, nothing for an empty queue, clamps a current past the end and done past current (Review Focus 2)', () => {
    const t = tokensFor('dark')
    const strip = (count: number, current: number, done: number) => kit.steps(t, { count, current, done, surface: desktop })
    const five = svgProps(strip(5, 2, 1))
    expect(five.alt).toBe('2 of 5, 1 done')
    expect(five.width).toBe(104) // 5 × 18 + 4 × 3.5: the prototype's strip
    expect(occurrences(five.source, '<rect ')).toBe(5)
    expect(occurrences(five.source, `fill="${t.tone.success}"`)).toBe(1)
    expect(occurrences(five.source, `fill="${t.tone.signature}"`)).toBe(1)
    const big = svgProps(strip(355, 2, 1))
    expect(big.alt).toBe('2 of 355, 1 done')
    expect(occurrences(big.source, '<rect ')).toBe(10)
    expect(big.source).toContain('>+345</text>')
    for (const [count, current, done] of [
      [0, 0, 0],
      [-3, 1, 0],
      [Number.NaN, 1, 0],
    ] as const) {
      expect(svgsOf(strip(count, current, done)), `${count}`).toEqual([])
      expect(strip(count, current, done)).toEqual(kit.steps(t, { count, current, done }))
    }
    expect(svgProps(strip(15, 12, 3)).source).not.toContain(`fill="${t.tone.signature}"`)
    expect(occurrences(svgProps(strip(5, 2, 9)).source, `fill="${t.tone.success}"`)).toBe(1)
    const surv = tokensFor('survivalist')
    expect(surv.tone.success).toBe(surv.tone.signature)
    const quiet = svgProps(kit.steps(surv, { count: 5, current: 3, done: 2, surface: desktop })).source
    expect(occurrences(quiet, `fill="${surv.tone.signature}"`)).toBe(1)
    expect(occurrences(quiet, `fill="${surv.fg.subtle}"`)).toBe(2)
  })

  test('the terminal strip is the ●○ +N text taskmaster-mods drew, in signature-text', () => {
    expect(kit.stepsText(5, 1)).toBe('●○○○○')
    expect(kit.stepsText(5, 3)).toBe('●●●○○')
    expect(kit.stepsText(355, 2)).toBe('●●○○○○○○○○+345')
    expect(kit.stepsText(15, 12)).toBe('●●●●●●●●●●+5')
    const t = tokensFor('light')
    const strip = kit.steps(t, { count: 5, current: 1, done: 0 }) as unknown as { type: string; props: { color: string } }
    expect(strip.type).toBe('Text')
    expect(strip.props.color).toBe(t.signatureText)
    expect(textOf(strip)).toBe('●○○○○')
  })

  test('buttons on the desktop: native variant (primary only when asked), a neutral row wrapper for every treatment', () => {
    for (const p of POLARITIES) {
      const t = tokensFor(p)
      expect(kit.buttonProps(t, { key: 'd', primary: true, surface: desktop })).toEqual({ hotkey: 'd', variant: 'primary' })
      expect(kit.buttonProps(t, { key: 'a', surface: desktop })).toEqual({ hotkey: 'a', variant: 'secondary' })
      expect(kit.buttonProps(t, { surface: desktop })).toEqual({ variant: 'secondary' })
      for (const treatment of ['outline', 'chip'] as const) {
        expect(kit.button(t, { treatment, tone: 'success', strength: 24, surface: desktop })).toEqual({ flexDirection: 'row' })
      }
    }
  })

  test('surfaces on the desktop: the level ground inside a full border-default border; a page root is a ground, never a card', () => {
    for (const p of POLARITIES) {
      const t = tokensFor(p)
      for (const level of ['raised', 'overlay', 'recessed'] as const) {
        expect(kit.surfaceProps(t, { level, surface: desktop })).toEqual({
          flexDirection: 'column',
          backgroundColor: t.surface[level],
          paddingX: 1,
          borderStyle: 'single',
          borderColor: t.border.default,
        })
      }
      const page = kit.surfaceProps(t, { level: 'page', surface: desktop })
      expect(page.borderStyle).toBeUndefined()
      expect(page).toEqual(kit.DESKTOP_PAINTS_PAGE ? kit.surfaceProps(t, { level: 'page' }) : { flexDirection: 'column', paddingX: 1 })
      const card = kit.surface(t, { level: 'overlay', children: ['x'], surface: desktop }) as unknown as { props: Record<string, unknown> }
      expect(card.props).toMatchObject({ backgroundColor: t.surface.overlay, borderStyle: 'single', borderColor: t.border.default })
    }
  })

  test('sizes come from the named constants: a rule is cells × DESKTOP_PX_PER_CELL, a pill grows MONO_ADVANCE per character', () => {
    const t = tokensFor('dark')
    expect(svgProps(kit.rule(t, { width: 12, surface: desktop })).width).toBe(Math.round(12 * DESKTOP_PX_PER_CELL))
    expect(svgsOf(kit.rule(t, { width: 0, surface: desktop }))).toEqual([])
    const short = svgProps(kit.chip(t, { text: 'refused', tone: 'critical', strength: 24, surface: desktop }))
    const long = svgProps(kit.chip(t, { text: 'refused twice', tone: 'critical', strength: 24, surface: desktop }))
    expect(Math.abs(long.width - short.width - 6 * MONO_ADVANCE)).toBeLessThanOrEqual(1)
    expect(short.height).toBe(20)
  })

  test('a desktop signal is the SVG shape in the tone colour, then the word (bold) and the detail as text', () => {
    const t = tokensFor('dark')
    const tree = kit.signal(t, { kind: 'critical', word: 'refused', detail: 'outstanding gates', surface: desktop }) as unknown as {
      type: string
      children: { type: string; props: Record<string, unknown> }[]
    }
    expect(tree.type).toBe('Box')
    expect(tree.children.map(c => c.type)).toEqual(['Svg', 'Text', 'Text'])
    expect(tree.children[0]?.props).toMatchObject({ alt: 'critical', width: 11, height: 11 })
    expect(String(tree.children[0]?.props.source)).toContain(`fill="${t.tone.critical}"`)
    expect(tree.children[1]?.props).toMatchObject({ color: t.fg.bold, bold: true })
    expect(textOf(tree)).toBe('refusedoutstanding gates')
  })

  test('checkbox: terminal ☐ / ☑ text; desktop an outline, or a success fill with a tick', () => {
    const t = tokensFor('dark')
    expect(textOf(kit.checkbox(t, { checked: false }))).toBe('☐')
    expect(textOf(kit.checkbox(t, { checked: true }))).toBe('☑')
    const open = svgProps(kit.checkbox(t, { checked: false, surface: desktop }))
    const ticked = svgProps(kit.checkbox(t, { checked: true, surface: desktop }))
    expect([open.alt, ticked.alt]).toEqual(['unchecked', 'checked'])
    expect(open.source).toContain(`fill="none" stroke="${t.border.strong}"`)
    expect(ticked.source).toContain(`fill="${t.tone.success}" stroke="${t.tone.success}"`)
    expect(ticked.source).toContain('<path ')
  })
})
```

`mods/rr-mods/tests/fixtures/inputs.ts`: append:

```ts
/** A pane a test plugin draws, sized like the gallery's. */
export const probePane = (requestId: string): RenderInput<'Pane'> => ({
  ...GALLERY_PANE,
  requestId,
  props: { ...GALLERY_PANE.props, title: requestId },
})
```

`mods/rr-mods/tests/fixtures/desktop-consumer.tsx`:

```tsx
// User intent: a second plugin that draws every $.rr element through the noun, on the surface its pane is drawn on (or
// forced to the desktop), so tests prove desktop trees validate on the desktop and never reach a terminal mount.
import type { EngineInterface, RenderNode } from 'claude-code'
import type { Plugin } from 'claude-code/testing'

import type { RrSurface } from '../../types'

export const PROBE = 'rr-probe-pane'
export const FORCED = 'rr-probe-forced'
const TONES = ['success', 'warning', 'critical', 'info', 'signature'] as const
const KINDS = ['success', 'warning', 'critical', 'info'] as const
const LEVELS = ['page', 'raised', 'overlay', 'recessed'] as const
const GROUNDS = ['page', 'raised', 'overlay'] as const
const STRIPS = [
  [5, 2, 1],
  [355, 2, 1],
  [0, 0, 0],
  [15, 12, 3],
] as const

/** 8 signals, 1 rule (width 0 draws text), 5 keycaps, 30 pills, 3 strips (the empty queue draws text), 2 checkboxes. */
async function everyPiece($: EngineInterface, surface: RrSurface): Promise<RenderNode[]> {
  const out: unknown[] = []
  for (const level of LEVELS) out.push(await $.rr.surface({ level, children: [level], surface }))
  out.push(await $.rr.label({ text: 'review', surface }))
  for (const kind of KINDS) {
    out.push(await $.rr.signal({ kind, word: kind, detail: 'why', surface }))
    out.push(await $.rr.signal({ kind, word: kind, surface }))
  }
  out.push(await $.rr.row({ cells: ['a', 'b'], emphasis: 'quiet', surface }))
  out.push(await $.rr.rule({ width: 12, surface }))
  out.push(await $.rr.rule({ width: 0, surface }))
  for (const tone of TONES) {
    out.push(await $.rr.keycap({ key: 'd', tone, surface }))
    for (const strength of [12, 24] as const) {
      for (const on of GROUNDS) out.push(await $.rr.chip({ text: `${tone} & <x>`, tone, strength, on, surface }))
    }
  }
  for (const [count, current, done] of STRIPS) out.push(await $.rr.steps({ count, current, done, surface }))
  out.push(await $.rr.checkbox({ checked: false, surface }))
  out.push(await $.rr.checkbox({ checked: true, surface }))
  return out as RenderNode[]
}

/** One recipe button per tone: `success` is the primary, armed with `d`. */
async function buttonParts($: EngineInterface, surface: RrSurface) {
  return Promise.all(
    TONES.map(async tone => ({
      tone,
      wrap: await $.rr.button({ treatment: 'chip', tone, surface }),
      press: await $.rr.buttonProps({ ...(tone === 'success' ? { key: 'd', primary: true } : {}), surface }),
    })),
  )
}

export const DESKTOP_CONSUMER: Plugin = {
  name: 'rr-probe-desktop',
  register(on) {
    on('ui.render', { component: 'Pane', requestId: PROBE }, async ($, e) => {
      const { Box, Button } = $.ui.resolve(e)
      const surface: RrSurface = e.surface === 'desktop' ? 'desktop' : 'terminal'
      const pieces = await everyPiece($, surface)
      const buttons = await buttonParts($, surface)
      return (
        <Box flexDirection="column">
          {pieces}
          {buttons.map(b => (
            <Box key={`btn-${b.tone}-box`} {...b.wrap}>
              <Button key={`btn-${b.tone}`} {...b.press} label={b.tone} onPress={() => $.ui.toast(`pressed ${b.tone}`)} />
            </Box>
          ))}
        </Box>
      )
    })
    on('ui.render', { component: 'Pane', requestId: FORCED }, async ($, e) => {
      const { Box } = $.ui.resolve(e)
      return <Box flexDirection="column">{await everyPiece($, 'desktop')}</Box>
    })
  },
}
```

`mods/rr-mods/tests/desktop-noun.test.ts`:

```ts
// User intent: prove $.rr's desktop trees validate where they are drawn — through the noun as other mods see it, in every
// polarity — and never reach a terminal mount.
import { describe, expect, test } from 'claude-code/testing'

import type { RrPolarity } from '../types'
import { DESKTOP_CONSUMER, FORCED, PROBE } from './fixtures/desktop-consumer'
import { probePane } from './fixtures/inputs'
import { svgFaults } from './fixtures/svg'
import { rrWorldOf, stateWorldOf } from './fixtures/world'

const POLARITIES: readonly RrPolarity[] = ['dark', 'light', 'survivalist']
const CONSUMER_NAME = 'rr-probe-desktop'

describe('$.rr on the desktop', () => {
  test('every element, tone, kind and level validates on the desktop through the noun in every polarity; presses work', { plugins: [DESKTOP_CONSUMER] }, async ($, on) => {
    const world = rrWorldOf(on, 'dark')
    const state = stateWorldOf(on)
    for (const polarity of POLARITIES) {
      state.values.set('rr-mods.override', polarity)
      const ui = await $.ui.mount({ plugin: CONSUMER_NAME, ...probePane(PROBE), surface: 'desktop' })
      const svgs = await ui.findAll({ type: 'Svg' })
      expect(svgs, polarity).toHaveLength(49)
      for (const s of svgs) expect(svgFaults(s.props), `${polarity} ${String(s.props.alt)}`).toEqual([])
      expect((await ui.find({ type: 'Button', key: 'btn-success' }))?.props).toMatchObject({ hotkey: 'd', label: 'success', variant: 'primary' })
      expect((await ui.find({ type: 'Button', key: 'btn-info' }))?.props).toMatchObject({ label: 'info', variant: 'secondary' })
      expect((await ui.find({ type: 'Button', key: 'btn-info' }))?.props.plain).toBeUndefined()
      expect((await ui.find({ type: 'Box', key: 'btn-info-box' }))?.props.backgroundColor).toBeUndefined()
      await ui.press({ key: 'btn-warning' })
      await ui.unmount()
    }
    expect(world.toasts).toEqual(['pressed warning', 'pressed warning', 'pressed warning'])
  })

  test('a desktop tree never reaches a terminal mount: the same pane draws terminal trees there, and a forced desktop tree is refused', { plugins: [DESKTOP_CONSUMER] }, async ($, on) => {
    rrWorldOf(on, 'dark')
    const terminal = await $.ui.mount({ plugin: CONSUMER_NAME, ...probePane(PROBE) })
    expect(await terminal.findAll({ type: 'Svg' })).toEqual([])
    expect((await terminal.find({ type: 'Button', key: 'btn-success' }))?.props).toMatchObject({ plain: true, hotkey: 'd' })
    await terminal.unmount()
    const refused = await $.ui
      .mount({ plugin: CONSUMER_NAME, ...probePane(FORCED) })
      .then(ui => ui.drawn())
      .then(
        () => false,
        () => true,
      )
    expect(refused).toBe(true)
  })
})
```

If `refused` comes out `false`, do not weaken the test. The engine then draws a desktop tree on a terminal without refusing it, which contradicts spec §3 row 2. Report it.

- [ ] **Step 5: Run the new tests to see them fail**

Run: `claude plugin test C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods`
Expected: FAIL. `desktop.test.ts` cannot load `../hooks/svg` (module not found). `desktop-noun.test.ts` fails with `$.rr.steps is not a function` or a validation error. The 26 earlier tests still pass.

- [ ] **Step 6: Write the contract — replace `mods/rr-mods/types/index.d.ts` whole**

```ts
// User intent: the $.rr contract — Reality Reprojection for mods on the terminal and the desktop Code tab: tokens for the
// active polarity and finished, non-interactive element trees per surface that any mod places in its own drawing. Buttons
// stay with the consumer: they can't cross a noun.
export type RrPolarity = 'dark' | 'light' | 'survivalist'
export type RrTone = 'success' | 'warning' | 'critical' | 'info' | 'signature'
export type RrSignalKind = 'success' | 'warning' | 'critical' | 'info'
/**
 * Where a tree is drawn. `'terminal'` (the default when absent) is the look tuned live in Windows Terminal; `'desktop'` draws
 * SVG pieces and native-button props. A tree holding an Svg fails validation on a terminal, so pass the surface of the site
 * you draw (`e.surface === 'desktop' ? 'desktop' : 'terminal'`). VS Code takes the terminal trees.
 */
export type RrSurface = 'terminal' | 'desktop'
/**
 * A surface step. `'page'` paints RR bg-page and is for pane roots only: a pane paints its own ground because the host's pane
 * background is not RR's page. The band never paints its ground, so a band never uses `'page'`. On the desktop every other
 * level also draws a full border-default border; a page root never does.
 */
export type RrLevel = 'page' | 'raised' | 'overlay' | 'recessed'
export type RrGround = 'page' | 'raised' | 'overlay'
export type RrTreatment = 'outline' | 'chip'
export type RrStrength = 12 | 24
export type RrNode =
  | string
  | { readonly type: string; readonly props?: Readonly<Record<string, unknown>>; readonly children?: readonly unknown[] }
export type RrBoxProps = {
  readonly flexDirection?: 'row' | 'column'
  readonly backgroundColor?: string
  readonly borderStyle?: 'single' | 'round' | 'bold'
  readonly borderColor?: string
  readonly paddingX?: number
  readonly width?: number | string
}
/**
 * RR's terminal button recipe: the whole surface presses. The treatment's wrapper Box (`button()`: a chip's tint on its
 * ground, or the round tone outline for the one primary action; both pad 1 column inside) holds ONE plain Button carrying
 * the label. `buttonProps()` gives that Button its `hotkey` (one digit or one lowercase letter), so the engine draws
 * `d: back to agent`, and a `hover` that turns the label bold `foreground-bold` (style only, no motion). Give the wrapper Box
 * a unique `key`: a Button's `hover` only applies inside a keyed Box. A noun can't hand out a Button or its press handler,
 * so the consumer draws the Button.
 *
 * @example
 * const wrap = await $.rr.button({ treatment: 'chip', tone: 'warning', on: 'raised', surface })
 * const press = await $.rr.buttonProps({ key: 'a', surface })
 * <Box key="back-box" {...wrap}>
 *   <Button key="back" {...press} label="back to agent" onPress={back} />
 * </Box>
 */
export type RrTerminalButtonProps = {
  readonly plain: true
  /** Present when `key` was given. Two Buttons on one hotkey in a site clash (the later wins). */
  readonly hotkey?: string
  readonly hover: { readonly bold: true; readonly color: string }
}
/**
 * The desktop's native button: `variant: 'primary'` for the card's one main action (`primary: true`), else `'secondary'`; no
 * `plain` and no `hover` (the native button has its own colour-only hover). `button({ surface: 'desktop' })` is a neutral
 * row wrapper, so the keyed-wrapper markup is the same on both surfaces.
 */
export type RrDesktopButtonProps = {
  readonly hotkey?: string
  readonly variant: 'primary' | 'secondary'
}
export type RrButtonProps = RrTerminalButtonProps | RrDesktopButtonProps
export type RrTokens = {
  readonly polarity: RrPolarity
  readonly surface: Readonly<Record<RrLevel, string>>
  readonly fg: { readonly bold: string; readonly default: string; readonly subtle: string; readonly disabled: string }
  readonly border: { readonly default: string; readonly subtle: string; readonly strong: string; readonly focus: string }
  readonly signatureText: string
  readonly tone: Readonly<Record<RrTone, string>>
  readonly tint12: Readonly<Record<RrGround, Readonly<Record<RrTone, string>>>>
  readonly tint24: Readonly<Record<RrGround, Readonly<Record<RrTone, string>>>>
  readonly keycap: Readonly<Record<RrTone, { readonly bg: string; readonly ink: string }>>
}
export type Rr = {
  /** Surface-free: one RR table per polarity serves every surface. */
  tokens: () => Promise<RrTokens>
  polarity: () => Promise<RrPolarity>
  surface: (args: { level: RrLevel; children: readonly RrNode[]; width?: number | string; padX?: number; surface?: RrSurface }) => Promise<RrNode>
  surfaceProps: (args: { level: RrLevel; width?: number | string; padX?: number; surface?: RrSurface }) => Promise<RrBoxProps>
  label: (args: { text: string; surface?: RrSurface }) => Promise<RrNode>
  signal: (args: { kind: RrSignalKind; word: string; detail?: string; surface?: RrSurface }) => Promise<RrNode>
  row: (args: { cells: readonly RrNode[]; emphasis?: 'strong' | 'quiet'; surface?: RrSurface }) => Promise<RrNode>
  rule: (args: { width: number; surface?: RrSurface }) => Promise<RrNode>
  /**
   * `strength` (chip only; default 12): 24 is the strong chip, the one primary action of a card (`done`), drawn in the
   * double-strength tint so it reads above the 12% secondaries on one baseline. Survivalist: a bolder grey step. Desktop:
   * a neutral row wrapper for every treatment.
   */
  button: (args: { treatment: RrTreatment; tone: RrTone; on?: RrGround; strength?: RrStrength; surface?: RrSurface }) => Promise<RrBoxProps>
  /** `primary` marks the card's one main action; only the desktop draws it differently (`variant: 'primary'`). */
  buttonProps: (args: { key?: string; primary?: boolean; surface?: RrSurface }) => Promise<RrButtonProps>
  keycap: (args: { key: string; tone: RrTone; surface?: RrSurface }) => Promise<RrNode>
  chip: (args: { text: string; tone: RrTone; strength: RrStrength; on?: RrGround; surface?: RrSurface }) => Promise<RrNode>
  /**
   * The review queue strip: `count` items, `current` (1-based), `done` this pass; past `cap` (default 10) the rest is `+N`.
   * Terminal: `●○ +N` text in signature-text (`done` does not change it). Desktop: an SVG segmented strip, alt
   * `"<current> of <count>, <done> done"`. An empty queue draws empty text on both.
   */
  steps: (args: { count: number; current: number; done: number; cap?: number; surface?: RrSurface }) => Promise<RrNode>
  /** A check item's box. Terminal: `☐` / `☑` text. Desktop: an SVG outline, or a success fill with a tick (alt checked/unchecked). */
  checkbox: (args: { checked: boolean; surface?: RrSurface }) => Promise<RrNode>
}

declare module 'claude-code' {
  interface EngineInterface {
    rr: Rr
  }
  interface PluginState {
    'rr-mods': { polarity: RrPolarity; override: RrPolarity | 'none' }
  }
}
```

- [ ] **Step 7: Write the SVG builders — create `mods/rr-mods/hooks/svg.ts`**

Before writing, read spec §10:
- Set `DESKTOP_PX_PER_CELL` to the S0 value. If S0 recorded none, use 8.
- Set `DESKTOP_PAINTS_PAGE` in Step 8 to the S0 decision. If S0 recorded none, use `true`.

```ts
// User intent: the SVG pieces Reality Reprojection draws on the desktop Code tab — built as strings from the RR table for the
// active polarity (an Svg follows no app theme), every text escaped, sizes from the approved prototype and tuned live at S4.
import type { RrGround, RrSignalKind, RrStrength, RrTokens, RrTone } from '../types'

/** CSS px one layout cell spans on the desktop (its columns are px ÷ the code font's metric). Measured at S0, tuned at S4. */
export const DESKTOP_PX_PER_CELL = 8
/** Advance of the 11 px monospace pill and strip labels, px per character. A guess, tuned at S4. */
export const MONO_ADVANCE = 6.6
/** Advance of the 9.5 px keycap letters, px per character. A guess, tuned at S4. */
export const KEYCAP_ADVANCE = 5.7
/** The platform's cap on an Svg's `source`. */
export const SVG_SOURCE_MAX = 131072

const MONO = 'JetBrains Mono, Consolas, monospace'
// The prototype's strip: 18 x 8 px segments, 3.5 px apart.
const SEGMENT = 18
const SEGMENT_GAP = 3.5
const SEGMENT_HEIGHT = 8

export type SvgPiece = { readonly source: string; readonly alt: string; readonly width: number; readonly height: number }

export function escapeXml(text: string): string {
  return text.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&apos;')
}

function doc(width: number, height: number, body: string, viewBox = `0 0 ${width} ${height}`): string {
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${width}" height="${height}" viewBox="${viewBox}">${body}</svg>`
}

/** A count as a whole number of at least 0; NaN and infinities count as 0. */
const whole = (n: number): number => (Number.isFinite(n) ? Math.max(0, Math.floor(n)) : 0)

/** The prototype's four signal shapes in a 12-unit box: circle, triangle, diamond, ring with an i. */
function shape(kind: RrSignalKind, color: string): string {
  if (kind === 'success') return `<circle cx="6" cy="6" r="5.5" fill="${color}"/>`
  if (kind === 'warning') return `<path d="M6 1 11.5 11H.5Z" fill="${color}"/>`
  if (kind === 'critical') return `<path d="M6 .5 11.5 6 6 11.5.5 6Z" fill="${color}"/>`
  return `<circle cx="6" cy="6" r="5" fill="none" stroke="${color}" stroke-width="1.4"/><path d="M6 5.2v3.6M6 3.3v.2" stroke="${color}" stroke-width="1.4"/>`
}

/** A signal's shape, 11 px, in the tone colour (survivalist tones are greys; the shape carries the kind). */
export function signalSvg(t: RrTokens, kind: RrSignalKind): SvgPiece {
  return { source: doc(11, 11, shape(kind, t.tone[kind]), '0 0 12 12'), alt: kind, width: 11, height: 11 }
}

/**
 * A state pill: the tint (12 or 24) on its ground, a full tone outline, a small tone dot and the label in a monospace face,
 * bold when strong. Survivalist has no hue: a grey-step fill with outline and dot in foreground-bold.
 */
export function pillSvg(t: RrTokens, a: { text: string; tone: RrTone; strength: RrStrength; on?: RrGround }): SvgPiece {
  const width = Math.ceil(20 + [...a.text].length * MONO_ADVANCE + 8)
  const strong = a.strength === 24
  const plain = t.polarity === 'survivalist'
  const fill = plain ? (strong ? t.border.strong : t.surface.raised) : (strong ? t.tint24 : t.tint12)[a.on ?? 'page'][a.tone]
  const ink = plain ? t.fg.bold : t.tone[a.tone]
  const weight = strong ? ' font-weight="600"' : ''
  const body =
    `<rect x=".5" y=".5" width="${width - 1}" height="19" rx="9.5" fill="${fill}" stroke="${ink}"/>` +
    `<circle cx="11" cy="10" r="3.5" fill="${ink}"/>` +
    `<text x="20" y="14" font-family="${MONO}" font-size="11"${weight} fill="${t.fg.bold}">${escapeXml(a.text)}</text>`
  return { source: doc(width, 20, body), alt: a.text === '' ? a.tone : a.text, width, height: 20 }
}

/** A keycap legend: a rounded border-strong outline on the raised ground, the key in foreground-bold. */
export function keycapSvg(t: RrTokens, key: string): SvgPiece {
  const width = Math.max(18, Math.ceil(12 + [...key].length * KEYCAP_ADVANCE))
  const body =
    `<rect x=".5" y=".5" width="${width - 1}" height="17" rx="4" fill="${t.surface.raised}" stroke="${t.border.strong}"/>` +
    `<text x="${width / 2}" y="12.5" text-anchor="middle" font-family="${MONO}" font-size="9.5" font-weight="600" fill="${t.fg.bold}">${escapeXml(key)}</text>`
  return { source: doc(width, 18, body), alt: `key ${key}`, width, height: 18 }
}

/** A hairline in border-default, `cells` layout cells long. */
export function ruleSvg(t: RrTokens, cells: number): SvgPiece {
  const width = Math.max(1, Math.round(cells * DESKTOP_PX_PER_CELL))
  return { source: doc(width, 1, `<rect width="${width}" height="1" fill="${t.border.default}"/>`), alt: 'rule', width, height: 1 }
}

/**
 * The queue strip: done segments in success, passed-but-skipped ones in foreground-disabled, the current one in signature,
 * the rest border-strong; past `cap` a `+N` label. Survivalist's success and signature are one grey, so its done segments
 * step down to foreground-subtle. Null when there is nothing to show (the caller draws the terminal's empty text).
 */
export function stepsSvg(t: RrTokens, a: { count: number; current: number; done: number; cap: number }): SvgPiece | null {
  const all = whole(a.count)
  if (all === 0) return null
  const cap = Math.max(1, whole(a.cap))
  const shown = Math.min(all, cap)
  const current = whole(a.current)
  const passed = Math.min(Math.max(0, current - 1), shown)
  const done = Math.min(whole(a.done), passed)
  const doneFill = t.polarity === 'survivalist' ? t.fg.subtle : t.tone.success
  const more = all > cap ? `+${all - cap}` : ''
  const top = more === '' ? 0 : 2
  const strip = shown * SEGMENT + (shown - 1) * SEGMENT_GAP
  const segments = Array.from({ length: shown }, (_, i) => {
    const fill = i < done ? doneFill : i < passed ? t.fg.disabled : i === current - 1 ? t.tone.signature : t.border.strong
    return `<rect x="${i * (SEGMENT + SEGMENT_GAP)}" y="${top}" width="${SEGMENT}" height="${SEGMENT_HEIGHT}" fill="${fill}"/>`
  }).join('')
  const tail = more === '' ? '' : `<text x="${strip + 6}" y="10" font-family="${MONO}" font-size="11" fill="${t.fg.subtle}">${more}</text>`
  const width = Math.ceil(more === '' ? strip : strip + 6 + more.length * MONO_ADVANCE)
  const height = more === '' ? SEGMENT_HEIGHT : 12
  return { source: doc(width, height, segments + tail), alt: `${current} of ${all}, ${whole(a.done)} done`, width, height }
}

/** A check item's box, 14 px: an outline in border-strong, or a success fill with a tick in the recessed ground. */
export function checkboxSvg(t: RrTokens, checked: boolean): SvgPiece {
  const body = checked
    ? `<rect x=".5" y=".5" width="13" height="13" fill="${t.tone.success}" stroke="${t.tone.success}"/>` +
      `<path d="M3.5 7.2 6 9.5l4.5-5" fill="none" stroke="${t.surface.recessed}" stroke-width="1.8"/>`
    : `<rect x=".5" y=".5" width="13" height="13" fill="none" stroke="${t.border.strong}"/>`
  return { source: doc(14, 14, body), alt: checked ? 'checked' : 'unchecked', width: 14, height: 14 }
}
```

- [ ] **Step 8: Teach the kit the desktop — replace `mods/rr-mods/hooks/kit.ts` whole**

Every terminal branch below is today's code, character for character. Keep it that way.

```ts
// User intent: Reality Reprojection's non-interactive elements as plain-data trees every mod draws the same way, per surface:
// the terminal look exactly as tuned live, the desktop with SVG shapes and native buttons. Surfaces step instead of
// shadowing, and colour never travels without its shape and word.
import type { RenderNode } from 'claude-code'

import type {
  RrBoxProps,
  RrButtonProps,
  RrDesktopButtonProps,
  RrGround,
  RrLevel,
  RrSignalKind,
  RrStrength,
  RrSurface,
  RrTerminalButtonProps,
  RrTokens,
  RrTone,
  RrTreatment,
} from '../types'
import { checkboxSvg, keycapSvg, pillSvg, ruleSvg, signalSvg, stepsSvg, type SvgPiece } from './svg'

export const SIGNAL_GLYPH: Readonly<Record<RrSignalKind, string>> = { success: '●', warning: '▲', critical: '◆', info: 'ⓘ' }

/** Whether a desktop pane root paints RR bg-page (true) or leaves the app's own ground (false). Decided at S0. */
export const DESKTOP_PAINTS_PAGE: boolean = true

// Survivalist has no hue to tint a chip button with, so it steps one surface up from its ground instead.
const NEXT_STEP: Readonly<Record<RrGround, Exclude<RrLevel, 'page' | 'recessed'>>> = { page: 'raised', raised: 'overlay', overlay: 'raised' }

const el = (tag: 'Box' | 'Text', props: Record<string, unknown>, ...children: unknown[]): RenderNode =>
  h(tag, props, ...children) as RenderNode

const svg = (piece: SvgPiece): RenderNode =>
  h('Svg', { source: piece.source, alt: piece.alt, width: piece.width, height: piece.height }) as RenderNode

export function surfaceProps(t: RrTokens, a: { level: RrLevel; width?: number | string; padX?: number; surface?: RrSurface }): RrBoxProps {
  const ground: RrBoxProps = {
    flexDirection: 'column',
    backgroundColor: t.surface[a.level],
    paddingX: a.padX ?? 1,
    ...(a.width === undefined ? {} : { width: a.width }),
  }
  if (a.surface !== 'desktop') return ground
  // Desktop: a card is its level's ground inside a full border-default border (an active card's round border-strong is the
  // consumer's); a pane root is a ground, not a card, so it never takes a border.
  if (a.level !== 'page') return { ...ground, borderStyle: 'single', borderColor: t.border.default }
  if (DESKTOP_PAINTS_PAGE) return ground
  return { flexDirection: 'column', paddingX: a.padX ?? 1, ...(a.width === undefined ? {} : { width: a.width }) }
}

export function surface(
  t: RrTokens,
  a: { level: RrLevel; children: readonly RenderNode[]; width?: number | string; padX?: number; surface?: RrSurface },
): RenderNode {
  return el('Box', { ...surfaceProps(t, a) }, ...a.children)
}

export function label(t: RrTokens, a: { text: string; surface?: RrSurface }): RenderNode {
  return el('Text', { color: t.signatureText, bold: t.polarity === 'survivalist' }, a.text.toUpperCase())
}

export function signal(t: RrTokens, a: { kind: RrSignalKind; word: string; detail?: string; surface?: RrSurface }): RenderNode {
  if (a.surface === 'desktop') {
    return el(
      'Box',
      { flexDirection: 'row', columnGap: 1, alignItems: 'center' },
      svg(signalSvg(t, a.kind)),
      el('Text', { color: t.fg.bold, bold: true }, a.word),
      ...(a.detail ? [el('Text', { color: t.fg.subtle }, a.detail)] : []),
    )
  }
  return el(
    'Box',
    { flexDirection: 'row' },
    el('Text', { color: t.tone[a.kind] }, SIGNAL_GLYPH[a.kind]),
    el('Text', { color: t.fg.bold, bold: true }, ` ${a.word}`),
    ...(a.detail ? [el('Text', { color: t.fg.subtle }, `  ${a.detail}`)] : []),
  )
}

export function row(t: RrTokens, a: { cells: readonly RenderNode[]; emphasis?: 'strong' | 'quiet'; surface?: RrSurface }): RenderNode {
  const color = a.emphasis === 'strong' ? t.fg.bold : a.emphasis === 'quiet' ? t.fg.subtle : t.fg.default
  const cells = a.cells.map(cell =>
    typeof cell === 'string' ? el('Text', { color, bold: a.emphasis === 'strong', wrap: 'truncate-end' }, cell) : cell,
  )
  return el('Box', { flexDirection: 'row', columnGap: 2 }, ...cells)
}

export function rule(t: RrTokens, a: { width: number; surface?: RrSurface }): RenderNode {
  const cells = Math.max(0, Math.floor(a.width))
  if (a.surface === 'desktop' && cells > 0) return svg(ruleSvg(t, cells))
  return el('Text', { color: t.border.default }, '─'.repeat(cells))
}

export function button(
  t: RrTokens,
  a: { treatment: RrTreatment; tone: RrTone; on?: RrGround; strength?: RrStrength; surface?: RrSurface },
): RrBoxProps {
  // The desktop's native button draws its own chrome: the wrapper only keeps the keyed-wrapper markup the same.
  if (a.surface === 'desktop') return { flexDirection: 'row' }
  const strong = a.strength === 24
  if (t.polarity === 'survivalist') {
    if (a.treatment === 'outline') return { borderStyle: 'bold', borderColor: t.fg.bold, paddingX: 1 }
    // The strong chip has no hue to deepen, so it takes a bolder grey than any surface step: border-strong.
    return { backgroundColor: strong ? t.border.strong : t.surface[NEXT_STEP[a.on ?? 'page']], paddingX: 1 }
  }
  return a.treatment === 'outline'
    ? { borderStyle: 'round', borderColor: t.tone[a.tone], paddingX: 1 }
    : { backgroundColor: (strong ? t.tint24 : t.tint12)[a.on ?? 'page'][a.tone], paddingX: 1 }
}

export function buttonProps(t: RrTokens, a: { key?: string; primary?: boolean; surface?: 'terminal' }): RrTerminalButtonProps
export function buttonProps(t: RrTokens, a: { key?: string; primary?: boolean; surface: 'desktop' }): RrDesktopButtonProps
export function buttonProps(t: RrTokens, a: { key?: string; primary?: boolean; surface?: RrSurface }): RrButtonProps
export function buttonProps(t: RrTokens, a: { key?: string; primary?: boolean; surface?: RrSurface }): RrButtonProps {
  if (a.surface === 'desktop') {
    return { ...(a.key === undefined ? {} : { hotkey: a.key }), variant: a.primary === true ? 'primary' : 'secondary' }
  }
  return { plain: true, ...(a.key === undefined ? {} : { hotkey: a.key }), hover: { bold: true, color: t.fg.bold } }
}

export function keycap(t: RrTokens, a: { key: string; tone: RrTone; surface?: RrSurface }): RenderNode {
  if (a.surface === 'desktop') return svg(keycapSvg(t, a.key))
  return el('Text', { backgroundColor: t.keycap[a.tone].bg, color: t.keycap[a.tone].ink, bold: true }, ` ${a.key} `)
}

export function chip(t: RrTokens, a: { text: string; tone: RrTone; strength: RrStrength; on?: RrGround; surface?: RrSurface }): RenderNode {
  if (a.surface === 'desktop') return svg(pillSvg(t, a))
  const glyph = a.tone === 'signature' ? '' : `${SIGNAL_GLYPH[a.tone]} `
  if (t.polarity === 'survivalist') {
    return el('Text', { color: t.fg.bold, bold: a.strength === 24 }, `[${glyph}${a.text}]`)
  }
  const tint = (a.strength === 24 ? t.tint24 : t.tint12)[a.on ?? 'page'][a.tone]
  return el('Text', { backgroundColor: tint, color: t.fg.bold, bold: a.strength === 24 }, ` ${glyph}${a.text} `)
}

/** The terminal strip, exactly as taskmaster-mods' `queueDots` drew it: `●` done and current, `○` ahead, `+N` past `cap`. */
export function stepsText(count: number, current: number, cap = 10): string {
  const all = Math.max(0, count)
  const shown = Math.min(all, cap)
  const filled = Math.max(0, Math.min(current, shown))
  return `${'●'.repeat(filled)}${'○'.repeat(shown - filled)}${all > cap ? `+${all - cap}` : ''}`
}

export function steps(t: RrTokens, a: { count: number; current: number; done: number; cap?: number; surface?: RrSurface }): RenderNode {
  const cap = a.cap ?? 10
  if (a.surface === 'desktop') {
    const piece = stepsSvg(t, { count: a.count, current: a.current, done: a.done, cap })
    if (piece !== null) return svg(piece)
  }
  return el('Text', { color: t.signatureText }, stepsText(a.count, a.current, cap))
}

export function checkbox(t: RrTokens, a: { checked: boolean; surface?: RrSurface }): RenderNode {
  if (a.surface === 'desktop') return svg(checkboxSvg(t, a.checked))
  return el('Text', { color: t.fg.default }, a.checked ? '☑' : '☐')
}
```

- [ ] **Step 9: Serve the new methods through the noun — edit `mods/rr-mods/hooks/register.tsx`**

Replace the header (lines 1–2) with:
```ts
// User intent: rr-mods's front door — give every mod $.rr (Reality Reprojection tokens and element trees for the active
// polarity, per surface: terminal or desktop) and the /rr-gallery pane where the look is tuned live against screenshots.
```
In the `engine.create` hook's `const rr: Rr = { … }`, after `chip: refuse,` add:
```ts
      steps: refuse,
      checkbox: refuse,
```
After the line `on('rr.chip', async ($, a) => ({ value: out(kit.chip(await tokensOf($), a)) }))` add:
```ts
  on('rr.steps', async ($, a) => ({ value: out(kit.steps(await tokensOf($), a)) }))
  on('rr.checkbox', async ($, a) => ({ value: out(kit.checkbox(await tokensOf($), a)) }))
```
The other `rr.*` hooks already pass the whole argument (`surface` and `primary` included) to the kit. Leave them unchanged.

- [ ] **Step 10: Keep taskmaster-mods compiling against the wider contract (terminal only, no output change)**

In `mods/taskmaster-mods/hooks/register.tsx`, in `rrOf`, after `chip: a => $.rr.chip(a),` add:
```ts
    steps: a => $.rr.steps(a),
    checkbox: a => $.rr.checkbox(a),
```
In `mods/taskmaster-mods/tests/fixtures/rr-stub.ts`, add the import under the existing one:
```ts
import { queueDots } from '../../hooks/model'
```
and after `chip: async a => text(` ${glyph[a.tone]} ${a.text} `),` add:
```ts
          steps: async a => h('Text', { color: '#8a9eeb' }, queueDots(a.current, a.count, a.cap ?? 10)) as never,
          checkbox: async a => text(a.checked ? '☑' : '☐'),
```

- [ ] **Step 11: Run every check**

```bash
claude plugin test C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods
claude plugin validate --strict C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods
npx -y -p typescript@5.6.3 tsc -p C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods
claude plugin test C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods
claude plugin validate --strict C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods
npx -y -p typescript@5.6.3 tsc -p C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods
```
Expected:
- rr-mods **39 pass**: the 24 old tests, 2 terminal pins, 11 desktop kit tests and 2 desktop noun tests. Both terminal pins are unchanged and green.
- taskmaster-mods **196 pass**.
- Both validations ✔.
- Both `tsc` runs silent.

- [ ] **Step 12: Commit**

```bash
git -C C:/Users/gruku/Files/Claude/taskmaster add mods/rr-mods/types/index.d.ts mods/rr-mods/hooks/svg.ts mods/rr-mods/hooks/kit.ts mods/rr-mods/hooks/register.tsx mods/rr-mods/tests/fixtures/svg.ts mods/rr-mods/tests/fixtures/inputs.ts mods/rr-mods/tests/fixtures/desktop-consumer.tsx mods/rr-mods/tests/desktop.test.ts mods/rr-mods/tests/desktop-noun.test.ts mods/taskmaster-mods/hooks/register.tsx mods/taskmaster-mods/tests/fixtures/rr-stub.ts
git -C C:/Users/gruku/Files/Claude/taskmaster commit -m "feat(rr-mods): \$.rr on the desktop — surface on every tree and button, SVG signals/pills/keycaps/rule, steps and checkbox, native button props; terminal unchanged"
```

---

### Task 2b: `/rr-gallery` on the desktop (S2, part 2)

**Spec:** §5.5, §7 "rr-mods desktop".

**Files:**
- Modify: `mods/rr-mods/hooks/gallery.ts` (whole file)
- Modify: `mods/rr-mods/hooks/register.tsx` (the `ui.render` Pane hook for `rr-gallery`; the type import)
- Modify: `mods/rr-mods/tests/desktop-noun.test.ts` (add a test)
- Modify: `mods/rr-mods/tests/noun.test.ts` (the old "terminal and desktop" gallery test becomes terminal only)

**Interfaces:**
- Consumes: Task 2a's kit, with `surface` on every call, `buttonProps(..., primary)`, `steps`, `checkbox`, `DESKTOP_PAINTS_PAGE`.
- Produces:
  - `galleryTree(t: RrTokens, width: number, demo: Demo, surface: RrSurface = 'terminal'): RenderNode`;
  - `treated(t, a: { id; treatment; tone; on?; strength?; surface?: RrSurface }, pressable): RenderNode`;
  - gallery Button keys unchanged (`outline-page`, `strong-page`, `<id>-page`, `tones-<tone>-<ground>`, `pol-*`);
  - new sections `STEPS` and `CHECKBOX`.

- [ ] **Step 1: Write the failing gallery test**

Append to `mods/rr-mods/tests/desktop-noun.test.ts`. Add to its imports:
```ts
import * as kit from '../hooks/kit'
import { tokensFor } from '../hooks/polarity'
import { GALLERY_PANE, SESSION } from './fixtures/inputs'
```
(merge `GALLERY_PANE, SESSION` into the existing `./fixtures/inputs` import line). Then add inside the `describe`:

```ts
  test('/rr-gallery on the desktop: native buttons (done the primary), neutral wrappers, clean SVGs rebuilt on every polarity flip (Review Focus 3)', async ($, on) => {
    const world = rrWorldOf(on, 'dark')
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: 'rr-mods', ...GALLERY_PANE, surface: 'desktop' })
    const unchecked = async (): Promise<string> =>
      String((await ui.findAll({ type: 'Svg' })).find(s => s.props.alt === 'unchecked')?.props.source ?? '')
    for (const polarity of POLARITIES) {
      await ui.press({ key: `pol-${polarity}` })
      const t = tokensFor(polarity)
      expect((await ui.find({ type: 'Box' }))?.props.backgroundColor, polarity).toBe(kit.DESKTOP_PAINTS_PAGE ? t.surface.page : undefined)
      expect((await ui.find({ type: 'Button', key: 'outline-page' }))?.props).toMatchObject({ label: 'done', hotkey: 'd', variant: 'primary' })
      expect((await ui.find({ type: 'Button', key: 'strong-page' }))?.props).toMatchObject({ label: 'done', variant: 'primary' })
      const skip = await ui.find({ type: 'Button', key: 'skip-page' })
      expect(skip?.props).toMatchObject({ label: 'skip', hotkey: 's', variant: 'secondary' })
      expect(skip?.props.plain).toBeUndefined()
      const wrapper = await ui.find({ type: 'Box', key: 'outline-page-box' })
      expect(wrapper?.props.flexDirection).toBe('row')
      expect(wrapper?.props.borderStyle).toBeUndefined()
      expect(wrapper?.props.backgroundColor).toBeUndefined()
      const svgs = await ui.findAll({ type: 'Svg' })
      for (const s of svgs) expect(svgFaults(s.props), `${polarity} ${String(s.props.alt)}`).toEqual([])
      const alts = svgs.map(s => String(s.props.alt))
      for (const alt of ['2 of 5, 1 done', '2 of 355, 1 done', 'unchecked', 'checked', 'rule', 'key d', 'refused', 'critical']) {
        expect(alts, `${polarity} ${alt}`).toContain(alt)
      }
      expect(await unchecked(), polarity).toContain(`stroke="${t.border.strong}"`)
    }
    expect(tokensFor('dark').border.strong).not.toBe(tokensFor('light').border.strong)
    await ui.press({ key: 'pol-dark' })
    expect(await unchecked()).not.toContain(`stroke="${tokensFor('light').border.strong}"`)
    await ui.press({ key: 'skip-page' })
    expect(world.toasts).toEqual(['rr-gallery: pressed skip-page'])
    await ui.unmount()
  })
```

In `mods/rr-mods/tests/noun.test.ts`, narrow the old test, which asserts the terminal recipe on the desktop:
- `test('every element validates on the terminal and the desktop in every polarity', async ($, on) => {` becomes `test('every element validates on the terminal in every polarity', async ($, on) => {`
- `for (const surface of ['terminal', 'desktop'] as const) {` becomes `for (const surface of ['terminal'] as const) {`
- The two final lines
  ```ts
      const perSurface = ['rr-gallery: pressed skip-page', 'rr-gallery: pressed tones-info-overlay']
      expect(world.toasts).toEqual([...perSurface, ...perSurface])
  ```
  become
  ```ts
      expect(world.toasts).toEqual(['rr-gallery: pressed skip-page', 'rr-gallery: pressed tones-info-overlay'])
  ```

Every assertion inside the loop stays unchanged.

- [ ] **Step 2: Run to see it fail**

Run: `claude plugin test C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods`
Expected: 1 fail. The new gallery test fails at `outline-page`: received `{ plain: true, hotkey: 'd', … }` with no `variant`. The other 39 pass.

- [ ] **Step 3: Draw the gallery through its surface — replace `mods/rr-mods/hooks/gallery.ts` whole**

```ts
// User intent: the /rr-gallery body — every $.rr element in every state with sample data, drawn through the surface the
// gallery is on, so the RR look is judged and tuned on one screen per surface (terminal and desktop) against screenshots.
import type { RenderNode } from 'claude-code'

import type { RrButtonProps, RrGround, RrLevel, RrSignalKind, RrStrength, RrSurface, RrTokens, RrTone, RrTreatment } from '../types'
import * as kit from './kit'
import { RR_SOURCE } from './tokens'

const LEVELS: readonly RrLevel[] = ['raised', 'overlay', 'recessed']
const KINDS: readonly RrSignalKind[] = ['success', 'warning', 'critical', 'info']
const TONES: readonly RrTone[] = ['success', 'warning', 'critical', 'info', 'signature']
const GROUNDS: readonly RrGround[] = ['page', 'raised', 'overlay']
// The buttons taskmaster-mods draws (d done is the one primary), then one chip per tone on each ground. Only the page rows arm
// hotkeys: two Buttons on one hotkey clash.
const PRIMARY_ROW: readonly { id: string; key: string; label: string; tone: RrTone }[] = [
  { id: 'back-to-agent', key: 'a', label: 'back to agent', tone: 'warning' },
  { id: 'skip', key: 's', label: 'skip', tone: 'signature' },
  { id: 'open', key: 'o', label: 'open', tone: 'signature' },
]
const TONE_SAMPLES: Readonly<Record<RrTone, { key: string; label: string }>> = {
  success: { key: 'r', label: 'resume' },
  warning: { key: 'w', label: 'wait' },
  critical: { key: 'x', label: 'discard' },
  info: { key: 'c', label: 'copy' },
  signature: { key: 'y', label: 'confirm' },
}

const box = (props: Record<string, unknown>, ...children: unknown[]): RenderNode => h('Box', props, ...children) as RenderNode

/** The consumer's Button in the recipe, drawn by register.tsx, which owns the press handlers. */
export type Demo = (id: string, label: string, press: RrButtonProps) => RenderNode

/** The recipe's wrapper: the treatment's keyed Box (the key scopes the Button's hover) around the consumer's Button. */
export function treated(
  t: RrTokens,
  a: { id: string; treatment: RrTreatment; tone: RrTone; on?: RrGround; strength?: RrStrength; surface?: RrSurface },
  pressable: RenderNode,
): RenderNode {
  return box({ key: `${a.id}-box`, ...kit.button(t, a) }, pressable)
}

export function galleryTree(t: RrTokens, width: number, demo: Demo, surface: RrSurface = 'terminal'): RenderNode {
  const section = (name: string, ...rows: RenderNode[]) =>
    box({ flexDirection: 'column' }, kit.label(t, { text: name, surface }), ...rows)
  const primaryRow = box(
    { flexDirection: 'row', columnGap: 1, alignItems: 'flex-start' },
    treated(
      t,
      { id: 'outline-page', treatment: 'outline', tone: 'success', surface },
      demo('outline-page', 'done', kit.buttonProps(t, { key: 'd', primary: true, surface })),
    ),
    // The card's primary since 2026-10-06: the strong (24%) chip, one row tall on the secondaries' baseline.
    treated(
      t,
      { id: 'strong-page', treatment: 'chip', tone: 'success', strength: 24, surface },
      demo('strong-page', 'done', kit.buttonProps(t, { primary: true, surface })),
    ),
    ...PRIMARY_ROW.map(b =>
      treated(
        t,
        { id: `${b.id}-page`, treatment: 'chip', tone: b.tone, surface },
        demo(`${b.id}-page`, b.label, kit.buttonProps(t, { key: b.key, surface })),
      ),
    ),
  )
  const toneRow = (on: RrGround) =>
    box(
      { flexDirection: 'row', columnGap: 1 },
      ...TONES.map(tone => {
        const id = `tones-${tone}-${on}`
        const press = kit.buttonProps(t, on === 'page' ? { key: TONE_SAMPLES[tone].key, surface } : { surface })
        return treated(t, { id, treatment: 'chip', tone, on, surface }, demo(id, TONE_SAMPLES[tone].label, press))
      }),
    )
  return box(
    { flexDirection: 'column', rowGap: 1 },
    section(
      'surfaces',
      ...LEVELS.map(level =>
        kit.surface(t, { level, children: [kit.row(t, { cells: [level, 'surface stepping, no shadow'], surface })], surface }),
      ),
    ),
    section(
      'voices',
      kit.row(t, { cells: ['DECLARATION OPENS'], emphasis: 'strong', surface }),
      kit.row(t, { cells: ['Narrator carries the content.'], surface }),
      kit.row(t, { cells: ['technical · metadata · 3h'], emphasis: 'quiet', surface }),
    ),
    section('signals', ...KINDS.map(kind => kit.signal(t, { kind, word: kind, detail: `${kind} detail`, surface }))),
    section(
      'buttons',
      primaryRow,
      ...GROUNDS.map(on =>
        on === 'page'
          ? box(
              { flexDirection: 'column' },
              kit.row(t, { cells: ['on page · hotkeys armed here only'], emphasis: 'quiet', surface }),
              toneRow(on),
            )
          : kit.surface(t, {
              level: on,
              children: [kit.row(t, { cells: [`on ${on}`], emphasis: 'quiet', surface }), toneRow(on)],
              surface,
            }),
      ),
    ),
    section(
      'states',
      box(
        { flexDirection: 'row', columnGap: 1 },
        kit.chip(t, { text: 'refused', tone: 'critical', strength: 24, surface }),
        kit.chip(t, { text: 'confirm done?', tone: 'warning', strength: 24, surface }),
        kit.chip(t, { text: 'signed off', tone: 'success', strength: 24, surface }),
      ),
      box({ flexDirection: 'row', columnGap: 1 }, ...TONES.map(tone => kit.chip(t, { text: tone, tone, strength: 12, surface }))),
    ),
    section(
      'keys',
      box(
        { flexDirection: 'row', columnGap: 1 },
        kit.keycap(t, { key: 'd', tone: 'success', surface }),
        kit.row(t, { cells: ['done'], surface }),
        kit.keycap(t, { key: 'a', tone: 'warning', surface }),
        kit.row(t, { cells: ['back'], surface }),
        kit.keycap(t, { key: 's', tone: 'signature', surface }),
        kit.row(t, { cells: ['skip'], surface }),
        kit.keycap(t, { key: 'o', tone: 'signature', surface }),
        kit.row(t, { cells: ['open'], surface }),
      ),
    ),
    section(
      'steps',
      kit.steps(t, { count: 5, current: 2, done: 1, surface }),
      kit.steps(t, { count: 355, current: 2, done: 1, surface }),
    ),
    section(
      'checkbox',
      box(
        { flexDirection: 'row', columnGap: 1 },
        kit.checkbox(t, { checked: false, surface }),
        kit.row(t, { cells: ['open item'], surface }),
        kit.checkbox(t, { checked: true, surface }),
        kit.row(t, { cells: ['ticked item'], surface }),
      ),
    ),
    section('rule', kit.rule(t, { width: Math.max(10, width - 2), surface })),
    kit.row(t, { cells: [`${t.polarity} · tokens ${RR_SOURCE.sha256.slice(0, 12)}`], emphasis: 'quiet', surface }),
  )
}
```

- [ ] **Step 4: Give the gallery hook its surface — edit `mods/rr-mods/hooks/register.tsx`**

Change the type import to:
```ts
import type { Rr, RrNode, RrPolarity, RrSurface, RrTokens } from '../types'
```
Replace the whole `on('ui.render', { component: 'Pane', requestId: GALLERY }, …)` hook with:

```tsx
  on('ui.render', { component: 'Pane', requestId: GALLERY }, async ($, e) => {
    const { Box, Button } = $.ui.resolve(e)
    // Every element as the surface it is drawn on would draw it: desktop pieces on the desktop, the terminal look elsewhere.
    const surface: RrSurface = e.surface === 'desktop' ? 'desktop' : 'terminal'
    await read($, POLARITY) // subscribes the pane, so a theme change redraws it
    const shown = await effective($)
    const t = tokensFor(shown)
    const flip = (to: RrPolarity | 'none') => async () => {
      await update($, OVERRIDE, () => to)
      await publish($)
    }
    const demo: Demo = (id, label, press) => (
      <Button key={id} {...press} label={label} onPress={() => $.ui.toast(`rr-gallery: pressed ${id}`)} />
    )
    const polarityButton = (id: string, key: string, label: string, to: RrPolarity | 'none') =>
      treated(
        t,
        { id, treatment: 'chip', tone: 'signature', surface },
        <Button key={id} {...kit.buttonProps(t, { key, surface })} label={label} onPress={flip(to)} />,
      )
    return (
      <Box {...kit.surfaceProps(t, { level: 'page', surface })} flexGrow={1} rowGap={1}>
        <Box flexDirection="row" columnGap={1} alignItems="flex-start">
          {kit.label(t, { text: `polarity ${shown}`, surface })}
          {polarityButton('pol-dark', '1', 'dark', 'dark')}
          {polarityButton('pol-light', '2', 'light', 'light')}
          {polarityButton('pol-survivalist', '3', 'survivalist', 'survivalist')}
          {polarityButton('pol-auto', '0', 'auto', 'none')}
        </Box>
        {galleryTree(t, e.props.bodyColumns, demo, surface)}
      </Box>
    )
  })
```

- [ ] **Step 5: Run every check**

```bash
claude plugin test C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods
claude plugin validate --strict C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods
npx -y -p typescript@5.6.3 tsc -p C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods
claude plugin test C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods
```
Expected:
- rr-mods **40 pass**. The terminal pins and the narrowed terminal gallery test are green.
- ✔.
- `tsc` silent.
- taskmaster-mods **196 pass**.

- [ ] **Step 6: Commit**

```bash
git -C C:/Users/gruku/Files/Claude/taskmaster add mods/rr-mods/hooks/gallery.ts mods/rr-mods/hooks/register.tsx mods/rr-mods/tests/desktop-noun.test.ts mods/rr-mods/tests/noun.test.ts
git -C C:/Users/gruku/Files/Claude/taskmaster commit -m "feat(rr-mods): /rr-gallery draws every element through its surface (desktop pieces and native buttons on the desktop); steps and checkbox sections"
```

---

### Task 3: taskmaster-mods on the desktop (S3)

**Spec:** §6, §7 "taskmaster-mods desktop".

**Files:**
- Create: `mods/taskmaster-mods/tests/fixtures/golden.ts`
- Create: `mods/taskmaster-mods/tests/terminal.test.ts`
- Create: `mods/taskmaster-mods/tests/desktop.test.ts`
- Modify: `mods/taskmaster-mods/hooks/rr.ts` (add `RrSurface`)
- Modify: `mods/taskmaster-mods/hooks/draw.tsx`:
  - header `:1-3`;
  - imports and `Ui` `:27-31`;
  - `ChipSpec` `:55-65`;
  - `chip` `:68-82`;
  - `confirmPair` `:91`;
  - band done `:260`;
  - `paneRoot`/`paneStatus` `:333-353`;
  - `ReviewHandlers` `:367-380`;
  - `headerStrip` `:389-417`;
  - review `:437,442,509-537,611,635-647`;
  - `HandoverHandlers` `:660-665`;
  - handovers `:746-783`.
- Modify: `mods/taskmaster-mods/hooks/register.tsx`:
  - imports `:11, :29`;
  - `rrOf` `:93-108`;
  - band hook `:494-504`;
  - review hook `:532-550`;
  - handovers hook `:572-583`.
- Modify: `mods/taskmaster-mods/tests/fixtures/rr-stub.ts` (whole file)

**Interfaces:**
- Consumes:
  - Task 2a's `$.rr` contract (`surface?`, `primary?`, `steps`, `checkbox`, desktop button props);
  - Task 1's `RR_POLARITY = { plugin: 'rr-mods', key: 'polarity' }`.
- Produces:
  - In `hooks/rr.ts`: `export type RrSurface = NonNullable<Parameters<Rr['label']>[0]['surface']>`.
  - In `hooks/draw.tsx`:
    - `export type Ui = Pick<Elements['terminal' | 'desktop' | 'vscode'], 'Box' | 'Text' | 'Button' | 'Input'> & { readonly surface: RrSurface }`;
    - `export function uiOf(els: Pick<Elements['terminal' | 'desktop' | 'vscode'], 'Box' | 'Text' | 'Button' | 'Input'>, surface: RenderSurface): Ui` (`'desktop'` → `'desktop'`, everything else → `'terminal'`);
    - `ReviewHandlers.close: () => void` and `HandoverHandlers.close: () => void`.
  - In `hooks/register.tsx`: `function rrOf($: EngineInterface, surface: RrSurface): Rr`, which adds `surface` to every tree and button call.
  - The close Button: key `close`, `role: 'dismiss'`, label `close`, desktop only.
  - Primary actions (`primary: true`): band `band-done`, card `done`, `band-yes` and `confirm-yes`, handovers `copy`.

- [ ] **Step 1: Pin today's terminal drawings (before touching any code)**

`mods/taskmaster-mods/tests/fixtures/golden.ts`: the same content as `mods/rr-mods/tests/fixtures/golden.ts` (Task 2a Step 1), with the header:
```ts
// User intent: pin taskmaster-mods' terminal drawings byte for byte while the desktop look is added beside them — a short
// fingerprint of a tree that ignores property order (the same function as rr-mods'; mods can't import each other).
```

`mods/taskmaster-mods/tests/terminal.test.ts`:

```ts
// User intent: the band, review card and handovers pane draw on the terminal exactly what they drew before the desktop
// work — one fingerprint per state, captured before draw.tsx changed, so any terminal tree change fails here.
import { describe, expect, mock, test } from 'claude-code/testing'

import { DEMO_DETAILS, demoSnapshot } from '../hooks/demo'
import { splitCheck } from '../hooks/model'
import type { TmHandover } from '../types'
import { fingerprint } from './fixtures/golden'
import { BAND, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const TM = { plugins: [RR_STUB] }
const ID = 'unified-chat-022'
const [, SECOND] = splitCheck(DEMO_DETAILS[ID]!.humanAction).items as [string, string]
const [, OTHER] = demoSnapshot(0).handovers as readonly [TmHandover, TmHandover]
const sized = (requestId: string, columns: number) => ({ ...pane(requestId), props: { ...pane(requestId).props, bodyColumns: columns } })

// Filled from the first run (Step 2): what each state drew before the desktop work. Never edited after that.
const DEMO_GOLDEN: Record<string, string> = {}
const TM_GOLDEN: Record<string, string> = {}

describe('terminal output is pinned', () => {
  test('band, review card and handovers in demo mode draw what they drew before the desktop work', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on), { [`ticks:${ID}`]: { at: 0, items: [SECOND] } })
    await $.session.start(SESSION)
    const prints: Record<string, string> = {}
    for (const [name, columns, maxRows] of [
      ['band.wide', 120, 12],
      ['band.narrow', 30, 2],
    ] as const) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND, props: { ...BAND.props, bodyColumns: columns, maxRows } })
      prints[name] = fingerprint(await ui.drawn())
      await ui.unmount()
    }
    const band = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await band.press({ key: 'band-done' })
    prints['band.confirm'] = fingerprint(await band.drawn())
    await band.press({ key: 'band-no' })
    await band.unmount()
    for (const columns of [100, 72, 28]) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...sized('tm-review', columns) })
      prints[`review.${columns}`] = fingerprint(await ui.drawn())
      await ui.unmount()
    }
    const review = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
    await review.press({ key: 'details' })
    prints['review.details'] = fingerprint(await review.drawn())
    await review.press({ key: 'details' })
    await review.press({ key: 'done' })
    prints['review.confirm'] = fingerprint(await review.drawn())
    await review.press({ key: 'confirm-no' })
    await review.press({ key: 'back' })
    prints['review.note'] = fingerprint(await review.drawn())
    await review.press({ key: 'note-cancel' })
    await review.press({ key: 'skip' })
    await review.press({ key: 'done' })
    await review.press({ key: 'confirm-yes' })
    prints['review.refused'] = fingerprint(await review.drawn())
    for (let i = 0; i < 4; i += 1) await review.press({ key: 'skip' })
    prints['review.clear'] = fingerprint(await review.drawn())
    await review.unmount()
    const handovers = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-handovers') })
    prints['handovers.list'] = fingerprint(await handovers.drawn())
    await handovers.press({ key: 'summary' })
    prints['handovers.open'] = fingerprint(await handovers.drawn())
    await handovers.press({ key: `ho:${OTHER.id}` })
    prints['handovers.other'] = fingerprint(await handovers.drawn())
    await handovers.unmount()
    expect(prints).toEqual(DEMO_GOLDEN)
  })

  test('panes when Taskmaster cannot be reached draw what they drew before the desktop work', TM, async ($, on) => {
    worldOf(on, mock.clock(on))
    stateOf(on, { [`${PLUGIN}.snapshot`]: { ...demoSnapshot(0), reason: 'tm: Connection closed', reachable: false } })
    const prints: Record<string, string> = {}
    for (const requestId of ['tm-review', 'tm-handovers']) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...pane(requestId) })
      prints[requestId] = fingerprint(await ui.drawn())
      await ui.unmount()
    }
    expect(prints).toEqual(TM_GOLDEN)
  })
})
```

- [ ] **Step 2: Run, read the prints, fill the goldens, commit the pins on their own**

Run: `claude plugin test C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods`
Expected: **2 fail**. The received objects hold 15 prints (demo) and 2 prints (tm).

Copy them verbatim into `DEMO_GOLDEN` and `TM_GOLDEN`, then run again. Expected: **198 pass**.

```bash
git -C C:/Users/gruku/Files/Claude/taskmaster add mods/taskmaster-mods/tests/fixtures/golden.ts mods/taskmaster-mods/tests/terminal.test.ts
git -C C:/Users/gruku/Files/Claude/taskmaster commit -m "test(taskmaster-mods): pin terminal drawings of the band, review card and handovers before the desktop work"
```

- [ ] **Step 3: Write the failing desktop tests**

`mods/taskmaster-mods/tests/desktop.test.ts`:

```ts
// User intent: the band, review card and handovers pane as a person meets them in the desktop app's Code tab — native
// buttons with one primary per card, SVG pieces from $.rr, the native close control — doing exactly what they do in the
// terminal; VS Code keeps the terminal trees and mobile its one line.
import { describe, expect, mock, test } from 'claude-code/testing'

import { DEMO_DETAILS, demoSnapshot } from '../hooks/demo'
import { handoverCopyText, splitCheck } from '../hooks/model'
import type { TmHandover } from '../types'
import { BAND, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const ID = 'unified-chat-022'
const [FIRST, SECOND] = splitCheck(DEMO_DETAILS[ID]!.humanAction).items as [string, string]
const [, OTHER] = demoSnapshot(0).handovers as readonly [TmHandover, TmHandover]
const DESKTOP_BAND = { ...BAND, surface: 'desktop' as const }
const DESKTOP_REVIEW = { ...pane('tm-review'), surface: 'desktop' as const }
const DESKTOP_HANDOVERS = { ...pane('tm-handovers'), surface: 'desktop' as const }

type Finds = { findAll: (q: { type: string }) => Promise<{ props: Record<string, unknown> }[]> }
/** The alts of every Svg drawn: the stub names each desktop piece by what it stands for. */
const altsOf = async (ui: Finds): Promise<string[]> => (await ui.findAll({ type: 'Svg' })).map(s => String(s.props.alt))

describe('desktop', () => {
  test('band: native buttons, done the primary, neutral wrappers, the needs-you signal an SVG shape beside its words', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...DESKTOP_BAND })
    expect((await ui.find({ type: 'Button', key: 'band-done' }))?.props).toMatchObject({ hotkey: 'd', label: 'done', variant: 'primary' })
    for (const key of ['band-back', 'band-review', 'band-handovers']) {
      const button = await ui.find({ type: 'Button', key })
      expect(button?.props.variant, key).toBe('secondary')
      expect(button?.props.plain, key).toBeUndefined()
      const wrapper = await ui.find({ type: 'Box', key: `${key}-box` })
      expect(wrapper?.props.backgroundColor, key).toBeUndefined()
      expect(wrapper?.props.borderStyle, key).toBeUndefined()
    }
    expect(await altsOf(ui)).toContain('warning')
    expect(await ui.find({ type: 'Text', text: '5 waiting on you' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^▲ / })).toBeUndefined()
  })

  test('band: d asks, y is the primary and n keeps the focus, y signs off and the band moves on', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...DESKTOP_BAND })
    await ui.press({ key: 'band-done' })
    expect((await ui.find({ type: 'Button', key: 'band-yes' }))?.props).toMatchObject({ hotkey: 'y', variant: 'primary' })
    expect((await ui.find({ type: 'Button', key: 'band-no' }))?.props).toMatchObject({ hotkey: 'n', variant: 'secondary', autoFocus: true })
    expect(await altsOf(ui)).toContain(`done ${ID}?`)
    await ui.press({ key: 'band-yes' })
    expect(await ui.find({ type: 'Text', text: /4 waiting on you/ })).toBeDefined()
  })

  test('review card: steps and checkboxes are SVGs, each item a native button, done the primary, close the native dismiss', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on), { [`ticks:${ID}`]: { at: 0, items: [SECOND] } })
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...DESKTOP_REVIEW })
    const shown = await altsOf(ui)
    for (const alt of ['1 of 5, 0 done', 'unchecked', 'checked', 'success']) expect(shown, alt).toContain(alt)
    expect(await ui.find({ type: 'Text', text: '●○○○○' })).toBeUndefined()
    expect((await ui.findAll({ type: 'Box' })).filter(b => b.props.borderStyle === 'round')).toHaveLength(1)
    expect((await ui.find({ type: 'Button', key: 'tick-0' }))?.props).toMatchObject({ hotkey: '1', label: FIRST, variant: 'secondary' })
    expect((await ui.find({ type: 'Button', key: 'tick-1' }))?.props).toMatchObject({ hotkey: '2', label: SECOND })
    expect((await ui.find({ type: 'Button', key: 'done' }))?.props).toMatchObject({ hotkey: 'd', variant: 'primary' })
    for (const key of ['back', 'skip', 'open', 'viewer', 'copy', 'details']) {
      expect((await ui.find({ type: 'Button', key }))?.props.variant, key).toBe('secondary')
    }
    expect(await ui.find({ type: 'Text', text: /esc close/ })).toBeUndefined()
    expect((await ui.find({ type: 'Button', key: 'close' }))?.props).toMatchObject({ role: 'dismiss', label: 'close' })
    await ui.press({ key: 'tick-0' })
    expect((await altsOf(ui)).filter(a => a === 'checked')).toHaveLength(2)
    expect(world.store.get(`ticks:${ID}`)).toMatchObject({ items: [SECOND, FIRST] })
    await ui.press({ key: 'close' })
    expect(world.closed).toEqual(['tm-review'])
  })

  test('review card: confirm, note, refusal and queue clear work on the desktop as on the terminal, and close stays reachable', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...DESKTOP_REVIEW })
    await ui.press({ key: 'done' })
    expect((await ui.find({ type: 'Button', key: 'confirm-yes' }))?.props).toMatchObject({ hotkey: 'y', variant: 'primary' })
    expect((await ui.find({ type: 'Button', key: 'confirm-no' }))?.props).toMatchObject({ hotkey: 'n', autoFocus: true })
    expect(await altsOf(ui)).toContain('2 of 2 unchecked — done anyway?')
    await ui.press({ key: 'confirm-no' })
    await ui.press({ key: 'back' })
    expect(await ui.find({ type: 'Input', key: 'note' })).toBeDefined()
    await ui.press({ key: 'note-cancel' })
    await ui.press({ key: 'skip' })
    await ui.press({ key: 'done' })
    await ui.press({ key: 'confirm-yes' })
    expect(await altsOf(ui)).toContain('refused')
    expect(await ui.find({ type: 'Text', text: /outstanding gates for lane/ })).toBeDefined()
    for (let i = 0; i < 4; i += 1) await ui.press({ key: 'skip' })
    expect(await ui.find({ type: 'Text', text: 'Queue clear  0 done · 5 skipped this pass' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'close' }))?.props.role).toBe('dismiss')
  })

  test('a narrow desktop pane never shows yes without no, and no keeps the focus (Review Focus 1)', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const columns of [72, 34, 28, 20]) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...DESKTOP_REVIEW, props: { ...DESKTOP_REVIEW.props, bodyColumns: columns } })
      await ui.press({ key: 'done' })
      expect(await ui.find({ type: 'Button', key: 'confirm-yes' }), `${columns}`).toBeDefined()
      expect((await ui.find({ type: 'Button', key: 'confirm-no' }))?.props.autoFocus, `${columns}`).toBe(true)
      expect((await altsOf(ui)).some(a => /unchecked/.test(a)), `${columns}`).toBe(true)
      await ui.press({ key: 'confirm-no' })
      await ui.unmount()
    }
  })

  test('a 355-item queue reaches the strip whole (Review Focus 2)', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    stateOf(on, { [`${PLUGIN}.snapshot`]: { ...demoSnapshot(0), queueTotal: 355 } })
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...DESKTOP_REVIEW })
    expect(await altsOf(ui)).toContain('1 of 355, 0 done')
  })

  test('handovers: copy the primary, rows and resume secondary, the summary toggles, close the native dismiss', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...DESKTOP_HANDOVERS })
    expect((await ui.find({ type: 'Button', key: 'copy' }))?.props).toMatchObject({ hotkey: 'c', label: 'copy', variant: 'primary' })
    expect((await ui.find({ type: 'Button', key: 'resume' }))?.props).toMatchObject({ hotkey: 'r', variant: 'secondary' })
    const rows = (await ui.findAll({ type: 'Button' })).filter(b => b.key?.startsWith('ho:'))
    expect(rows).toHaveLength(5)
    for (const row of rows) expect(row.props.variant, row.key).toBe('secondary')
    expect(await ui.find({ type: 'Text', text: '5 of 6 · superseded hidden' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /esc close/ })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: 'DECISIONS' })).toBeUndefined()
    await ui.press({ key: 'summary' })
    expect(await ui.find({ type: 'Text', text: 'DECISIONS' })).toBeDefined()
    await ui.press({ key: `ho:${OTHER.id}` })
    await ui.press({ key: 'copy' })
    expect(world.copies).toEqual([handoverCopyText(OTHER)])
    await ui.press({ key: 'close' })
    expect(world.closed).toEqual(['tm-handovers'])
  })

  test('VS Code draws the terminal trees (no SVG, esc close, plain hotkey buttons); mobile keeps its one line (Review Focus 4)', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const review = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review'), surface: 'vscode' })
    expect(await review.findAll({ type: 'Svg' })).toEqual([])
    expect(await review.find({ type: 'Text', text: '●○○○○' })).toBeDefined()
    expect(await review.find({ type: 'Text', text: /^esc close$/ })).toBeDefined()
    expect((await review.find({ type: 'Button', key: 'done' }))?.props).toMatchObject({ plain: true, hotkey: 'd' })
    expect(await review.find({ type: 'Button', key: 'close' })).toBeUndefined()
    await review.unmount()
    const band = await $.ui.mount({ plugin: PLUGIN, ...BAND, surface: 'vscode' })
    expect(await band.findAll({ type: 'Svg' })).toEqual([])
    expect(await band.find({ type: 'Text', text: '▲ 5 waiting on you' })).toBeDefined()
    await band.unmount()
    const mobile = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review'), surface: 'mobile' })
    expect(await mobile.find({ type: 'Text', text: 'Open the review queue in the terminal or the desktop app.' })).toBeDefined()
  })
})
```

- [ ] **Step 4: Run to see them fail**

Run: `claude plugin test C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods`
Expected:
- Most of the 8 new desktop tests fail. For example, `band-done` lacks `variant`, because no surface reaches the stub, and there is no `close` Button.
- The VS Code / mobile test passes already.
- The other 198 pass.

- [ ] **Step 5: Make the stub answer per surface — replace `mods/taskmaster-mods/tests/fixtures/rr-stub.ts` whole**

Every terminal answer below is exactly the current one. That keeps Step 1's pins valid.

```ts
// User intent: a stand-in $.rr for taskmaster-mods tests — `claude plugin test` does not load dependencies — with plain,
// predictable trees so tests assert words, keys and SVG alts, not colours; button props follow rr-mods's real recipe per surface.
import type { Plugin } from 'claude-code/testing'

import { queueDots } from '../../hooks/model'

export const RR_STUB: Plugin = {
  name: 'rr-mods',
  register(on) {
    on('engine.create', async ($, e, next) => {
      const built = await next(e)
      const tone = { success: '#3a9a5b', warning: '#c4881d', critical: '#d14343', info: '#5b8fc7', signature: '#8a9eeb' }
      const strong = { success: '#1f5f35', warning: '#7a5410', critical: '#7d2525', info: '#2f557d', signature: '#4a5590' }
      const tint = { page: tone, raised: tone, overlay: tone }
      const tint24 = { page: strong, raised: strong, overlay: strong }
      const glyph = { success: '●', warning: '▲', critical: '◆', info: 'ⓘ', signature: '' }
      const keycap = { bg: '#8a9eeb', ink: '#0d0d0c' }
      const text = (s: string) => h('Text', {}, s) as never
      // A desktop piece is an Svg leaf whose alt names what it stands for, so a test finds it by alt.
      const svg = (alt: string) =>
        h('Svg', { source: '<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>', alt, width: 10, height: 10 }) as never
      const desktop = (a: { surface?: string }) => a.surface === 'desktop'
      const said = (a: { word: string; detail?: string }) => `${a.word}${a.detail ? `  ${a.detail}` : ''}`
      return {
        ...built,
        rr: {
          tokens: async () => ({
            polarity: 'dark' as const,
            surface: { page: '#141413', raised: '#1d1d1b', overlay: '#272725', recessed: '#0d0d0c' },
            fg: { bold: '#f5f3ed', default: '#d2cfc8', subtle: '#a09c95', disabled: '#66645f' },
            border: { default: '#343331', subtle: '#302f2d', strong: '#4d4c48', focus: '#5e79e6' },
            signatureText: '#8a9eeb',
            tone,
            tint12: tint,
            tint24,
            keycap: { success: keycap, warning: keycap, critical: keycap, info: keycap, signature: keycap },
          }),
          polarity: async () => 'dark' as const,
          surface: async a => h('Box', { flexDirection: 'column' }, ...a.children) as never,
          surfaceProps: async () => ({ flexDirection: 'column' as const, paddingX: 1 }),
          label: async a => text(a.text.toUpperCase()),
          signal: async a =>
            desktop(a) ? (h('Box', { flexDirection: 'row', columnGap: 1 }, svg(a.kind), text(said(a))) as never) : text(`${glyph[a.kind]} ${said(a)}`),
          row: async a => h('Box', { flexDirection: 'row', columnGap: 2 }, ...a.cells.map(c => (typeof c === 'string' ? text(c) : c))) as never,
          rule: async a => (desktop(a) ? svg('rule') : text('─'.repeat(a.width))),
          button: async a =>
            desktop(a)
              ? { flexDirection: 'row' as const }
              : a.treatment === 'outline'
                ? { borderStyle: 'round' as const, borderColor: tone[a.tone], paddingX: 1 }
                : { backgroundColor: (a.strength === 24 ? strong : tone)[a.tone], paddingX: 1 },
          buttonProps: async a =>
            desktop(a)
              ? { ...(a.key === undefined ? {} : { hotkey: a.key }), variant: a.primary === true ? ('primary' as const) : ('secondary' as const) }
              : { plain: true as const, ...(a.key === undefined ? {} : { hotkey: a.key }), hover: { bold: true as const, color: '#f5f3ed' } },
          keycap: async a => (desktop(a) ? svg(`key ${a.key}`) : text(` ${a.key} `)),
          chip: async a => (desktop(a) ? svg(a.text) : text(` ${glyph[a.tone]} ${a.text} `)),
          steps: async a =>
            desktop(a)
              ? a.count > 0
                ? svg(`${a.current} of ${a.count}, ${a.done} done`)
                : text('')
              : (h('Text', { color: '#8a9eeb' }, queueDots(a.current, a.count, a.cap ?? 10)) as never),
          checkbox: async a => (desktop(a) ? svg(a.checked ? 'checked' : 'unchecked') : text(a.checked ? '☑' : '☐')),
        },
      }
    })
  },
}
```

- [ ] **Step 6: Name the surface type — `mods/taskmaster-mods/hooks/rr.ts`**

Append:
```ts
export type RrSurface = NonNullable<Parameters<Rr['label']>[0]['surface']>
```

- [ ] **Step 7: Draw per surface — edit `mods/taskmaster-mods/hooks/draw.tsx`**

Make these edits in order. Every terminal path keeps its JSX and arithmetic exactly; Step 1's pins prove it.

**7a. Header (lines 1–3)**, replaced with:
```ts
// User intent: how taskmaster-mods looks on the terminal and the desktop Code tab — the band, the review card and the handovers
// list drawn from plain view data with Reality Reprojection pieces from $.rr for the site's surface; Buttons are drawn here
// because their press handlers must stay in this plugin. JSX compiles to h(...): never name a variable `h` in this file.
```

**7b. Lines 27–31.** Replace
```ts
import type { Rr, RrButtonProps, RrNode, RrTokens, RrTone, RrTreatment } from './rr'

export type Ui = Pick<Elements['terminal'], 'Box' | 'Text' | 'Button' | 'Input'>

const node = (n: RrNode): RenderNode => n as unknown as RenderNode
```
with
```ts
import type { Rr, RrButtonProps, RrNode, RrSurface, RrTokens, RrTone, RrTreatment } from './rr'

type Drawing = Pick<Elements['terminal' | 'desktop' | 'vscode'], 'Box' | 'Text' | 'Button' | 'Input'>

/**
 * The elements a drawing uses and the $.rr surface it draws with: `desktop` gets $.rr's desktop pieces and native controls;
 * the terminal and VS Code (whose table takes the terminal trees) get the look tuned live in the terminal.
 */
export type Ui = Drawing & { readonly surface: RrSurface }

export function uiOf(els: Drawing, surface: RenderSurface): Ui {
  return { Box: els.Box, Text: els.Text, Button: els.Button, Input: els.Input, surface: surface === 'desktop' ? 'desktop' : 'terminal' }
}

const node = (n: RrNode): RenderNode => n as unknown as RenderNode

/** What a terminal $.rr piece reads as, its text in order: a Button label is a string, so a terminal glyph goes in as text. */
function textOf(n: unknown): string {
  if (typeof n === 'string') return n
  if (typeof n !== 'object' || n === null) return ''
  return ((n as { children?: readonly unknown[] }).children ?? []).map(textOf).join('')
}

/** Cells a desktop's native button adds around its label (tests/fixtures/measure.ts counts a chrome Button so). Tuned at S4. */
const NATIVE_BUTTON_CELLS = 4
```

**7c. `ChipSpec`.** After `readonly strength?: 12 | 24` add:
```ts
  /** The card's one main action (`done`, `yes`, handovers `copy`): the desktop draws its primary button; the terminal as before. */
  readonly primary?: true
```

**7d. `chip()`.** Replace
```ts
    rr.buttonProps(c.hotkey === undefined ? {} : { key: c.hotkey }),
```
with
```ts
    rr.buttonProps({ ...(c.hotkey === undefined ? {} : { key: c.hotkey }), ...(c.primary === true ? { primary: true } : {}) }),
```
and replace
```ts
  const label = press.hotkey === undefined ? c.label.length : press.hotkey.length + 2 + c.label.length
```
with
```ts
  // The terminal draws `k: label`; a desktop's native button draws the label in its own chrome.
  const label =
    'variant' in press
      ? c.label.length + NATIVE_BUTTON_CELLS
      : press.hotkey === undefined
        ? c.label.length
        : press.hotkey.length + 2 + c.label.length
```

**7e. `confirmPair`.** In the `yes` chip spec, add `primary: true`:
```ts
    chip(ui, rr, { id: `${prefix}-yes`, hotkey: 'y', label: 'yes', treatment: 'chip', tone: 'success', primary: true, onPress: onYes }),
```

**7f. Band done (line 260).** Add `primary: true`:
```ts
        chip(ui, rr, { id: 'band-done', hotkey: 'd', label: 'done', treatment: 'chip', tone: 'success', strength: 24, primary: true, onPress: () => on.askDone(task.id) }),
```

**7g. `paneRoot` and `paneStatus` (lines 333–353)**, replaced whole with:
```tsx
/**
 * A pane's root: RR's page ground painted by the pane itself (the host's pane background is not RR's page). On the desktop
 * it also holds the native close control (`role: 'dismiss'`), in every state; the terminal keeps `esc close` as dim text.
 */
async function paneRoot(ui: Ui, rr: Rr, children: readonly RenderChildren[], onClose: () => void): Promise<RenderElement> {
  const { Box, Button } = ui
  const ground = await rr.surfaceProps({ level: 'page' })
  if (ui.surface !== 'desktop') {
    return (
      <Box {...ground} flexGrow={1}>
        {children}
      </Box>
    )
  }
  return (
    <Box {...ground} flexGrow={1}>
      <Button key="close" role="dismiss" label="close" onPress={onClose} />
      {children}
    </Box>
  )
}

/**
 * What a pane shows before it has data, while tm is retried after a transient failure (neutral, like Loading), or when
 * Taskmaster cannot be reached.
 */
async function paneStatus(
  ui: Ui,
  rr: Rr,
  t: RrTokens,
  title: RenderNode,
  s: TmSnapshot | null,
  connecting: boolean,
  onClose: () => void,
): Promise<RenderElement> {
  const { Text } = ui
  if (connecting) return paneRoot(ui, rr, [title, <Text color={t.fg.subtle}>{CONNECTING}</Text>], onClose)
  if (s === null) return paneRoot(ui, rr, [title, <Text color={t.fg.subtle}>Loading…</Text>], onClose)
  return paneRoot(ui, rr, [title, node(await rr.signal({ kind: 'critical', word: 'Taskmaster unreachable', detail: s.reason }))], onClose)
}
```

**7h. `ReviewHandlers`.** After `copyCheck: (id: string, text: string, surface: RenderSurface) => void` add:
```ts
  /** The desktop's native close control (the terminal closes the pane on esc). */
  close: () => void
```
**`HandoverHandlers`.** After `toggleSummary: (h: TmHandover, open: boolean) => void` add the same two lines.

**7i. `headerStrip` (lines 389–417)**, replaced whole with:
```tsx
/** The strip above the card: REVIEW, the queue as steps, position and tally, and (terminal only) `esc close` at the right edge. */
async function headerStrip(ui: Ui, rr: Rr, t: RrTokens, n: number, total: number, done: number, width: number): Promise<RenderNode> {
  const { Box, Text } = ui
  const title = { el: node(await rr.label({ text: 'review' })), width: 'review'.length }
  // The desktop closes through its native dismiss control (paneRoot); only the terminal spells the key out.
  const esc = ui.surface === 'terminal' ? escHint(ui, t) : null
  const steps = node(await rr.steps({ count: total, current: n, done }))
  // The strip's room in cells is the terminal dots' length on both surfaces (the desktop SVG is sized near it; S4 checks).
  const dots = queueDots(n, total)
  const at = [`${n} of ${total} · ${done} done this pass`, `${n} of ${total} · ${done} done`, `${n}/${total}`]
  // Gives way right to left: the dots, then esc close, then the tally shortens.
  const room = (withDots: boolean, withEsc: boolean) =>
    width - title.width - 2 - (withDots ? dots.length + 2 : 0) - (withEsc && esc !== null ? esc.width + 2 : 0)
  const showDots = (at[0] ?? '').length <= room(true, true)
  const showEsc = esc !== null && at.some(a => a.length <= room(false, true))
  const pos = at.find(a => a.length <= room(showDots, showEsc)) ?? truncate(at[at.length - 1] ?? '', room(false, false))
  return (
    <Box flexDirection="row">
      <Box flexDirection="row" columnGap={2}>
        {title.el}
        {showDots ? steps : null}
        <Text color={t.fg.subtle}>{pos}</Text>
      </Box>
      <Box flexGrow={1} />
      {showEsc && esc !== null ? (
        <Box flexDirection="row" paddingLeft={2}>
          {esc.el}
        </Box>
      ) : null}
    </Box>
  )
}
```

**7j. Review pane, status returns.** At line 437 the call `return paneStatus(ui, rr, t, title, v.snapshot, v.connecting === true)` appears twice in the file: once in `reviewPaneTree` and once in `handoversPaneTree`. Replace both (replace-all) with:
```ts
return paneStatus(ui, rr, t, title, v.snapshot, v.connecting === true, on.close)
```
Replace line 442's
```ts
    return paneRoot(ui, rr, [title, node(await rr.signal({ kind: 'success', word: 'Queue clear', detail: tally }))])
```
with
```ts
    return paneRoot(ui, rr, [title, node(await rr.signal({ kind: 'success', word: 'Queue clear', detail: tally }))], on.close)
```

**7k. The checklist (lines 509–537).** Replace from `    const armed = mode === 'card'` through the closing `    })` of `check.items.forEach` with:
```tsx
    const armed = mode === 'card'
    const rowProps = await Promise.all(check.items.map((_, i) => rr.buttonProps(i < 9 ? { key: String(i + 1) } : {})))
    const marks = await Promise.all(check.items.map(entry => rr.checkbox({ checked: ticked.includes(entry) })))
    check.items.forEach((entry, i) => {
      const press = rowProps[i]
      const mark = marks[i]
      if (press === undefined || mark === undefined) return
      if (ui.surface === 'desktop') {
        // The desktop: the checkbox shape beside a native button carrying the whole item (the button wraps its own label).
        body.push(
          <Box key={`tick-${i}-box`} flexDirection="row" columnGap={1}>
            {node(mark)}
            {armed ? (
              <Button key={`tick-${i}`} {...press} label={entry} onPress={() => on.toggleTick(item.id, entry)} />
            ) : (
              <Text color={t.fg.default}>{entry}</Text>
            )}
          </Box>,
        )
        return
      }
      const keyCells = press.hotkey === undefined ? 0 : press.hotkey.length + 2
      const lead = keyCells + 2
      const wrapped = wrapText(entry, room - lead)
      const first = `${textOf(mark)} ${wrapped[0] ?? ''}`
      body.push(
        <Box key={`tick-${i}-box`} flexDirection="column">
          {armed ? (
            <Button key={`tick-${i}`} {...press} label={first} onPress={() => on.toggleTick(item.id, entry)} />
          ) : (
            <Box paddingLeft={keyCells}>
              <Text color={t.fg.default}>{first}</Text>
            </Box>
          )}
          {wrapped.slice(1).map(line => (
            <Box paddingLeft={lead}>
              <Text color={t.fg.default}>{line}</Text>
            </Box>
          ))}
        </Box>,
      )
    })
```

**7l. Card done (line 611).** Add `primary: true`:
```ts
          chip(ui, rr, { id: 'done', hotkey: 'd', label: 'done', treatment: 'chip', tone: 'success', strength: 24, primary: true, onPress: () => on.askDone(item.id) }),
```

**7m. Review return (lines 635–647).** Replace the closing
```tsx
    ...actions,
  ])
```
with
```tsx
    ...actions,
  ], on.close)
```

**7n. Handovers (lines 749–783).** Replace
```tsx
  if (picked === undefined) return paneRoot(ui, rr, [title, <Text color={t.fg.subtle}>No open handovers.</Text>])
```
with
```tsx
  if (picked === undefined) return paneRoot(ui, rr, [title, <Text color={t.fg.subtle}>No open handovers.</Text>], on.close)
```
Replace `  const esc = escHint(ui, t)` with:
```ts
  const esc = ui.surface === 'terminal' ? escHint(ui, t) : null
```
In the `copy` chip spec, add `primary: true`:
```ts
    chip(ui, rr, { id: 'copy', hotkey: 'c', label: 'copy', treatment: 'chip', tone: 'signature', primary: true, onPress: press => on.copy(picked, press.surface) }),
```
Replace the final
```tsx
    chipRow(ui, fit([copy, resume, esc], inner, 1)),
  ])
```
with
```tsx
    chipRow(ui, fit([copy, resume, esc], inner, 1)),
  ], on.close)
```
(`fit` already skips `null`.)

- [ ] **Step 8: Bind the surface once in `mods/taskmaster-mods/hooks/register.tsx`**

Line 11: `import { bandTree, handoversPaneTree, reviewPaneTree, type Ui } from './draw'` becomes
```ts
import { bandTree, handoversPaneTree, reviewPaneTree, uiOf } from './draw'
```
Line 29: `import type { Rr } from './rr'` becomes
```ts
import type { Rr, RrSurface } from './rr'
```
Replace `rrOf` (lines 93–108, including Task 2a's two passthroughs) with:
```ts
// Every tree and button call of a drawing carries its site's surface (spec §6); tokens and polarity are surface-free.
function rrOf($: EngineInterface, surface: RrSurface): Rr {
  return {
    tokens: () => $.rr.tokens(),
    polarity: () => $.rr.polarity(),
    surface: a => $.rr.surface({ ...a, surface }),
    surfaceProps: a => $.rr.surfaceProps({ ...a, surface }),
    label: a => $.rr.label({ ...a, surface }),
    signal: a => $.rr.signal({ ...a, surface }),
    row: a => $.rr.row({ ...a, surface }),
    rule: a => $.rr.rule({ ...a, surface }),
    button: a => $.rr.button({ ...a, surface }),
    buttonProps: a => $.rr.buttonProps({ ...a, surface }),
    keycap: a => $.rr.keycap({ ...a, surface }),
    chip: a => $.rr.chip({ ...a, surface }),
    steps: a => $.rr.steps({ ...a, surface }),
    checkbox: a => $.rr.checkbox({ ...a, surface }),
  }
}
```
In the `AbovePrompt` hook, replace
```ts
    const ui = $.ui.resolve(e) as unknown as Ui
    const ours = await bandTree(ui, rrOf($), model, await read($, BAND), e.props.bodyColumns, e.props.maxRows, {
```
with
```ts
    const ui = uiOf($.ui.resolve(e), e.surface)
    const ours = await bandTree(ui, rrOf($, ui.surface), model, await read($, BAND), e.props.bodyColumns, e.props.maxRows, {
```
(`const { Box } = ui` below stays.)

In the `tm-review` Pane hook, replace
```ts
    return reviewPaneTree(
      $.ui.resolve(e) as unknown as Ui,
      rrOf($),
```
with
```ts
    const ui = uiOf($.ui.resolve(e), e.surface)
    return reviewPaneTree(
      ui,
      rrOf($, ui.surface),
```
and after `copyCheck: (id, text, surface) => act(f => f.copyCheck(id, text, surface)),` add:
```ts
        close: () =>
          act(async () => {
            await mod.host?.closePane(REVIEW)
          }),
```
In the `tm-handovers` Pane hook, replace
```ts
    return handoversPaneTree(
      $.ui.resolve(e) as unknown as Ui,
      rrOf($),
```
with
```ts
    const ui = uiOf($.ui.resolve(e), e.surface)
    return handoversPaneTree(
      ui,
      rrOf($, ui.surface),
```
and after `toggleSummary: (handover, open) => act(f => f.toggleSummary(handover, open)),` add:
```ts
        close: () =>
          act(async () => {
            await mod.host?.closePane(HANDOVERS)
          }),
```
(`REVIEW` and `HANDOVERS` are already imported from `./model`.)

- [ ] **Step 9: Run every check**

```bash
claude plugin test C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods
claude plugin validate --strict C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods
npx -y -p typescript@5.6.3 tsc -p C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods
claude plugin test C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods
git -C C:/Users/gruku/Files/Claude/taskmaster grep -n 'as unknown as Ui' -- mods/taskmaster-mods
```
Expected:
- taskmaster-mods **206 pass**: the 196 old tests, 2 terminal pins (unchanged and green) and 8 desktop tests.
- validate ✔.
- `tsc` silent.
- rr-mods **40 pass**.
- The grep prints nothing.

If a terminal pin fails, a terminal tree changed. Compare `ui.drawn()` of that state before and after with `git stash`, and undo the difference.

- [ ] **Step 10: Commit**

```bash
git -C C:/Users/gruku/Files/Claude/taskmaster add mods/taskmaster-mods/hooks/rr.ts mods/taskmaster-mods/hooks/draw.tsx mods/taskmaster-mods/hooks/register.tsx mods/taskmaster-mods/tests/fixtures/rr-stub.ts mods/taskmaster-mods/tests/desktop.test.ts
git -C C:/Users/gruku/Files/Claude/taskmaster commit -m "feat(taskmaster-mods): desktop look — surface passed to \$.rr, native buttons with one primary, SVG steps and checkboxes, native close; terminal unchanged"
```

---

### Task 4: S4 — live desktop tuning with the user (orchestrator + user)

**Spec:** §5.4, §5.5, §8 S4, §10.

**Files:**
- Modify, desktop look only:
  - `mods/rr-mods/hooks/svg.ts`: the constants `DESKTOP_PX_PER_CELL`, `MONO_ADVANCE`, `KEYCAP_ADVANCE`, the segment sizes, and the shapes;
  - `mods/rr-mods/hooks/kit.ts`: the desktop branches and `DESKTOP_PAINTS_PAGE`;
  - `mods/taskmaster-mods/hooks/draw.tsx`: `NATIVE_BUTTON_CELLS` and the desktop-only branches.
- Modify: `docs/specs/2026-10-08-desktop-mods-design.md` §10.

**Interfaces:**
- Consumes: Tasks 2a, 2b and 3.
- Produces: the tuned desktop look, with each decision recorded in spec §10. Terminal output is untouched: both suites' terminal pins stay green.

- [ ] **Step 1: Gallery round.** Ask the user to open a desktop Local session with both mods (the route from S0, paths now `mods\rr-mods` and `mods\taskmaster-mods`), run `/rr-gallery`, press `1`, `2`, `3` and `0`, and paste a screenshot of each. Compare each screenshot against the prototype (https://claude.ai/artifact/Xt7nkD1Uz267erYJDoWPw3) and the rules:
  - SVG shapes are crisp at their size and in the right colours for each polarity;
  - pill labels fit their pill (`MONO_ADVANCE`);
  - the rule spans the asked width (`DESKTOP_PX_PER_CELL`);
  - the strip's height sits on the label baseline;
  - keycaps are legible;
  - surfaces step (raised < overlay) with full-perimeter borders, with no side rail and no shadow;
  - the native buttons show one primary;
  - hover changes colour only;
  - survivalist is legible by shape and weight alone.

- [ ] **Step 2: Real panes round.** Data source `demo`. Ask for screenshots of the band, `/tm-review` (card, `d` confirm, a refusal on the second card after `s` then `d` `y`, details open, queue clear), and `/tm-handovers` (collapsed, `i` expanded), at a wide and a narrow pane width. Check:
  - the active card keeps its round border-strong;
  - the header strip and tally fit at narrow widths (the strip is budgeted as the terminal dots' cells);
  - the native close control sits at the trailing edge and closes the pane;
  - check-item buttons wrap their own text;
  - nothing clips.

- [ ] **Step 3: One round of changes.** Agree the changes with the user. Make only desktop-path edits in the files above. For example: set `DESKTOP_PX_PER_CELL` to the measured value, change `MONO_ADVANCE` so pills fit, flip `DESKTOP_PAINTS_PAGE`, change `NATIVE_BUTTON_CELLS`, or promote the `▸ ▾` disclosure to an SVG (spec §5.2 "S4 may promote them"). Every desktop test reads the named constants, so changing a value needs no test edit. A new desktop element needs a test in `desktop.test.ts` in the step style of Task 2a. Then run:
  ```bash
  claude plugin test C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods
  claude plugin test C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods
  npx -y -p typescript@5.6.3 tsc -p C:/Users/gruku/Files/Claude/taskmaster/mods/rr-mods
  npx -y -p typescript@5.6.3 tsc -p C:/Users/gruku/Files/Claude/taskmaster/mods/taskmaster-mods
  ```
  Expected: everything passes. The terminal pins are green: a terminal pin failing means the change leaked into the terminal, so undo it.

- [ ] **Step 4: Record and commit the round.** Append one line per decision to spec §10:
  ```markdown
  - 2026-10-xx (S4 round <n>): <decision> (<why, from which screenshot>).
  ```
  ```bash
  git -C C:/Users/gruku/Files/Claude/taskmaster add mods/rr-mods/hooks mods/taskmaster-mods/hooks docs/specs/2026-10-08-desktop-mods-design.md
  git -C C:/Users/gruku/Files/Claude/taskmaster commit -m "fix(rr-mods): desktop tuning round <n> — <summary>"
  ```
  Use the `fix(taskmaster-mods): …` prefix when only `draw.tsx` changed.

- [ ] **Step 5: Loop or close.** Repeat Steps 1–4 until the user says the desktop gallery and the three real surfaces look right in dark, light and survivalist. Close with a §10 line `- 2026-10-xx (S4 closed): user approved the desktop look (<polarities checked>).`, committed as in Step 4.

---

### Task 5: S5 — desktop-app runbook, verified in a real Local session (orchestrator + user)

**Spec:** §9, §8 S5.

**Files:**
- Create: `docs/runbooks/desktop-app.md`
- Modify: `README.md` (one paragraph in "### 2. Connect your assistant" → "Claude Code")
- Modify: `docs/specs/2026-10-08-desktop-mods-design.md` §9 (mark each line verified)

**Interfaces:**
- Consumes: S0's loading route and polarity finding (spec §10, §11), and the finished mods.
- Produces: a user-facing runbook in which every line was checked live, or is marked as not verifiable yet.

- [ ] **Step 1: Write the runbook draft**

`docs/runbooks/desktop-app.md`. Use S0's results (spec §10/§11) for the variable route, the separator and polarity. Where S0 found something different from what is written below, write what S0 found.

```markdown
<!-- User intent: tell a Taskmaster user exactly what it takes to run Taskmaster, and its optional mods, in the Claude desktop
     app's Code tab, every line checked in a real Local session. -->

# Runbook: Taskmaster in the Claude desktop app

Verified on <date> with Claude desktop <app version> (bundled Claude Code <version>) on Windows 11.

## Which sessions can run Taskmaster

| Session | Taskmaster | Why |
|---|---|---|
| **Local** | Yes | It runs on your machine, so it can start the stdio `tm` server and read your `.taskmaster/` store. |
| Cloud | No | It runs remotely and cannot reach the local `tm` server or store. |
| SSH / WSL | Only with the plugin, `uv`, Python and git installed on that side | The session runs where it connects. |

## Before the first session

1. **Plugin.** The app reads the same `~/.claude` as the CLI. If `claude plugin list` in a terminal shows `taskmaster@gruku-tools`, the app has it too.
2. **Tools on the user PATH.** The app inherits the user PATH, not PowerShell profiles. In a new `cmd` window, `where uv`, `where python` and `where git` must each print a path.
3. **A POSIX shell for hooks.** Taskmaster's hooks are `.sh` scripts and run through Git for Windows' bash. They fail open: without bash, gates and resurfacing silently do nothing. Check this in the session (below).

## Start a session

1. Open the **Code** tab, start a new session, choose **Local**, and pick a project folder that has a `.taskmaster/`.
2. **Check the `tm` server.** Ask "show the Taskmaster status". Expect a status summary, not an MCP error. A cold first start can take a few seconds.
3. **Check the hooks.** Ask "Is the Taskmaster session-start context loaded? Quote its first two words." Expect `Taskmaster active.`

## The optional mods (band, review queue, handovers)

They need a Claude Code build with the mods API (2.1.289 or newer; the app bundles its own).

- **From a source checkout:** in the Local environment's environment editor, set
  `CLAUDE_CODE_PLUGIN_DIRS` = `<checkout>\mods\rr-mods;<checkout>\mods\taskmaster-mods`
  (separator `;`), then start a new session.
- **From the marketplace:** not available yet. `rr-mods` and `taskmaster-mods` are not listed in `gruku-tools` until the TUI plan's shipping task lands.

What appears:
- the band above the prompt: this session's task, and "N waiting on you" with `1` review and `2` handovers;
- `/tm-review` and `/tm-handovers`, with native buttons and a close control;
- a status line only on faults (`◆ tm offline`, `ⓘ tm reply unreadable`).

**Polarity.** `auto` follows the Claude Code theme row. On the desktop that row <follows | does not follow> the app's appearance (S0). If it does not, set rr-mods' **Polarity** to `dark` or `light` to match the app.

## The board

Ask "open the Taskmaster dashboard". The reply names a `http://127.0.0.1:<port>/` address. Open it in the app's **Browser** pane.

## When something is missing

| Symptom | Check |
|---|---|
| MCP error, or the band says `◆ tm offline` | Step 2 under "Before the first session" (`uv` on the user PATH); a Cloud session instead of Local. |
| No gates or resurfacing | Step 3 (bash for hooks). |
| No band or panes | `CLAUDE_CODE_PLUGIN_DIRS` paths and separator; the bundled Claude Code version. |
| Colours wrong for the app's appearance | Set rr-mods' Polarity explicitly. |
```

- [ ] **Step 2: Verify each line live with the user**

Ask the user to start a desktop **Local** session in `C:\Users\gruku\Files\Claude\claude-tools`, which has a real backlog, with `taskmaster-mods` Data source `tm` and the mods loaded as in the runbook. Then go through the runbook top to bottom. For every checkable line, the user runs the check and reports what they saw:
- `claude plugin list`;
- the three `where` commands;
- status;
- the hook quote;
- band, `/tm-review` and `/tm-handovers` with real data;
- the dashboard opening in the Browser pane;
- the versions.

For any line that does not hold, change the line to what was observed. Lines that cannot be checked here keep their wording and gain a marker: "Cloud" (unless the user opts to try a cloud session) and "From the marketplace" get `(documented, not run)`. Fill the "Verified on" line with the observed date and versions.

- [ ] **Step 3: Link it and mark the spec**

In `README.md`, inside the `<summary><strong>Claude Code</strong></summary>` block, after the paragraph ending "makes Taskmaster's skills\navailable to the session.", add:

```markdown
In the Claude desktop app's **Code** tab, use a **Local** session; see the
[desktop app runbook](docs/runbooks/desktop-app.md) for the checks and the optional mods.
```

In spec §9, replace "(…; not yet run live)" in its first line with "(verified live <date>, see `docs/runbooks/desktop-app.md`)". Append ` — verified` or ` — documented, not run` to each bullet to match Step 2.

- [ ] **Step 4: Commit**

```bash
git -C C:/Users/gruku/Files/Claude/taskmaster add docs/runbooks/desktop-app.md README.md docs/specs/2026-10-08-desktop-mods-design.md
git -C C:/Users/gruku/Files/Claude/taskmaster commit -m "docs(desktop): runbook for Taskmaster and its mods in the Claude desktop app, verified in a Local session"
```

---

## Self-review against the spec

| Spec section | Covered by |
|---|---|
| §1–2 goals 1–4 | Goal 1: Tasks 2a, 2b, 3 and 4. Goal 2 (RR knowledge in `$.rr`): Task 2a (`svg.ts`, `kit.ts`); `draw.tsx` only passes the surface. Goal 3: terminal pins in Tasks 2a and 3, plus the unchanged suites. Goal 4: Task 5. |
| §2 non-goals | No board in a mod and no option B. Mobile and VS Code are pinned by Task 3 (Review Focus 4). No server change. |
| §3 platform facts, ★ rows | Task 0 (probe + recording); "desktop tree fails on terminal" pinned in Task 2a `desktop-noun.test.ts`. |
| §4 rename | Task 1, every file enumerated; types re-laid; ledger appended; descriptions. |
| §5.1 contract | Task 2a types: `RrSurface`, `surface?` on every tree and button method, `buttonProps` → `{ hotkey?, variant }`, neutral `button` wrapper, `steps`, `checkbox`; `tokens`/`polarity` surface-free. |
| §5.2 desktop treatments | Task 2a `kit.ts` and `svg.ts`, one test each; pane-root ground per S0 (`DESKTOP_PAINTS_PAGE`); `row` and disclosure left for S4. |
| §5.3 SVG rules | `svg.ts` (strings from tokens, `escapeXml`, alt always, never `isInteractive`, `MONO_ADVANCE`); `svgFaults` checks every rule on every piece. |
| §5.4 fidelity | Prototype sizes in `svg.ts`; Task 4. |
| §5.5 gallery on desktop | Task 2b. |
| §5.6 polarity | No new detection. S0 records the theme-row finding; the runbook tells the user to set polarity explicitly when needed. |
| §6 taskmaster-mods | Task 3: surface via `rrOf`, casts removed (all three), `rr.steps` and `rr.checkbox`, `primary` on `done`/`yes`/handovers `copy`, check rows, dismiss on desktop, `esc close` kept on terminal. |
| §7 testing | Terminal: pins plus the unchanged suites (one old test narrowed, see the note below). rr-mods desktop: every method, tone, kind, level and polarity mounted on desktop via the consumer; SVG checks; never on a terminal mount. taskmaster-mods desktop: band, card (normal, confirm, refused, queue clear), handovers (collapsed, expanded); presses `d` `y` `a` ticks dismiss. Paint: Task 4. |
| §8 build order and stop rule | Tasks 0 → 5. Task 0 Step 5 carries the stop rule, plus a gate on hex colours. |
| §9 desktop app | Task 5. |
| §10 tuning log | Tasks 0 and 4 write it. |
| §11 open items | All five are answered in Task 0 Step 6. |

**One existing test changes.** `mods/rr-mods/tests/noun.test.ts` "every element validates on the terminal and the desktop in every polarity" asserts the *terminal* recipe on a desktop mount, which the spec replaces. Task 2b narrows it to the terminal, keeping every assertion. The desktop half moves to `desktop-noun.test.ts`.

**Not verifiable before running, so each is a live step:**
- whether the desktop app honours `CLAUDE_CODE_PLUGIN_DIRS` and with which separator;
- hex, `round` and painted backgrounds;
- the theme row;
- px per cell;
- whether native buttons show hotkeys (all Task 0);
- whether the desktop strip and pills fit their cell budgets (Task 4);
- every runbook line (Task 5).
