<!-- User intent: port the user-approved round-4 HTML prototype (one element vocabulary, keycap ladder, labelled actions, phase picker, direction-C band, a Taskmaster sidebar, 3-line lists) into taskmaster-mods, then build the restore pane in that style, stage by stage with a live check after each. -->

# Taskmaster mods visual rework Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every taskmaster-mods surface (band, Session, Review, Handovers, and the new Restore pane) drawn from one element vocabulary that matches the approved prototype: one filled key per context, labelled actions beside their subject, a phase picker instead of the strip, a direction-C band without counts, navigation through a Taskmaster sidebar, and 3-line list items, 3 to a page.

**Architecture:** A new `hooks/vocab.tsx` holds the vocabulary (inks, glyph badges, the key ladder, footer, list item, frame, toolbar, summary, bulk row, picker and menu, sidebar); `hooks/nav.ts` holds the pure navigation model; `kit.tsx` keeps only `kitOf` and the small helpers the vocabulary builds on. The band, Session, Review and Handovers trees are rewritten on the vocabulary in place (their flows, data and tm calls stay), one Taskmaster pane is open at a time, and the sidebar is an absolute overlay inside it. Stage E then builds the old plan's Stage 5 restore pane: its scanner, model and flows unchanged by reference, its drawing and wiring rewritten in the new style.

**Tech Stack:** Claude Code mods API (TSX hooks modules; `claude plugin test`, `claude plugin validate --strict`, `tsc`), `$.mcp.call('plugin:taskmaster:tm', …)`, Python 3 + pytest for the scanner.

**Spec:** `docs/specs/2026-10-11-mods-ux-design.md`, read §15 (the visual rework) first; §1–§14 still govern data and behaviour. The visual source of truth is `docs/specs/assets/2026-10-12-mods-visual-rework/` (`Main.dc.html` component sheet; `gen.py` holds every element's exact shape, ink and label).

**Previous plan:** `docs/superpowers/plans/2026-10-11-mods-ux.md` (Stages 0–4 are built; its Stage 5 is re-planned here as Stage E, which quotes its Tasks 5.1–5.5 and 5.7 by line range).

## Global Constraints

- Design: Reality Reprojection via `$.rr` tokens only; **no hex literal in `mods/taskmaster-mods/hooks/**`** (tests may assert the stub's hex).
- No coloured left rails, no motion on hover (hover changes colour or background only), no box-shadows; surface stepping.
- Hotkeys match `/^[a-z0-9]$/` (the engine refuses others; `actionKey` and `footer` throw on a bad one).
- One Button per hotkey in a drawn tree, except where a later Button deliberately wins (a y/n's `n` over nothing: the footer drops `p/n` while a y/n is up).
- Do not touch `hooks/handover-guard.ts`.
- Every new file starts with a 1–3 line `// User intent: …` header.
- Validator rules: never name a variable `h` in a .tsx file; a function taking `$` is declared at file top; every `$.state` key is declared in `types/index.d.ts` `PluginState`; `atom`/`read`/`update` sources are consts in the same file; `$` never crosses an import.
- No Taskmaster server, schema or skill change.
- Existing files are CRLF; keep them CRLF when editing (the Edit tool preserves line endings). New files may be LF.
- No push and no install-copy sync. Every stage ends with a live-check task: stop there and wait for the user.
- Labels are verb + object, exactly as this plan writes them (spec §15.3); keys exactly as spec §15.9.
- Counts in "Expected" lines are deltas (`+N`), never absolute totals: the suite size at a task's start is whatever the previous task left.

## Review Focus

1. **Hidden footer keys and Tab.** `FOOTER_KEYS = 'under'` draws label-less hotkey Buttons under the footer text; Tab may stop on them invisibly. Task A0 asks the user; if it is unacceptable the const flips to `'inline'` (one line, tests in Task A1 cover both by const).
2. **A y/n under a footer `n`.** The footer is drawn last, so its `n` (page next) would beat a confirm's `n` (no). Every pane with a y/n drops the `p/n` footer item while it is up; Tasks D2 and D3 test that no Button but `*-no` carries `n` then (restore has no y/n), and the band and Session have no `n` besides their own y/n.
3. **One pane at a time.** Opening a Taskmaster pane closes the plugin's other Taskmaster panes; a pane closed by the person must not leave `nav.sidebar` stuck open (Task C1 tests both, and the `ui.close` hook passes every close it does not refuse).
4. **Every row fits.** The Session tab clipped at its right edge. Each pane task has a width test: no drawn row wider than `bodyColumns` at 60 and 100 columns (`widthOf`), on the terminal.
5. **Overlays over live keys.** The sidebar (`1`–`4`) and the phase menu (`0`–`9`) are drawn after the body, so their digits win; opening one closes the other and disarms review's `t` ticking (Task C1 tests the flow; the live checks confirm the paint).

---

## Shell, paths and checks (every task)

Commands are Git Bash with absolute paths. Never `cd`. Each Bash call starts with:

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
```

Mod checks, run after every implementation step:

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
claude plugin test $WT/mods/taskmaster-mods
claude plugin validate --strict $WT/mods/taskmaster-mods
npx -y -p typescript@5.6.3 tsc -p $WT/mods/taskmaster-mods
```

`tsc` reports two known errors (TS2589) in `hooks/handover-guard.ts:404` and `tests/handover-guard.test.ts:296` (Claude Code 2.1.296 types; not ours). "tsc clean" in this plan means those two and no others. Under machine load `claude plugin test` can fail unrelated tests with `timed out after 5000 ms`: re-run once before debugging. To run one file, pass its path: `claude plugin test $WT/mods/taskmaster-mods --file tests/vocab.test.ts` is not supported; run the whole suite and read the named test's line.

If the engine-laid types under `mods/*/.claude-plugin/types/` are missing, lay them with:

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
env -u ANTHROPIC_API_KEY claude -p --model haiku --plugin-dir $WT/mods/rr-tui --plugin-dir $WT/mods/taskmaster-mods "Reply with the single word ok."
```

## Test helpers you will use

- `tests/fixtures/kit-ui.ts`: `testKit(surface?, said?)` (a `Kit` over stub tokens; `said` collects toasts), `walk`, `byKey(tree, key)`, `textsOf(tree)`, `hoversOf(tree)`.
- `tests/fixtures/measure.ts`: `widthOf(node)` (cells across as the terminal lays it out; a plain Button with a hotkey counts `k: label`; absolute boxes count 0), `elementsOf`, `isEl`, `Drawn`.
- `tests/fixtures/tokens.ts`: `STUB_TOKENS` (`surface.page '#141413'`, `raised '#1d1d1b'`, `overlay '#272725'`; `fg.bold '#f5f3ed'`, `default '#d2cfc8'`, `subtle '#a09c95'`, `disabled '#66645f'`; `border.default '#343331'`, `strong '#4d4c48'`, `focus '#5e79e6'`; `signatureText '#8a9eeb'`; `tone.success '#3a9a5b'`, `warning '#c4881d'`, `critical '#d14343'`, `info '#5b8fc7'`; `tint24.*.signature '#4a5590'`; `keycap.signature { bg '#8a9eeb', ink '#0d0d0c' }`).
- `tests/fixtures/world.ts`: `worldOf(on, clock)` records `opened`, `closed`, `toasts`, `copies`, `fills`, `calls`, …; `stateOf(on, seed)` answers `$.state` from memory (call `ui.redraw()` after a press when you seed with it).
- `tests/fixtures/inputs.ts`: `PLUGIN`, `SESSION`, `BAND` (bodyColumns 120, maxRows 12), `pane(requestId)` (bodyColumns 72, focused), `command(name)`.
- Demo data (`hooks/demo.ts`): the bound task `unified-chat-022` (in review, critical, ready for done), handover resumed from `2026-10-04-audit-fixes-landed` (20 h old, tldr `Audit fixes landed on the native branch`, next `Record the merge for tm-audit-030`), queue `unified-chat-022` (1 h), `tm-audit-031` (5 h), `docs-012` (30 h), `ISS-7` (48 h), `DEC-4` (72 h); review phases `Patch 1.9` (active, 3), `Release 2.0` (planned, 2), `Patch 1.8` (done, 0).

## File structure

| File | Responsibility | Stage |
|------|----------------|-------|
| `hooks/vocab.tsx` (new) | The element vocabulary: inks, glyph badges, key ladder, ask row, footer, list item, frame, toolbar pieces, summary, bulk, section, empty, offline, picker, menu, sidebar | A |
| `hooks/nav.ts` (new) | Pane ids and titles, sidebar entries, Esc layer, `RESTORE_BUILT` | A (pure), C (used) |
| `hooks/phases.ts` | `pickerName` added; strip-only helpers removed in D4 | A, D4 |
| `hooks/kit.tsx` | Keeps `kitOf`, `badge`, `fit`, `row`, `label`, `signal`, `rule`, `stepUp`, `HOTKEY`, `KEYCAP_FACE`, `PANEL_TITLE`, `TM_TAG`, `StripView`, `StripHandlers`; the rest leaves in D4 | D4 |
| `hooks/kit-gallery.tsx` | `/tm-kit` becomes the component sheet | A |
| `hooks/band.tsx` | Direction-C band | B |
| `hooks/flows.ts` | `openSidebar`, `toggleSidebar`, `goTo`, `retry`, one-pane opening; close double-count fix | B, C, D3 |
| `hooks/register.tsx` | `NAV` and `SESSION_INFO` atoms, sidebar overlay in every pane, the `ui.close` hook, the pane renders on the new trees | B, C, D |
| `hooks/session.tsx`, `hooks/review.tsx`, `hooks/handovers.tsx` | Rewritten on the vocabulary | D |
| `hooks/review-list.ts`, `hooks/handover-list.ts`, `hooks/model.ts` | `LIST_PAGE_SIZE = 3` | D |
| `hooks/restore/*`, `scanner/*` | Restore (Stage E) | E |
| `types/index.d.ts` | `TmNav`, `TmSessionInfo`; PluginState `nav`, `sessionInfo` | A, B, D1 |

---

## Stage A: The vocabulary

**Goal.** Every element on the component sheet exists as a tested kit function, and `/tm-kit` (demo) shows the sheet so the user can judge it by eye before any screen moves onto it. Nothing the user sees outside `/tm-kit` changes in this stage.

### Task A0: Live probe of the four unproven mechanics (orchestrator + user)

The orchestrator runs this task itself with the user; no subagent. It decides four consts that later tasks read: `FOOTER_KEYS`, `MENU_PLACEMENT`, `SIDEBAR_ESC` (all in `hooks/vocab.tsx`, created in Task A1 with the defaults `'under'`, `'absolute'`, `'refuse'`) and whether close-then-open switching keeps the keyboard (decision S2).

**Files:**
- Create (gitignored scratch): `.superpowers/probes/vocab-probe/.claude-plugin/plugin.json`, `.superpowers/probes/vocab-probe/hooks/hooks.json`, `.superpowers/probes/vocab-probe/hooks/register.tsx`, `.superpowers/probes/vocab-probe/types/index.d.ts`

**Interfaces:**
- Consumes: nothing from taskmaster-mods.
- Produces: four answers, written into the SDD ledger and into Task A1's consts before A1 is dispatched.

- [ ] **Step 1: Write the probe mod**

`.superpowers/probes/vocab-probe/.claude-plugin/plugin.json`:

```json
{ "name": "vocab-probe", "version": "0.1.0", "description": "Throwaway probe for the taskmaster-mods visual rework", "types": "./types/index.d.ts" }
```

`.superpowers/probes/vocab-probe/hooks/hooks.json`:

```json
{ "modules": ["./register.tsx"] }
```

`.superpowers/probes/vocab-probe/types/index.d.ts`:

```ts
// User intent: the probe's one state value — which layer is open over pane A — so Esc refusal and overlays can be tried live.
declare module 'claude-code' {
  interface PluginState {
    'vocab-probe': { layer: 'none' | 'menu' | 'sidebar' }
  }
}
export {}
```

`.superpowers/probes/vocab-probe/hooks/register.tsx`:

```tsx
// User intent: try, live, the four mechanics the taskmaster-mods visual rework relies on — overlay key faces, footer keys
// hidden under text, absolute overlays whose digits win over the body, Esc refused for an open layer, close-then-open switching.
import { atom, read, update } from 'claude-code'
import type { Register } from 'claude-code'

const LAYER = atom({ plugin: 'vocab-probe', key: 'layer' } as const, 'none' as 'none' | 'menu' | 'sidebar')

export const register: Register = on => {
  on('session.start', async ($, e, next) => {
    try {
      await $.command.register({ name: 'probe-vocab', description: 'Open the vocab probe pane A' })
    } catch {
      // registered by an earlier load
    }
    return next(e)
  })

  on('command.run', { command: 'probe-vocab' }, async $ => {
    await $.ui.open({ id: 'probe-a', title: 'Probe A', focus: true, closeOnEscape: true })
    return { text: 'Probe A opened.' }
  })

  on('ui.close', async ($, e, next) => {
    if (e.id !== 'probe-a' || e.origin.kind !== 'person') return next(e)
    if ((await read($, LAYER)) === 'none') return next(e)
    await update($, LAYER, () => 'none' as const)
    return { value: undefined }
  }).catch(($, e, next) => next(e))

  on('ui.render', { component: 'Pane', requestId: 'probe-b' }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    return (
      <Box flexDirection="column">
        <Text>Probe B. Did pane A close (no tab row)? Does q work right away (keyboard kept)?</Text>
        <Button key="b-q" plain hotkey="q" label="q toasts" onPress={() => $.ui.toast('B: q pressed')} />
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: 'probe-a' }, async ($, e) => {
    const { Box, Text, Button } = $.ui.resolve(e)
    const layer = await read($, LAYER)
    const say = (what: string) => () => $.ui.toast(`A: ${what}`)
    const face = (key: string, fill: boolean, color: string) => (
      <Box position="absolute" left={0} top={0}>
        <Text {...(fill ? { backgroundColor: 'blue' } : {})} color={color} bold>{` ${key} `}</Text>
      </Box>
    )
    const cap = (id: string, key: string, label: string, fill: boolean, color: string) => (
      <Box key={`${id}-box`} flexDirection="row" flexShrink={0} {...(fill ? { backgroundColor: 'blue', paddingRight: 1 } : {})}>
        <Button key={id} plain hotkey={key} label={label} onPress={say(label)}>
          {fill ? <Text color="white" bold>{label}</Text> : null}
        </Button>
        {face(key, fill, color)}
      </Box>
    )
    const rows = ['first row', 'second row', 'third row', 'fourth row', 'fifth row'].map(text => <Text>{`   ${text} (d below the overlay: does it still fire?)`}</Text>)
    const menu = (
      <Box flexDirection="column" borderStyle="round" backgroundColor="gray" paddingX={1} width={30}>
        {['zero', 'one', 'two'].map((name, i) => (
          <Box key={`m${i}-box`} flexDirection="row">
            <Button key={`m${i}`} plain hotkey={String(i)} label={`${name} pick`} onPress={say(`menu ${name}`)} />
            {face(String(i), false, 'gray')}
          </Box>
        ))}
        <Text>Esc should close only this list</Text>
      </Box>
    )
    const sidebar = (
      <Box flexDirection="column" borderStyle="round" backgroundColor="gray" paddingX={1} width={30}>
        <Box key="s1-box" flexDirection="row">
          <Button key="s1" plain hotkey="1" label="stay in A" autoFocus onPress={() => void update($, LAYER, () => 'none' as const)} />
          {face('1', false, 'white')}
        </Box>
        <Box key="s2-box" flexDirection="row">
          <Button
            key="s2"
            plain
            hotkey="2"
            label="switch to B"
            onPress={async () => {
              await update($, LAYER, () => 'none' as const)
              await $.ui.close({ id: 'probe-a' })
              await $.ui.open({ id: 'probe-b', title: 'Probe B', focus: true, closeOnEscape: true })
            }}
          />
          {face('2', false, 'white')}
        </Box>
        <Text>Esc should close only this sidebar</Text>
      </Box>
    )
    const footerKeys = ['j', 'k', 'm', 'f'].map(key => (
      <Button key={`key:${key}`} plain hotkey={key} label="" onPress={key === 'm' ? () => void update($, LAYER, l => (l === 'sidebar' ? 'none' : 'sidebar')) : key === 'f' ? () => void update($, LAYER, l => (l === 'menu' ? 'none' : 'menu')) : say(`footer ${key}`)} />
    ))
    return (
      <Box flexDirection="column">
        <Text>1. Key faces: primary (filled), secondary, destructive (red letter), then a row with d beneath the overlays.</Text>
        <Box flexDirection="row" columnGap={3}>
          {cap('p', 'd', 'mark task done', true, 'white')}
          {cap('s', 'c', 'copy handover', false, 'cyan')}
          {cap('x', 'x', 'close handover', false, 'red')}
        </Box>
        <Text>2. Footer below: j k m f work? Tab: does the ring stop on invisible footer keys?</Text>
        <Box flexDirection="column">
          {rows}
          {layer === 'menu' ? (
            <Box position="absolute" top={0} left={2}>
              {menu}
            </Box>
          ) : null}
        </Box>
        <Box flexDirection="row" width={60}>
          {footerKeys}
          <Box position="absolute" top={0} left={0} width={60}>
            <Text color="gray">{'j/k move · f phases · m sidebar · esc close'.padEnd(60)}</Text>
          </Box>
        </Box>
        {layer === 'sidebar' ? (
          <Box position="absolute" top={0} left={0}>
            {sidebar}
          </Box>
        ) : null}
      </Box>
    )
  })
}
```

- [ ] **Step 2: Validate it**

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
claude plugin validate --strict $WT/.superpowers/probes/vocab-probe
```

Expected: ✔ (warnings about colour names are fine; this mod is throwaway).

- [ ] **Step 3: Ask the user to run it and answer**

Tell the user to start `claude --plugin-dir C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux/.superpowers/probes/vocab-probe` in a terminal at least 120 columns wide (fullscreen layout), run `/probe-vocab`, and answer:

1. Do the three keys read ` d mark task done` (filled), ` c copy handover`, ` x close handover` with no stray `:` showing?
2. Footer: do `j`, `k` toast, `f` open the list, `m` open the sidebar? Does the footer read as plain text (no `j:` visible)? Does Tab stop on invisible footer keys, and is that acceptable?
3. With the list open (`f`): does it paint cleanly over the rows; do `0`/`1`/`2` pick; does Esc close only the list and keep pane A?
4. With the sidebar open (`m`): does it paint over the left of the pane; does `1` close it; does Esc close only the sidebar; does `2` switch to pane B with **no tab row**, and does `q` work in B at once?

- [ ] **Step 4: Record the answers**

Map the answers onto the consts (Task A1 creates them; set them in A1's code before dispatching it):

| Answer | Const |
|--------|-------|
| 1 shows a stray `:` | in A1's `actionKey`, give the overlay Text `backgroundColor={groundOf(k, on)}` on every rung (not only the primary) |
| 2 footer keys fail, or Tab stops are unacceptable | `FOOTER_KEYS = 'inline'` |
| 3 the list paints badly | `MENU_PLACEMENT = 'swap'` |
| 3 or 4 Esc closes the pane | `SIDEBAR_ESC = 'pane'` |
| 4 a tab row shows, or B lacks the keyboard | stop and ask the user (decision S2 needs another form) |

Append to `.superpowers/sdd/2026-10-11-mods-ux/progress.md` (the ledger the SDD skill keeps):

```
Task A0 (visual rework): probe answers 1:<…> 2:<…> 3:<…> 4:<…> → FOOTER_KEYS=<…> MENU_PLACEMENT=<…> SIDEBAR_ESC=<…>
```

No commit (the probe is gitignored scratch).

---

### Task A1: Inks, glyph badges, the key ladder, the ask row, the footer

**Files:**
- Create: `mods/taskmaster-mods/hooks/vocab.tsx`
- Create: `mods/taskmaster-mods/tests/vocab.test.ts`

**Interfaces:**
- Consumes: `HOTKEY`, `KEYCAP_FACE`, `Kit`, `Piece`, `stepUp` (hooks/kit.tsx); `truncate` (hooks/model.ts); `RrGround` (hooks/rr.ts).
- Produces (exact):
  - `type Ink = 'bold' | 'default' | 'subtle' | 'disabled' | 'faint'`; `inkOf(k: Kit, ink: Ink): string`
  - `type Seg = { readonly text: string; readonly color: string; readonly bold?: boolean }`; `line(k: Kit, segs: readonly Seg[], width: number): Piece` (cuts across segments, never wider than `width`)
  - `BADGE_W = 15`; `BADGES: Readonly<Record<string, { glyph; tone; bold }>>`; `glyphBadge(k, word, a?: { pad?: boolean; dim?: boolean }): Piece`; `statusBadgeWord(status: string): string`; `badgeInk(k, word): string`
  - `type Rung = 'primary' | 'secondary' | 'destructive'`; `type ActionSpec = { id; key; label; rung?; dim?; autoFocus?; on?; onPress(press) }`; `actionKey(k, a: ActionSpec): Piece`; `actionRows(k, keys: readonly Piece[], width: number): RenderNode[]` (gap 3, wraps)
  - `askRow(k, a: { id; question; onYes; onNo; width; on? }): RenderNode[]` (keys `<id>-yes`, `<id>-no`; `n` autoFocus)
  - `type FooterKey = { readonly key: string; readonly onPress: () => void; readonly id?: string }`; `type FooterItem = { readonly keys: string; readonly word: string; readonly press?: readonly FooterKey[] }`; `FOOTER_KEYS: 'under' | 'inline'`; `hints(k, items, width): RenderNode`; `footer(k, items, width): RenderNode` (footer Buttons keyed `id` when given, else `key:<letter>`)
  - `MENU_PLACEMENT: 'absolute' | 'swap'` and `SIDEBAR_ESC: 'refuse' | 'pane'` (declared here so every later task imports them from one place)

- [ ] **Step 1: Write the failing tests**

`mods/taskmaster-mods/tests/vocab.test.ts`:

```ts
// User intent: pin the visual rework's element vocabulary (spec §15.2–15.3) — inks, glyph badges, the five-rung key ladder,
// labelled actions, the y/n row and the footer — so every screen built on it shows the approved prototype's parts.
import { describe, expect, test } from 'claude-code/testing'

import {
  actionKey,
  actionRows,
  askRow,
  BADGE_W,
  FOOTER_KEYS,
  footer,
  glyphBadge,
  hints,
  inkOf,
  line,
  statusBadgeWord,
} from '../hooks/vocab'
import { byKey, hoversOf, testKit, textsOf, walk } from './fixtures/kit-ui'
import { widthOf } from './fixtures/measure'
import { STUB_TOKENS as T } from './fixtures/tokens'

const noop = (): void => undefined

describe('inks and lines', () => {
  test('the prototype inks are RR tokens: faint is border.strong', () => {
    const k = testKit()
    expect([inkOf(k, 'bold'), inkOf(k, 'default'), inkOf(k, 'subtle'), inkOf(k, 'disabled'), inkOf(k, 'faint')]).toEqual([
      T.fg.bold,
      T.fg.default,
      T.fg.subtle,
      T.fg.disabled,
      T.border.strong,
    ])
  })

  test('a line cuts across its segments and is never wider than its room', () => {
    const k = testKit()
    const segs = [
      { text: 'next  ', color: T.signatureText },
      { text: 'Record the merge for tm-audit-030', color: T.fg.default },
    ]
    expect(line(k, segs, 80).width).toBe(39)
    const cut = line(k, segs, 12)
    expect(cut.width).toBe(12)
    expect(textsOf(cut.el)).toEqual(['next  ', 'Recor…'])
    expect(widthOf(cut.el)).toBe(12)
  })
})

describe('glyph badges', () => {
  test('glyph + upper-case word in the tone, padded to the label column in a list; never a fill', () => {
    const k = testKit()
    const b = glyphBadge(k, 'blocked', { pad: true })
    expect(b.width).toBe(BADGE_W)
    expect(walk(b.el)[0]).toMatchObject({ type: 'Text', props: { color: T.tone.critical, bold: true }, children: ['▲ BLOCKED      '] })
    expect(walk(b.el)[0]?.props?.backgroundColor).toBeUndefined()
    expect(walk(glyphBadge(k, 'OPEN').el)[0]).toMatchObject({ props: { color: T.fg.subtle, bold: false }, children: ['○ OPEN'] })
    expect(walk(glyphBadge(k, 'RESUMED FROM').el)[0]).toMatchObject({ props: { color: T.signatureText }, children: ['↳ RESUMED FROM'] })
    expect(walk(glyphBadge(k, 'IN REVIEW', { dim: true }).el)[0]).toMatchObject({ props: { color: T.border.strong, bold: false } })
    expect(walk(glyphBadge(k, 'whatever').el)[0]?.children).toEqual(['· WHATEVER'])
  })

  test('a task status reads as its badge word', () => {
    expect([statusBadgeWord('in-review'), statusBadgeWord('in-progress'), statusBadgeWord('')]).toEqual(['IN REVIEW', 'IN PROGRESS', 'UNKNOWN'])
  })
})

describe('the key ladder', () => {
  test('primary: one plain Button with its hotkey and verb + object label, filled with the RR keycap, ink label, glyph over k:', () => {
    const k = testKit()
    const key = actionKey(k, { id: 'done', key: 'd', label: 'mark task done', rung: 'primary', onPress: noop })
    expect(byKey(key.el, 'done')).toMatchObject({ type: 'Button', props: { key: 'done', hotkey: 'd', label: 'mark task done', plain: true } })
    expect(byKey(key.el, 'done-box')?.props).toMatchObject({ backgroundColor: T.keycap.signature.bg, paddingRight: 1 })
    expect(textsOf(key.el)).toEqual(['mark task done', ' d '])
    expect(walk(key.el).some(e => e.props?.position === 'absolute')).toBe(true)
    expect(key.width).toBe(3 + 'mark task done'.length + 1)
    expect(widthOf(key.el)).toBe(key.width)
  })

  test('secondary: the letter bold in signature ink, no fill; destructive: the letter in warning ink', () => {
    const k = testKit()
    const copy = actionKey(k, { id: 'copy', key: 'c', label: 'copy handover', onPress: noop })
    expect(byKey(copy.el, 'copy-box')?.props?.backgroundColor).toBeUndefined()
    const glyph = walk(copy.el).find(e => e.type === 'Text' && (e.children ?? []).includes(' c '))
    expect(glyph?.props).toMatchObject({ color: T.signatureText, bold: true })
    expect(glyph?.props?.backgroundColor).toBeUndefined()
    expect(copy.width).toBe(3 + 'copy handover'.length)
    const close = actionKey(k, { id: 'close', key: 'x', label: 'close handover', rung: 'destructive', onPress: noop })
    expect(walk(close.el).find(e => (e.children ?? []).includes(' x '))?.props?.color).toBe(T.tone.warning)
  })

  test('disabled: dim label and glyph, and a press says why instead of acting', async () => {
    const said: string[] = []
    const k = testKit('terminal', said)
    let pressed = 0
    const off = actionKey(k, { id: 'bulk-done', key: 'b', label: 'mark 0 tasks done', rung: 'primary', dim: 'Nothing selected', onPress: () => void (pressed += 1) })
    const button = byKey(off.el, 'bulk-done')
    expect(button?.props?.dimColor).toBe(true)
    expect(byKey(off.el, 'bulk-done-box')?.props?.backgroundColor).toBeUndefined()
    expect(walk(off.el).find(e => (e.children ?? []).includes(' b '))?.props?.color).toBe(T.fg.disabled)
    expect(off.width).toBe(3 + 'mark 0 tasks done'.length)
  })

  test('a bad hotkey throws naming the key; hovers change colour or background only, never position', () => {
    const k = testKit()
    expect(() => actionKey(k, { id: 'x', key: 'R', label: 'x', onPress: noop })).toThrow(/key x: hotkey "R"/)
    const keys = [actionKey(k, { id: 'a', key: 'a', label: 'send task to agent', onPress: noop }), actionKey(k, { id: 'd', key: 'd', label: 'mark task done', rung: 'primary', onPress: noop })]
    for (const hover of keys.flatMap(p => hoversOf(p.el))) {
      for (const prop of Object.keys(hover)) expect(['backgroundColor', 'color', 'bold']).toContain(prop)
    }
  })

  test('desktop: the letter sits beside the native button, the primary is the native primary', () => {
    const k = testKit('desktop')
    const key = actionKey(k, { id: 'done', key: 'd', label: 'mark task done', rung: 'primary', onPress: noop })
    expect(byKey(key.el, 'done')?.props).toMatchObject({ hotkey: 'd', variant: 'primary' })
    expect(walk(key.el).some(e => e.props?.position === 'absolute')).toBe(false)
  })

  test('action rows keep 3 cells between keys and wrap rather than drop one', () => {
    const k = testKit()
    const keys = ['mark task done', 'send task to agent', 'open task', 'task in viewer'].map((label, i) =>
      actionKey(k, { id: `k${i}`, key: 'abcd'[i] ?? 'z', label, onPress: noop }),
    )
    const wide = actionRows(k, keys, 200)
    expect(wide).toHaveLength(1)
    expect(walk(wide[0])[0]?.props?.columnGap).toBe(3)
    const narrow = actionRows(k, keys, 40)
    expect(narrow.length).toBeGreaterThan(1)
    for (const row of narrow) expect(widthOf(row)).toBeLessThanOrEqual(40)
    expect(narrow.flatMap(r => walk(r)).filter(e => e.type === 'Button')).toHaveLength(4)
  })
})

describe('the ask row', () => {
  test('the question in warning ink, then y yes and n no (n focused); one row when it fits, else two', () => {
    const k = testKit()
    const rows = askRow(k, { id: 'band', question: 'mark unified-chat-022 done?', onYes: noop, onNo: noop, width: 80 })
    expect(rows).toHaveLength(1)
    expect(byKey(rows[0], 'band-yes')?.props).toMatchObject({ hotkey: 'y', label: 'yes' })
    expect(byKey(rows[0], 'band-no')?.props).toMatchObject({ hotkey: 'n', label: 'no', autoFocus: true })
    expect(walk(rows[0]).find(e => e.type === 'Text' && (e.children ?? []).includes('mark unified-chat-022 done?'))?.props).toMatchObject({ color: T.tone.warning, bold: true })
    expect(askRow(k, { id: 'band', question: 'mark unified-chat-022 done?', onYes: noop, onNo: noop, width: 30 })).toHaveLength(2)
  })
})

describe('the footer', () => {
  const items = (calls: string[]) => [
    { keys: 'j/k', word: 'move', press: [{ key: 'j', onPress: () => void calls.push('j') }, { key: 'k', onPress: () => void calls.push('k') }] },
    { keys: 's', word: 'select', press: [{ key: 's', onPress: () => void calls.push('s') }] },
    { keys: 'esc', word: 'close' },
  ]

  test('one Button per listed key, keyed key:<letter>; the line reads keys subtle, words disabled, · faint', () => {
    const k = testKit()
    const tree = footer(k, items([]), 60)
    for (const key of ['j', 'k', 's']) expect(byKey(tree, `key:${key}`)?.props).toMatchObject({ hotkey: key, plain: true })
    expect(walk(tree).filter(e => e.type === 'Button')).toHaveLength(3)
    if (FOOTER_KEYS === 'under') {
      expect(textsOf(tree).slice(0, 7)).toEqual(['j/k', ' move', ' · ', 's', ' select', ' · ', 'esc'])
      expect(walk(tree).some(e => e.props?.position === 'absolute')).toBe(true)
      for (const b of walk(tree).filter(e => e.type === 'Button')) expect(b.props?.label).toBe('')
    }
  })

  test('the hint line alone is cut to its room; a bad footer key throws', () => {
    const k = testKit()
    const short = hints(k, items([]), 10)
    expect(textsOf(short).join('').length).toBeLessThanOrEqual(10)
    expect(() => footer(k, [{ keys: '[', word: 'x', press: [{ key: '[', onPress: noop }] }], 40)).toThrow(/hotkey "\["/)
  })
})
```

- [ ] **Step 2: Run the suite to see the new tests fail**

Run the mod checks. Expected: `tests/vocab.test.ts` fails to load (`Cannot find module '../hooks/vocab'`); every other test passes.

- [ ] **Step 3: Write `hooks/vocab.tsx`**

```tsx
// User intent: the one element vocabulary every taskmaster-mods screen draws with (visual rework 2026-10-12, spec §15) —
// inks, glyph badges, the five-rung key ladder, labelled actions, the footer, list items, the frame, the phase picker and the
// Taskmaster sidebar — so every screen is built from the approved prototype's parts and nothing else.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode, UiPressArgument } from 'claude-code'

import { HOTKEY, KEYCAP_FACE, type Kit, type Piece, stepUp } from './kit'
import { truncate } from './model'
import type { RrGround } from './rr'

/** Footer keys (spec §15.8 S6, Task A0): `under` hides label-less hotkey Buttons under the footer text; `inline` draws `k: word`. */
export const FOOTER_KEYS: 'under' | 'inline' = 'under'
/** The phase list (S5, Task A0): `absolute` floats it over the list; `swap` replaces the list while it is open. */
export const MENU_PLACEMENT: 'absolute' | 'swap' = 'absolute'
/** Esc while the sidebar or the phase list is open (S4, Task A0): `refuse` closes that layer; `pane` lets Esc close the pane. */
export const SIDEBAR_ESC: 'refuse' | 'pane' = 'refuse'

// ── Inks (spec §15.2): the prototype's text classes as RR tokens ─────────────────────────────────────────────────────

export type Ink = 'bold' | 'default' | 'subtle' | 'disabled' | 'faint'

export function inkOf(k: Kit, ink: Ink): string {
  return ink === 'faint' ? k.t.border.strong : k.t.fg[ink]
}

/** One run of text in a colour. */
export type Seg = { readonly text: string; readonly color: string; readonly bold?: boolean }

/** Segments side by side, cut where `width` runs out (the last one shown ends in `…`): never wider than its room. */
export function line(k: Kit, segs: readonly Seg[], width: number): Piece {
  const { Box, Text } = k.ui
  const out: RenderNode[] = []
  let used = 0
  for (const s of segs) {
    if (used >= width) break
    const cut = truncate(s.text, width - used)
    if (cut === '') continue
    used += cut.length
    out.push(
      <Text color={s.color} bold={s.bold === true}>
        {cut}
      </Text>,
    )
  }
  return { el: <Box flexDirection="row">{out}</Box>, width: used }
}

// ── Glyph badges (component sheet BADGE): symbol + word in the tone, never a fill ─────────────────────────────────────

export const BADGE_W = 15
type BadgeTone = 'critical' | 'warning' | 'info' | 'success' | 'signature' | 'subtle' | 'disabled'
export type BadgeSpec = { readonly glyph: string; readonly tone: BadgeTone; readonly bold: boolean }
export const BADGES: Readonly<Record<string, BadgeSpec>> = {
  BLOCKED: { glyph: '▲', tone: 'critical', bold: true },
  ACTION: { glyph: '◆', tone: 'warning', bold: true },
  'IN REVIEW': { glyph: '●', tone: 'info', bold: true },
  'IN PROGRESS': { glyph: '●', tone: 'info', bold: true },
  TODO: { glyph: '○', tone: 'subtle', bold: false },
  DONE: { glyph: '✓', tone: 'success', bold: true },
  ARCHIVED: { glyph: '✓', tone: 'disabled', bold: false },
  INTERRUPTED: { glyph: '▲', tone: 'critical', bold: true },
  WIP: { glyph: '◆', tone: 'warning', bold: true },
  'HANDED OFF': { glyph: '●', tone: 'info', bold: true },
  UNKNOWN: { glyph: '?', tone: 'subtle', bold: false },
  OPEN: { glyph: '○', tone: 'subtle', bold: false },
  CLOSED: { glyph: '✓', tone: 'disabled', bold: false },
  'RESUMED FROM': { glyph: '↳', tone: 'signature', bold: false },
  'WRITTEN HERE': { glyph: '↳', tone: 'signature', bold: false },
  'FOR THIS TASK': { glyph: '↳', tone: 'signature', bold: false },
  RUNNING: { glyph: '○', tone: 'success', bold: false },
  REFUSED: { glyph: '▲', tone: 'critical', bold: true },
}
const UNKNOWN_BADGE: BadgeSpec = { glyph: '·', tone: 'subtle', bold: false }

function toneInk(k: Kit, tone: BadgeTone): string {
  if (tone === 'subtle') return k.t.fg.subtle
  if (tone === 'disabled') return k.t.fg.disabled
  if (tone === 'signature') return k.t.signatureText
  return k.t.tone[tone]
}

/** The ink a badge word draws in (a tally's count follows it). */
export const badgeInk = (k: Kit, word: string): string => toneInk(k, (BADGES[word.toUpperCase()] ?? UNKNOWN_BADGE).tone)

/** `▲ BLOCKED`: the glyph and the upper-case word in its tone; `pad` fills the label column; `dim` draws it faint. */
export function glyphBadge(k: Kit, word: string, a: { readonly pad?: boolean; readonly dim?: boolean } = {}): Piece {
  const { Text } = k.ui
  const upper = word.toUpperCase()
  const spec = BADGES[upper] ?? UNKNOWN_BADGE
  const text = `${spec.glyph} ${upper}`
  const shown = a.pad === true ? text.padEnd(BADGE_W) : text
  const dim = a.dim === true
  return {
    el: (
      <Text color={dim ? k.t.border.strong : toneInk(k, spec.tone)} bold={!dim && spec.bold}>
        {shown}
      </Text>
    ),
    width: shown.length,
  }
}

/** A task status as its badge word: `in-review` → `IN REVIEW`; '' → `UNKNOWN`. */
export const statusBadgeWord = (status: string): string => (status === '' ? 'UNKNOWN' : status.toUpperCase().replace(/-/g, ' '))

// ── The key ladder (spec §15.3) ─────────────────────────────────────────────────────────────────────────────────────

export type Rung = 'primary' | 'secondary' | 'destructive'
export type ActionSpec = {
  /** The Button's key; its wrapper Box is `<id>-box`. */
  readonly id: string
  /** One digit or one lowercase letter (anything else throws). */
  readonly key: string
  /** Verb + object, naming what it acts on: `mark task done`, `copy handover`. */
  readonly label: string
  /** Default secondary. One primary per context; destructive keys go last in their row and ask y/n. */
  readonly rung?: Rung
  /** Present: drawn disabled (rung 4); a press toasts this reason instead of acting. */
  readonly dim?: string
  readonly autoFocus?: boolean
  /** The ground it sits on (the hover steps one up from it); default page. */
  readonly on?: RrGround
  readonly onPress: (press: UiPressArgument) => void
}

function checkKey(id: string, key: string): void {
  if (!HOTKEY.test(key)) throw new Error(`taskmaster-mods: key ${id}: hotkey "${key}" is not one digit or one lowercase letter`)
}

/**
 * An action key: one plain Button (hotkey + label) with a 3-cell ` k ` glyph laid over the engine's `k: ` prefix. Primary
 * fills the RR keycap behind the whole key; secondary is a bold signature letter; destructive a warning letter; disabled dim.
 */
export function actionKey(k: Kit, a: ActionSpec): Piece {
  const { Box, Text, Button } = k.ui
  const { t } = k
  checkKey(a.id, a.key)
  const rung = a.rung ?? 'secondary'
  const why = a.dim
  const on = a.on ?? 'page'
  const filled = rung === 'primary' && why === undefined
  const letter = why !== undefined ? t.fg.disabled : filled ? t.keycap.signature.ink : rung === 'destructive' ? t.tone.warning : t.signatureText
  const button = (
    <Button
      key={a.id}
      plain
      hotkey={a.key}
      label={a.label}
      {...(rung === 'primary' ? { variant: 'primary' as const } : {})}
      {...(why !== undefined ? { dimColor: true } : {})}
      {...(a.autoFocus === true ? { autoFocus: true as const } : {})}
      hover={{ bold: true, color: filled ? t.keycap.signature.ink : t.fg.bold }}
      onPress={why === undefined ? a.onPress : () => k.say(why)}
    >
      {filled ? (
        <Text color={t.keycap.signature.ink} bold>
          {a.label}
        </Text>
      ) : null}
    </Button>
  )
  if (k.surface !== 'terminal') {
    // A desktop draws its own native button: the letter sits beside it, never over it.
    return {
      el: (
        <Box key={`${a.id}-box`} flexDirection="row" columnGap={1} flexShrink={0}>
          <Text color={why !== undefined ? t.fg.disabled : t.signatureText} bold>
            {a.key}
          </Text>
          {button}
        </Box>
      ),
      width: 2 + a.label.length,
    }
  }
  const glyph =
    KEYCAP_FACE === 'overlay' ? (
      <Box position="absolute" left={0} top={0}>
        <Text {...(filled ? { backgroundColor: t.keycap.signature.bg } : {})} color={letter} bold={why === undefined}>
          {` ${a.key} `}
        </Text>
      </Box>
    ) : null
  return {
    el: (
      <Box
        key={`${a.id}-box`}
        flexDirection="row"
        flexShrink={0}
        {...(filled ? { backgroundColor: t.keycap.signature.bg, paddingRight: 1 } : {})}
        hover={{ backgroundColor: filled ? t.keycap.signature.bg : stepUp(t, on) }}
      >
        {button}
        {glyph}
      </Box>
    ),
    width: 3 + a.label.length + (filled ? 1 : 0),
  }
}

/** Keys in rows of `width`, 3 cells apart, wrapping to a new row rather than dropping a key. */
export function actionRows(k: Kit, keys: readonly Piece[], width: number): RenderNode[] {
  const { Box } = k.ui
  const rows: Piece[][] = []
  let used = 0
  for (const key of keys) {
    const last = rows[rows.length - 1]
    if (last !== undefined && used + 3 + key.width <= width) {
      last.push(key)
      used += 3 + key.width
    } else {
      rows.push([key])
      used = key.width
    }
  }
  return rows.map(r => (
    <Box flexDirection="row" columnGap={3} alignItems="flex-start">
      {r.map(p => p.el)}
    </Box>
  ))
}

/** A y/n: the question in bold warning ink, then `y yes` and `n no` (n holds the focus: Enter cancels). Two rows when narrow. */
export function askRow(
  k: Kit,
  a: { readonly id: string; readonly question: string; readonly onYes: () => void; readonly onNo: () => void; readonly width: number; readonly on?: RrGround },
): RenderNode[] {
  const { Box, Text } = k.ui
  const on = a.on ?? 'page'
  const yes = actionKey(k, { id: `${a.id}-yes`, key: 'y', label: 'yes', on, onPress: a.onYes })
  const no = actionKey(k, { id: `${a.id}-no`, key: 'n', label: 'no', on, autoFocus: true, onPress: a.onNo })
  const keys = (
    <Box flexDirection="row" columnGap={3}>
      {yes.el}
      {no.el}
    </Box>
  )
  const pair = yes.width + 3 + no.width
  const text = truncate(a.question, Math.max(4, a.width))
  const question = (
    <Text color={k.t.tone.warning} bold>
      {text}
    </Text>
  )
  if (text.length + 3 + pair > a.width) return [question, keys]
  return [
    <Box flexDirection="row" columnGap={3}>
      {question}
      {keys}
    </Box>,
  ]
}

// ── The footer (component sheet FOOTER): nav and meta keys, never capped ──────────────────────────────────────────────

/** A footer key's Button: keyed `id` when given (review's `focus-next`, handovers' `move-down`), else `key:<letter>`. */
export type FooterKey = { readonly key: string; readonly onPress: () => void; readonly id?: string }
/** One footer entry: what it shows (`j/k`, `move`) and the Buttons that carry its hotkeys (none: another element has the key). */
export type FooterItem = { readonly keys: string; readonly word: string; readonly press?: readonly FooterKey[] }

/** The footer's words alone, cut to `width`: keys subtle, words disabled, ` · ` faint. */
export function hints(k: Kit, items: readonly FooterItem[], width: number): RenderNode {
  const { Box, Text } = k.ui
  const room = Math.max(1, width)
  const out: RenderNode[] = []
  let used = 0
  const push = (text: string, color: string): void => {
    if (used >= room) return
    const cut = truncate(text, room - used)
    used += cut.length
    out.push(<Text color={color}>{cut}</Text>)
  }
  items.forEach((item, i) => {
    if (i > 0) push(' · ', k.t.border.strong)
    push(item.keys, k.t.fg.subtle)
    push(` ${item.word}`, k.t.fg.disabled)
  })
  return <Box flexDirection="row">{out}</Box>
}

/** A pane's last row: the hints, and every listed key pressable (FOOTER_KEYS says how the keys are drawn). */
export function footer(k: Kit, items: readonly FooterItem[], width: number): RenderNode {
  const { Box, Text, Button } = k.ui
  const keys = items.flatMap(item => item.press ?? [])
  for (const p of keys) checkKey(p.id ?? `key:${p.key}`, p.key)
  if (FOOTER_KEYS === 'inline') {
    return (
      <Box flexDirection="row" columnGap={2} flexWrap="wrap">
        {items.map(item => {
          const press = item.press ?? []
          if (press.length === 0) return <Text color={k.t.fg.subtle}>{`${item.keys} ${item.word}`}</Text>
          return (
            <Box flexDirection="row">
              {press.map((p, n) => (
                <Button key={p.id ?? `key:${p.key}`} plain dimColor hotkey={p.key} label={n === press.length - 1 ? item.word : ''} onPress={p.onPress} />
              ))}
            </Box>
          )
        })}
      </Box>
    )
  }
  const room = Math.max(1, width, keys.length * 3)
  return (
    <Box flexDirection="row" width={room}>
      {keys.map(p => (
        <Button key={p.id ?? `key:${p.key}`} plain hotkey={p.key} label="" onPress={p.onPress} />
      ))}
      <Box position="absolute" top={0} left={0} width={room}>
        <Box flexDirection="row" width={room}>
          {hints(k, items, Math.max(1, width))}
          <Box flexGrow={1} />
        </Box>
      </Box>
    </Box>
  )
}
```

The `under` footer paints the hints over a ground-less spacer; the hidden Buttons' `k: ` marks are covered because the absolute box spans the whole room. If Task A0 answered that a stray mark shows through, give the inner `Box` `backgroundColor={k.t.surface.page}`.

- [ ] **Step 4: Run the checks**

Expected: `tests/vocab.test.ts` passes (+15 tests); everything else unchanged; validate ✔; tsc clean.

- [ ] **Step 5: Commit**

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
git -C $WT add mods/taskmaster-mods/hooks/vocab.tsx mods/taskmaster-mods/tests/vocab.test.ts
git -C $WT commit -m "feat(taskmaster-mods): the rework vocabulary — inks, glyph badges, the key ladder, the ask row, the footer"
```

---

### Task A2: The list item, frame, toolbar pieces, summary, bulk, section, empty and offline rows

**Files:**
- Modify: `mods/taskmaster-mods/hooks/vocab.tsx` (append; add `PANEL_TITLE` and `signal`-free imports as below)
- Modify: `mods/taskmaster-mods/hooks/model.ts` (one constant)
- Create: `mods/taskmaster-mods/tests/vocab-list.test.ts`

**Interfaces:**
- Consumes: Task A1's `line`, `Seg`, `glyphBadge`, `badgeInk`, `BADGE_W`, `actionRows`.
- Produces (exact):
  - `MARK_W = 2`, `LABEL_COL = 17`, `CONTENT_COL = 19`
  - `type MarkSpec = { readonly key: string; readonly selected: boolean; readonly onPress: () => void; readonly dim?: string }`; `mark(k, m: MarkSpec | null): Piece` (a no-hotkey Button keyed `m.key`, `□`/`■`)
  - `type HeadSpec = { mark: MarkSpec | null; badge: string; ident?: string; title: string; meta?: string; age: string; dim?: boolean; pick?: { key: string; onPress: () => void } }`; `itemHead(k, a: HeadSpec, width): RenderNode` (exactly `width` cells when it fits)
  - `itemLine(k, segs: readonly Seg[], width): RenderNode` (content column); `fieldSegs(k, name, value): Seg[]` (`name` in signature ink + 2 spaces, value default)
  - `item(k, head, lines: readonly (readonly Seg[])[], width): RenderNode[]`
  - `focusedItem(k, a: { id: string; head: HeadSpec; lines: readonly (readonly Seg[])[]; extra?: readonly RenderNode[]; actions: readonly RenderNode[] }, width): RenderNode` (Box keyed `card:<id>`, round border in `border.focus`)
  - `itemRule(k, width): RenderNode`; `type ListEntry = { readonly focused: boolean; readonly nodes: readonly RenderNode[] }`; `listing(k, entries, width): RenderNode[]` (a rule between two collapsed neighbours only)
  - `type Health = { readonly kind: RrSignalKind; readonly word: string }`; `paneFrame(k, a: { name: string; health?: Health; width: number }, children: readonly RenderNode[], overlay?: RenderNode | null): RenderNode`
  - `paneToolbar(k, a: { left: Piece; right: readonly Piece[]; width: number }): RenderNode`; `pagerPiece(k, a: { page; pages; onTurn(page) }): Piece` (Buttons `page-prev`, `page-next`, no hotkey); `togglePiece(k, a: { id; label; on: boolean; onPress }): Piece`; `refreshPiece(k, a: { label: string; onPress }): Piece` (Button `refresh`)
  - `type Tally = { readonly word: string; readonly count: number }`; `summaryRow(k, sentence, tallies, width): RenderNode`
  - `type Scope = { readonly id: string; readonly label: string; readonly onPress: () => void }`; `bulkRow(k, a: { keys: readonly Piece[]; scopes: readonly Scope[]; width }): RenderNode`
  - `sectionRow(k, label, note, width): RenderNode`; `labelRow(k, label, segs, width): RenderNode` (Session's label column); `underLabel(k, rows: readonly RenderNode[]): RenderNode` (content-column indent for action rows)
  - `emptyRow(k, sentence, way: Piece | null, width): RenderNode`; `offlineRow(k, detail, retry: Piece, width): RenderNode`
  - `model.ts`: `export const LIST_PAGE_SIZE = 3`

- [ ] **Step 1: Write the failing tests**

`mods/taskmaster-mods/tests/vocab-list.test.ts`:

```ts
// User intent: pin the list and frame elements of the rework (spec §15.6–15.7) — the 3-line item in its label column, the
// focused card's box, the dim rule between items, the pane frame with its title and health, the toolbar, summary, bulk,
// section, empty and offline rows — each exactly as wide as its room.
import { describe, expect, test } from 'claude-code/testing'

import {
  actionKey,
  bulkRow,
  CONTENT_COL,
  emptyRow,
  fieldSegs,
  focusedItem,
  item,
  itemHead,
  itemRule,
  labelRow,
  listing,
  offlineRow,
  pagerPiece,
  paneFrame,
  paneToolbar,
  refreshPiece,
  sectionRow,
  summaryRow,
  togglePiece,
} from '../hooks/vocab'
import { LIST_PAGE_SIZE } from '../hooks/model'
import { byKey, testKit, textsOf, walk } from './fixtures/kit-ui'
import { widthOf } from './fixtures/measure'
import { STUB_TOKENS as T } from './fixtures/tokens'

const noop = (): void => undefined
const HEAD = { mark: { key: 'sel:docs-012', selected: false, onPress: noop }, badge: 'ACTION', ident: 'docs-012', title: 'README mentions the review queue', age: '30h' }

describe('the list item', () => {
  test('a head row is mark, padded badge, bold id, title, age right-aligned: exactly its width', () => {
    const k = testKit()
    const row = itemHead(k, HEAD, 80)
    expect(widthOf(row)).toBe(80)
    expect(textsOf(row)).toEqual(['□', '◆ ACTION       ', 'docs-012  ', 'README mentions the review queue', ' '.repeat(80 - 2 - 15 - 10 - 32 - 5), '  30h'])
    expect(byKey(row, 'sel:docs-012')?.props).toMatchObject({ plain: true, label: '□' })
    expect(byKey(row, 'sel:docs-012')?.props?.hotkey).toBeUndefined()
  })

  test('no ident: the title is bold; a long title is cut; meta sits before the age; dim draws faint', () => {
    const k = testKit()
    const row = itemHead(k, { mark: null, badge: 'OPEN', title: 'Shipped unified-chat-022 to dev and wrote it all down', meta: 'main', age: '2h' }, 50)
    expect(widthOf(row)).toBe(50)
    const title = walk(row).find(e => e.type === 'Text' && String(e.children?.[0]).startsWith('Shipped'))
    expect(title?.props).toMatchObject({ color: T.fg.bold, bold: true })
    expect(String(title?.children?.[0]).endsWith('…')).toBe(true)
    expect(textsOf(row)).toContain('main   ')
    const dim = itemHead(k, { ...HEAD, dim: true }, 60)
    // The first inked Text is the selection mark (its own ink); every one after it is faint.
    const inked = walk(dim).filter(e => e.type === 'Text' && e.props?.color !== undefined)
    expect(inked.slice(1).every(e => e.props?.color === T.border.strong)).toBe(true)
  })

  test('a selected mark is ■ in the focus ink; an unselectable one says why', async () => {
    const said: string[] = []
    const k = testKit('terminal', said)
    const on = itemHead(k, { ...HEAD, mark: { key: 'sel:a', selected: true, onPress: noop } }, 60)
    expect(walk(on).find(e => e.type === 'Text' && e.children?.[0] === '■')?.props?.color).toBe(T.border.focus)
  })

  test('detail lines sit in the content column; a field is a signature label and its value', () => {
    const k = testKit()
    const rows = item(k, HEAD, [[{ text: 'patch-1-9 · lane light', color: T.fg.subtle }], fieldSegs(k, 'human', 'Proof-read the paragraph')], 70)
    expect(rows).toHaveLength(3)
    expect(walk(rows[1])[0]?.props?.paddingLeft).toBe(CONTENT_COL)
    expect(textsOf(rows[2])).toEqual(['human  ', 'Proof-read the paragraph'])
    expect(walk(rows[2]).find(e => e.children?.[0] === 'human  ')?.props?.color).toBe(T.signatureText)
    for (const r of rows) expect(widthOf(r)).toBeLessThanOrEqual(70)
  })

  test('the focused item is boxed in the focus border, its actions under a blank row; rules only between collapsed items', () => {
    const k = testKit()
    const actions = [actionKey(k, { id: 'done', key: 'd', label: 'mark task done', rung: 'primary', onPress: noop })]
    const card = focusedItem(k, { id: 'uc', head: { ...HEAD, ident: 'unified-chat-022' }, lines: [], actions: actions.map(a => a.el) }, 80)
    expect(walk(card)[0]?.props).toMatchObject({ key: 'card:uc', borderStyle: 'round', borderColor: T.border.focus, paddingX: 1 })
    expect(widthOf(card)).toBeLessThanOrEqual(80)
    const collapsed = { focused: false, nodes: item(k, HEAD, [], 80) }
    const rows = listing(k, [{ focused: true, nodes: [card] }, collapsed, collapsed], 80)
    expect(rows.filter(r => textsOf(r).join('').includes('┄'))).toHaveLength(1)
    expect(textsOf(itemRule(k, 10))).toEqual(['  ┄┄┄┄┄┄┄┄'])
    expect(LIST_PAGE_SIZE).toBe(3)
  })
})

describe('the frame and the rows around a list', () => {
  test('the frame: round border in border.default, ◆ TASKMASTER · NAME in the border, the health at the right, no fill but page', () => {
    const k = testKit()
    const frame = paneFrame(k, { name: 'review', health: { kind: 'success', word: 'tm ok' }, width: 72 }, [])
    expect(walk(frame)[0]?.props).toMatchObject({ borderStyle: 'round', borderColor: T.border.default, width: 72, backgroundColor: T.surface.page })
    expect(textsOf(frame)).toEqual(expect.arrayContaining(['◆ ', 'TASKMASTER · ', 'REVIEW', '● ', 'tm ok']))
    const desktop = paneFrame(testKit('desktop'), { name: 'review', width: 72 }, [])
    expect(walk(desktop).some(e => e.props?.position === 'absolute')).toBe(false)
  })

  test('an overlay is drawn last, absolute at the body top-left', () => {
    const k = testKit()
    const { Text } = k.ui
    const frame = paneFrame(k, { name: 'session', width: 72 }, [<Text>body</Text>], <Text>over</Text>)
    const kids = walk(frame)[0]?.children ?? []
    const last = kids[kids.length - 1] as { props?: Record<string, unknown> }
    expect(last.props).toMatchObject({ position: 'absolute', top: 0, left: 0 })
  })

  test('the toolbar keeps the left piece and drops toggles before u when narrow; pager arrows have no hotkey', () => {
    const said: string[] = []
    const k = testKit('terminal', said)
    const { Text } = k.ui
    const left = { el: <Text>picker</Text>, width: 30 }
    const right = [togglePiece(k, { id: 'show-closed', label: '◐ show closed', on: false, onPress: noop }), pagerPiece(k, { page: 0, pages: 2, onTurn: noop }), refreshPiece(k, { label: 'refresh', onPress: noop })]
    const wide = paneToolbar(k, { left, right, width: 100 })
    for (const key of ['show-closed', 'page-prev', 'page-next', 'refresh']) expect(byKey(wide, key), key).toBeDefined()
    expect(byKey(wide, 'page-prev')?.props?.hotkey).toBeUndefined()
    expect(textsOf(wide)).toContain(' 1 / 2 ')
    const narrow = paneToolbar(k, { left, right, width: 50 })
    expect(byKey(narrow, 'show-closed')).toBeUndefined()
    expect(byKey(narrow, 'refresh')).toBeDefined()
    expect(textsOf(refreshPiece(k, { label: 'rescan', onPress: noop }).el)).toEqual(['u', ' rescan'])
  })

  test('summary: the sentence, then each tally as its badge and count, a zero one faint; cut to the room', () => {
    const k = testKit()
    const row = summaryRow(k, '5 waiting on you · newest 1h ago', [{ word: 'BLOCKED', count: 1 }, { word: 'IN REVIEW', count: 0 }], 100)
    expect(textsOf(row)).toEqual(['5 waiting on you · newest 1h ago', '▲ BLOCKED', ' 1', '● IN REVIEW', ' 0'])
    expect(walk(row).find(e => e.children?.[0] === ' 0')?.props?.color).toBe(T.border.strong)
    expect(widthOf(summaryRow(k, '5 waiting on you · newest 1h ago', [{ word: 'BLOCKED', count: 1 }], 40))).toBeLessThanOrEqual(40)
  })

  test('bulk: keys at the left, scopes at the right as quiet buttons; section, label, empty and offline rows fit', () => {
    const k = testKit()
    const keys = [actionKey(k, { id: 'bulk-done', key: 'b', label: 'mark 0 tasks done', dim: 'Nothing selected', onPress: noop })]
    const bulk = bulkRow(k, { keys, scopes: [{ id: 'sel-all', label: 'all', onPress: noop }, { id: 'sel-none', label: 'none', onPress: noop }], width: 80 })
    expect(byKey(bulk, 'sel-all')?.props).toMatchObject({ label: 'all', plain: true })
    expect(byKey(bulk, 'sel-all')?.props?.hotkey).toBeUndefined()
    expect(textsOf(sectionRow(k, 'HANDED OFF', '1 · an open handover carries the next step', 80))).toEqual(['  ', 'HANDED OFF', '  1 · an open handover carries the next step'])
    const label = labelRow(k, 'TASK', [{ text: 'unified-chat-022', color: T.fg.bold }], 40)
    expect(textsOf(label)[0]).toBe(`  ${'TASK'.padEnd(17)}`)
    expect(widthOf(label)).toBeLessThanOrEqual(40)
    expect(textsOf(emptyRow(k, 'Nothing waiting in Patch 1.8', null, 60))).toEqual(['Nothing waiting in Patch 1.8'])
    const retry = actionKey(k, { id: 'refresh', key: 'u', label: 'retry tm connection', onPress: noop })
    const off = offlineRow(k, 'tm: Connection closed', retry, 80)
    expect(textsOf(off).slice(0, 2)).toEqual(['▲ tm offline', ' · tm: Connection closed'])
    expect(widthOf(off)).toBeLessThanOrEqual(80)
  })
})
```

- [ ] **Step 2: Run the suite to see the new tests fail**

Expected: `tests/vocab-list.test.ts` fails on the missing exports.

- [ ] **Step 3: Implement**

In `mods/taskmaster-mods/hooks/model.ts`, replace:

```ts
export const PAGE_SIZE = 5
```

with:

```ts
export const PAGE_SIZE = 5
/** The rework's lists (spec §15.6): three items a page, review, handovers and restore alike. */
export const LIST_PAGE_SIZE = 3
```

In `mods/taskmaster-mods/hooks/vocab.tsx`, replace:

```tsx
import { HOTKEY, KEYCAP_FACE, type Kit, type Piece, stepUp } from './kit'
import { truncate } from './model'
import type { RrGround } from './rr'
```

with:

```tsx
import { fit, HOTKEY, KEYCAP_FACE, type Kit, PANEL_TITLE, type Piece, stepUp } from './kit'
import { truncate } from './model'
import type { RrGround, RrSignalKind } from './rr'
```

Append to `mods/taskmaster-mods/hooks/vocab.tsx`:

```tsx
// ── The list item (component sheet ITEM, CARD, FIELD, SELECTION; spec §15.6) ─────────────────────────────────────────

export const MARK_W = 2
/** The label column: the selection mark and the padded badge. */
export const LABEL_COL = MARK_W + BADGE_W
/** Where an item's detail lines and a section's content start. */
export const CONTENT_COL = LABEL_COL + 2

export type MarkSpec = { readonly key: string; readonly selected: boolean; readonly onPress: () => void; readonly dim?: string }

/** The selection mark: `□` / `■` as a quiet Button (no hotkey: `s` in the footer selects the focused item); null draws two blanks. */
export function mark(k: Kit, m: MarkSpec | null): Piece {
  const { Box, Text, Button } = k.ui
  if (m === null) return { el: <Text>{'  '}</Text>, width: MARK_W }
  const glyph = m.selected ? '■' : '□'
  const color = m.dim !== undefined ? k.t.border.strong : m.selected ? k.t.border.focus : k.t.fg.disabled
  const why = m.dim
  return {
    el: (
      <Box key={`${m.key}-box`} flexDirection="row" paddingRight={1} flexShrink={0} hover={{ backgroundColor: stepUp(k.t, 'page') }}>
        <Button key={m.key} plain label={glyph} onPress={why === undefined ? m.onPress : () => k.say(why)}>
          <Text color={color}>{glyph}</Text>
        </Button>
      </Box>
    ),
    width: MARK_W,
  }
}

export type HeadSpec = {
  readonly mark: MarkSpec | null
  /** A BADGES word: `ACTION`, `OPEN`, `INTERRUPTED`. */
  readonly badge: string
  /** A bold id before the title (review); absent, the title itself is bold (handovers, restore). */
  readonly ident?: string
  readonly title: string
  /** Subtle words before the age (a branch, a ctx size). */
  readonly meta?: string
  /** `30h`, `16:29`: right-aligned in 5 cells. */
  readonly age: string
  readonly dim?: boolean
  /** Present: the title is a quiet Button keyed `key` (a click or the Tab ring picks the item: handovers' `ho:<id>`). */
  readonly pick?: { readonly key: string; readonly onPress: () => void }
}

/** An item's first row: mark, badge, id, title, then meta and age at the right; exactly `width` cells when it fits. */
export function itemHead(k: Kit, a: HeadSpec, width: number): RenderNode {
  const { Box, Text } = k.ui
  const { t } = k
  const dim = a.dim === true
  const faint = t.border.strong
  const m = mark(k, a.mark)
  const b = glyphBadge(k, a.badge, { pad: true, dim })
  const age = a.age.padStart(5)
  const identRoom = Math.max(4, width - m.width - b.width - age.length - 4)
  const ident = a.ident === undefined || a.ident === '' ? '' : `${truncate(a.ident, identRoom)}  `
  const metaWanted = a.meta === undefined || a.meta === '' ? '' : `${a.meta}   `
  const meta = width - m.width - b.width - ident.length - metaWanted.length - age.length >= 8 ? metaWanted : ''
  const room = Math.max(0, width - m.width - b.width - ident.length - meta.length - age.length - 2)
  const title = truncate(a.title, room)
  const gap = Math.max(0, width - m.width - b.width - ident.length - title.length - meta.length - age.length)
  const { Button } = k.ui
  const titleText = (
    <Text color={dim ? faint : ident === '' ? t.fg.bold : t.fg.default} bold={!dim && ident === ''}>
      {title}
    </Text>
  )
  return (
    <Box flexDirection="row">
      {m.el}
      {b.el}
      {ident === '' ? null : (
        <Text color={dim ? faint : t.fg.bold} bold={!dim}>
          {ident}
        </Text>
      )}
      {a.pick === undefined ? (
        titleText
      ) : (
        <Button key={a.pick.key} plain label={title === '' ? ' ' : title} onPress={a.pick.onPress}>
          {titleText}
        </Button>
      )}
      <Text>{' '.repeat(gap)}</Text>
      {meta === '' ? null : <Text color={dim ? faint : t.fg.subtle}>{meta}</Text>}
      <Text color={dim ? faint : t.fg.subtle}>{age}</Text>
    </Box>
  )
}

/** A detail line in the content column, cut to the room. */
export function itemLine(k: Kit, segs: readonly Seg[], width: number): RenderNode {
  const { Box } = k.ui
  return <Box paddingLeft={CONTENT_COL}>{line(k, segs, Math.max(1, width - CONTENT_COL)).el}</Box>
}

/** A field: the lowercase name in signature ink beside its value (`next  Record the merge`). */
export const fieldSegs = (k: Kit, name: string, value: string): Seg[] => [
  { text: `${name}  `, color: k.t.signatureText },
  { text: value, color: k.t.fg.default },
]

/** A collapsed item: its head and 1–2 detail lines; no keys. */
export function item(k: Kit, head: HeadSpec, lines: readonly (readonly Seg[])[], width: number): RenderNode[] {
  return [itemHead(k, head, width), ...lines.map(l => itemLine(k, l, width))]
}

/** The focused item: boxed in the focus border, its lines, any extra rows, then a blank row and its action rows. */
export function focusedItem(
  k: Kit,
  a: {
    readonly id: string
    readonly head: HeadSpec
    readonly lines: readonly (readonly Seg[])[]
    readonly extra?: readonly RenderNode[]
    readonly actions: readonly RenderNode[]
  },
  width: number,
): RenderNode {
  const { Box, Text } = k.ui
  const inner = Math.max(10, width - 4)
  return (
    <Box key={`card:${a.id}`} flexDirection="column" borderStyle="round" borderColor={k.t.border.focus} paddingX={1}>
      {itemHead(k, a.head, inner)}
      {a.lines.map(l => itemLine(k, l, inner))}
      {[...(a.extra ?? [])]}
      {a.actions.length === 0 ? null : <Text> </Text>}
      {a.actions.length === 0 ? null : (
        <Box flexDirection="column" paddingLeft={1}>
          {[...a.actions]}
        </Box>
      )}
    </Box>
  )
}

/** The dim `┄` rule between two collapsed items, two cells in. */
export function itemRule(k: Kit, width: number): RenderNode {
  const { Text } = k.ui
  return <Text color={k.t.border.default}>{`  ${'┄'.repeat(Math.max(0, width - 2))}`}</Text>
}

export type ListEntry = { readonly focused: boolean; readonly nodes: readonly RenderNode[] }

/** Items in order, a rule between two collapsed neighbours (the focused box needs none). */
export function listing(k: Kit, entries: readonly ListEntry[], width: number): RenderNode[] {
  const out: RenderNode[] = []
  entries.forEach((entry, i) => {
    const prev = entries[i - 1]
    if (prev !== undefined && !prev.focused && !entry.focused) out.push(itemRule(k, width))
    out.push(...entry.nodes)
  })
  return out
}

// ── The frame (component sheet FRAME) ──────────────────────────────────────────────────────────────────────────────

export type Health = { readonly kind: RrSignalKind; readonly word: string }
const HEALTH_GLYPH: Readonly<Record<RrSignalKind, string>> = { success: '●', warning: '▲', critical: '▲', info: '○' }

/**
 * A pane: round border in border.default on the page ground, `◆ TASKMASTER · NAME` set into the top border (desktop: a first
 * row), the health at the top right, the children, and an optional overlay drawn last at the body's top-left (the sidebar).
 */
export function paneFrame(
  k: Kit,
  a: { readonly name: string; readonly health?: Health; readonly width: number },
  children: readonly RenderNode[],
  overlay: RenderNode | null = null,
): RenderNode {
  const { Box, Text } = k.ui
  const { t } = k
  const title = (
    <Text>
      <Text color={t.signatureText}>◆ </Text>
      <Text color={t.fg.subtle}>TASKMASTER · </Text>
      <Text color={t.fg.bold} bold>
        {a.name.toUpperCase()}
      </Text>
    </Text>
  )
  const health =
    a.health === undefined ? null : (
      <Text>
        <Text color={t.tone[a.health.kind]}>{`${HEALTH_GLYPH[a.health.kind]} `}</Text>
        <Text color={t.fg.subtle}>{a.health.word}</Text>
      </Text>
    )
  const frame = {
    flexDirection: 'column' as const,
    borderStyle: 'round',
    borderColor: t.border.default,
    backgroundColor: t.surface.page,
    paddingX: 1,
    width: a.width,
  }
  const over =
    overlay === null ? null : (
      <Box position="absolute" top={0} left={0}>
        {overlay}
      </Box>
    )
  if (PANEL_TITLE[k.surface === 'desktop' ? 'desktop' : 'terminal'] === 'row') {
    return (
      <Box {...frame}>
        <Box flexDirection="row">
          {title}
          <Box flexGrow={1} />
          {health}
        </Box>
        {[...children]}
        {over}
      </Box>
    )
  }
  return (
    <Box {...frame}>
      <Box position="absolute" top={-1} left={1} backgroundColor={t.surface.page} paddingX={1}>
        {title}
      </Box>
      {health === null ? null : (
        <Box position="absolute" top={-1} right={1} backgroundColor={t.surface.page} paddingX={1}>
          {health}
        </Box>
      )}
      {[...children]}
      {over}
    </Box>
  )
}

// ── Toolbar pieces (component sheet TOOLBAR): clickable, no hotkeys — their keys live in the footer ───────────────────

/** Row 1: the left piece (the picker, or Session's words), a spacer, then the right pieces 3 cells apart; narrow, the first right pieces give way. */
export function paneToolbar(k: Kit, a: { readonly left: Piece; readonly right: readonly Piece[]; readonly width: number }): RenderNode {
  const { Box } = k.ui
  const kept = [...a.right]
  const used = (): number => kept.reduce((n, p) => n + p.width, 0) + 3 * Math.max(0, kept.length - 1)
  while (kept.length > 1 && a.left.width + 2 + used() > a.width) kept.shift()
  return (
    <Box flexDirection="row" alignItems="flex-start">
      {a.left.el}
      <Box flexGrow={1} />
      <Box flexDirection="row" columnGap={3}>
        {kept.map(p => p.el)}
      </Box>
    </Box>
  )
}

function quietButton(k: Kit, id: string, text: string, color: string, onPress: () => void, why = ''): RenderNode {
  const { Box, Text, Button } = k.ui
  return (
    <Box key={`${id}-box`} flexDirection="row" hover={{ backgroundColor: stepUp(k.t, 'page') }}>
      <Button key={id} plain label={text} {...(why === '' ? {} : { dimColor: true })} onPress={why === '' ? onPress : () => k.say(why)}>
        <Text color={why === '' ? color : k.t.border.strong}>{text}</Text>
      </Button>
    </Box>
  )
}

/** `‹ 1 / 2 ›`: the arrows are quiet Buttons (`p` / `n` live in the footer); at an end the arrow dims and says why. */
export function pagerPiece(k: Kit, a: { readonly page: number; readonly pages: number; readonly onTurn: (page: number) => void }): Piece {
  const { Box, Text } = k.ui
  const pages = Math.max(1, a.pages)
  const at = Math.min(Math.max(0, a.page), pages - 1)
  const count = ` ${at + 1} / ${pages} `
  return {
    el: (
      <Box flexDirection="row">
        {quietButton(k, 'page-prev', '‹', k.t.fg.subtle, () => a.onTurn(at - 1), at === 0 ? 'Already on the first page' : '')}
        <Text color={k.t.fg.subtle}>{count}</Text>
        {quietButton(k, 'page-next', '›', k.t.fg.subtle, () => a.onTurn(at + 1), at === pages - 1 ? 'Already on the last page' : '')}
      </Box>
    ),
    width: count.length + 2,
  }
}

/** A toolbar toggle (`◐ show closed`): signature ink while on, disabled ink while off. */
export function togglePiece(k: Kit, a: { readonly id: string; readonly label: string; readonly on: boolean; readonly onPress: () => void }): Piece {
  return { el: quietButton(k, a.id, a.label, a.on ? k.t.signatureText : k.t.fg.disabled, a.onPress), width: a.label.length }
}

/** `u refresh` (`u rescan`): a quiet Button keyed `refresh`, the u subtle and the word disabled (`u` itself is a footer key). */
export function refreshPiece(k: Kit, a: { readonly label: string; readonly onPress: () => void }): Piece {
  const { Box, Text, Button } = k.ui
  return {
    el: (
      <Box key="refresh-box" flexDirection="row" hover={{ backgroundColor: stepUp(k.t, 'page') }}>
        <Button key="refresh" plain label={`u ${a.label}`} onPress={a.onPress}>
          <Text color={k.t.fg.subtle}>u</Text>
          <Text color={k.t.fg.disabled}>{` ${a.label}`}</Text>
        </Button>
      </Box>
    ),
    width: 2 + a.label.length,
  }
}

// ── Summary, bulk, section, label, empty and offline rows ───────────────────────────────────────────────────────────

export type Tally = { readonly word: string; readonly count: number }

/** Row 2: the sentence, then each tally as its badge and count (zero faint); tallies give way from the end. */
export function summaryRow(k: Kit, sentence: string, tallies: readonly Tally[], width: number): RenderNode {
  const { Box, Text } = k.ui
  const lead = line(k, [{ text: sentence, color: k.t.fg.subtle }], width)
  const pieces: Piece[] = tallies.map(tally => {
    const badge = glyphBadge(k, tally.word, { dim: tally.count === 0 })
    const count = ` ${tally.count}`
    return {
      el: (
        <Box flexDirection="row">
          {badge.el}
          <Text color={tally.count === 0 ? k.t.border.strong : badgeInk(k, tally.word)}>{count}</Text>
        </Box>
      ),
      width: badge.width + count.length,
    }
  })
  return (
    <Box flexDirection="row" columnGap={3}>
      {fit([lead, ...pieces], width, 3)}
    </Box>
  )
}

export type Scope = { readonly id: string; readonly label: string; readonly onPress: () => void }

/** Row 3: bulk keys at the left (their labels carry the count), scope Buttons (`all · none`) at the right. */
export function bulkRow(k: Kit, a: { readonly keys: readonly Piece[]; readonly scopes: readonly Scope[]; readonly width: number }): RenderNode {
  const { Box, Text } = k.ui
  const scopeWidth = a.scopes.reduce((n, s) => n + s.label.length, 0) + 3 * Math.max(0, a.scopes.length - 1)
  return (
    <Box flexDirection="row" alignItems="flex-start">
      <Box flexDirection="row" columnGap={3}>
        {fit(a.keys, Math.max(1, a.width - scopeWidth - 2), 3)}
      </Box>
      <Box flexGrow={1} />
      <Box flexDirection="row">
        {a.scopes.flatMap((s, i) => [...(i === 0 ? [] : [<Text color={k.t.border.strong}> · </Text>]), quietButton(k, s.id, s.label, k.t.fg.subtle, s.onPress)])}
      </Box>
    </Box>
  )
}

/** A section row in a list: `  HANDED OFF  1 · an open handover carries the next step`. */
export function sectionRow(k: Kit, label: string, note: string, width: number): RenderNode {
  return line(
    k,
    [
      { text: '  ', color: k.t.fg.subtle },
      { text: label, color: k.t.fg.subtle },
      { text: note === '' ? '' : `  ${note}`, color: k.t.fg.disabled },
    ],
    width,
  ).el
}

/** Session's label column: `  TASK` padded to the content column, then the segments. */
export function labelRow(k: Kit, label: string, segs: readonly Seg[], width: number): RenderNode {
  return line(k, [{ text: `  ${label.padEnd(LABEL_COL)}`, color: k.t.fg.subtle }, ...segs], width).el
}

/** Rows (action rows, a y/n) indented to the content column. */
export function underLabel(k: Kit, rows: readonly RenderNode[]): RenderNode {
  const { Box } = k.ui
  return (
    <Box flexDirection="column" paddingLeft={CONTENT_COL}>
      {[...rows]}
    </Box>
  )
}

/** EMPTY: one sentence and, when there is one, the one way out. */
export function emptyRow(k: Kit, sentence: string, way: Piece | null, width: number): RenderNode {
  const { Box } = k.ui
  const text = line(k, [{ text: sentence, color: k.t.fg.subtle }], Math.max(1, width - (way === null ? 0 : way.width + 3)))
  return (
    <Box flexDirection="row" columnGap={3}>
      {text.el}
      {way === null ? null : way.el}
    </Box>
  )
}

/** OFFLINE: `▲ tm offline · <detail>` and the retry key. */
export function offlineRow(k: Kit, detail: string, retry: Piece, width: number): RenderNode {
  const { Box } = k.ui
  const words = line(
    k,
    [
      { text: '▲ tm offline', color: k.t.tone.critical, bold: true },
      { text: detail === '' ? '' : ` · ${detail}`, color: k.t.fg.subtle },
    ],
    Math.max(12, width - retry.width - 3),
  )
  return (
    <Box flexDirection="row" columnGap={3}>
      {words.el}
      {retry.el}
    </Box>
  )
}
```

- [ ] **Step 4: Run the checks**

Expected: `tests/vocab-list.test.ts` passes (+11); everything else unchanged; validate ✔; tsc clean.

- [ ] **Step 5: Commit**

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
git -C $WT add mods/taskmaster-mods/hooks/vocab.tsx mods/taskmaster-mods/hooks/model.ts mods/taskmaster-mods/tests/vocab-list.test.ts
git -C $WT commit -m "feat(taskmaster-mods): rework vocabulary — the 3-line list item, frame, toolbar, summary, bulk and status rows"
```

---

### Task A3: The phase picker and its dropdown

**Files:**
- Modify: `mods/taskmaster-mods/hooks/phases.ts` (append `pickerName`)
- Modify: `mods/taskmaster-mods/hooks/vocab.tsx` (imports; append the picker section)
- Create: `mods/taskmaster-mods/tests/vocab-picker.test.ts`

**Interfaces:**
- Consumes: `StripView`, `StripHandlers` (kit.tsx: `{ pane, view, allCount, focused, digits, empty, on? }` and `{ choose(id), toggleDone(), overlay(open) }`); `orderPhases`, `isDoneShown`, `shownTabs`, `step`, `atEnd`, `ALL`, `PhaseTab` (phases.ts); Task A1's `FooterItem`, `hints`, `SIDEBAR_ESC`, `MENU_PLACEMENT`.
- Produces (exact):
  - `phases.ts`: `pickerName(tab: PhaseTab): string` (`All`, `Patch 1.9 · active`, `Patch 1.8 ✓`, `Release 2.0`, `no task`)
  - `vocab.tsx`: `MENU_W = 44`; `phasePicker(k, s: StripView, on: StripHandlers): Piece` (Buttons `phase-prev`, `phase-field`, `phase-next`, none with a hotkey); `phaseMenu(k, s, on): RenderNode` (rows `phase-pick:<id>` with hotkeys `0`–`9`, the chosen one autoFocus; toggle `phase-done`); `withPhaseMenu(k, s, on, body: readonly RenderNode[]): RenderNode[]`; `phaseFooter(k, s, on): FooterItem[]` (`h/l phase` with keys h, l; `f phases` with key f)

- [ ] **Step 1: Write the failing tests**

`mods/taskmaster-mods/tests/vocab-picker.test.ts`:

```ts
// User intent: pin the phase picker that replaces the strip (spec §15.4) — ‹ [ phase  count ▾ ] › with h/l and f in the
// footer, the dropdown listing every shown phase with its count, done ones dim, 0–9 only while open, ◐ done as its last row.
import { describe, expect, test } from 'claude-code/testing'

import { DEMO_PHASES } from '../hooks/demo'
import type { StripView } from '../hooks/kit'
import { ALL, FRESH_PHASE_VIEW, orderPhases, pickerName } from '../hooks/phases'
import { MENU_PLACEMENT, phaseFooter, phaseMenu, phasePicker, withPhaseMenu } from '../hooks/vocab'
import { byKey, testKit, textsOf, walk } from './fixtures/kit-ui'
import { widthOf } from './fixtures/measure'
import { STUB_TOKENS as T } from './fixtures/tokens'

const view = (choice: string, overlay = false): StripView => ({
  pane: 'review',
  view: { ...FRESH_PHASE_VIEW, choice, phases: DEMO_PHASES, loaded: true, overlay },
  allCount: 5,
  focused: true,
  digits: true,
  empty: 'Nothing waiting',
})

function recorder() {
  const calls: string[] = []
  return {
    calls,
    on: {
      choose: (id: string) => void calls.push(`choose ${id}`),
      toggleDone: () => void calls.push('done'),
      overlay: (open: boolean) => void calls.push(`overlay ${open}`),
    },
  }
}

describe('the picker', () => {
  test('names: All, the active phase, a done one, a planned one', () => {
    expect(orderPhases(DEMO_PHASES, 5).map(pickerName)).toEqual(['All', 'Patch 1.9 · active', 'Release 2.0', 'Patch 1.8 ✓'])
  })

  test('‹ [ Patch 1.9 · active  3 ▾ ] ›: three quiet Buttons, no hotkeys; the field on the overlay ground', () => {
    const { on, calls } = recorder()
    const k = testKit()
    const picker = phasePicker(k, view('patch-1-9'), on)
    for (const key of ['phase-prev', 'phase-field', 'phase-next']) expect(byKey(picker.el, key)?.props?.hotkey, key).toBeUndefined()
    expect(textsOf(picker.el)).toEqual(['‹', ' ', ' Patch 1.9 · active  3 ▾ ', ' ', '›'])
    expect(walk(picker.el).find(e => e.children?.[0] === ' Patch 1.9 · active  3 ▾ ')?.props).toMatchObject({ backgroundColor: T.surface.overlay, color: T.fg.bold })
    expect(picker.width).toBe(widthOf(picker.el))
    expect(calls).toEqual([])
  })

  test('the footer carries h, l and f; h/l step both ways, no wrap; at an end it says why', () => {
    const said: string[] = []
    const { on, calls } = recorder()
    const k = testKit('terminal', said)
    const items = phaseFooter(k, view('patch-1-9'), on)
    expect(items.map(i => `${i.keys} ${i.word}`)).toEqual(['h/l phase', 'f phases'])
    const keys = Object.fromEntries(items.flatMap(i => i.press ?? []).map(p => [p.key, p.onPress]))
    keys.h?.()
    keys.l?.()
    keys.f?.()
    expect(calls).toEqual([`choose ${ALL}`, 'choose release-2', 'overlay true'])
    const atStart = Object.fromEntries(phaseFooter(k, view(ALL), on).flatMap(i => i.press ?? []).map(p => [p.key, p.onPress]))
    atStart.h?.()
    expect(said).toEqual(['Already on the first phase'])
  })
})

describe('the dropdown', () => {
  test('every shown phase with its count, digits 0–9, the chosen row highlighted and focused, done rows dim', () => {
    const { on, calls } = recorder()
    const k = testKit()
    const menu = phaseMenu(k, view('patch-1-9', true), on)
    expect(walk(menu)[0]?.props).toMatchObject({ borderStyle: 'round', borderColor: T.border.strong, backgroundColor: T.surface.overlay, width: 44 })
    expect(byKey(menu, 'phase-pick:')?.props).toMatchObject({ hotkey: '0' })
    expect(byKey(menu, 'phase-pick:patch-1-9')?.props).toMatchObject({ hotkey: '1', autoFocus: true })
    expect(byKey(menu, 'phase-pick:patch-1-9-box')?.props?.backgroundColor).toBe(T.tint24.overlay.signature)
    expect(byKey(menu, 'phase-pick:patch-1-8')?.props).toMatchObject({ hotkey: '3', dimColor: true })
    expect(String(byKey(menu, 'phase-pick:patch-1-9')?.props?.label)).toMatch(/^Patch 1\.9 · active +3$/)
    expect(byKey(menu, 'phase-done')?.props?.hotkey).toBeUndefined()
    expect(textsOf(menu)).toEqual(expect.arrayContaining(['◐ show done phases', 'on']))
    expect(calls).toEqual([])
  })

  test('closed: the body as it is; open: the menu floats over the body (or replaces it, by MENU_PLACEMENT)', () => {
    const { on } = recorder()
    const k = testKit()
    const { Text } = k.ui
    const body = [<Text>row one</Text>, <Text>row two</Text>]
    expect(withPhaseMenu(k, view('patch-1-9'), on, body)).toEqual(body)
    const open = withPhaseMenu(k, view('patch-1-9', true), on, body)
    expect(open).toHaveLength(1)
    const texts = textsOf(open)
    if (MENU_PLACEMENT === 'absolute') {
      expect(texts).toEqual(expect.arrayContaining(['row one', 'row two']))
      expect(walk(open).some(e => e.props?.position === 'absolute' && e.props?.left === 2)).toBe(true)
    } else {
      expect(texts).not.toContain('row one')
    }
  })
})
```

- [ ] **Step 2: Run the suite to see the new tests fail**

Expected: `tests/vocab-picker.test.ts` fails on the missing exports.

- [ ] **Step 3: Implement**

Append to `mods/taskmaster-mods/hooks/phases.ts`:

```ts

/** The picker's and the dropdown's name for a tab (spec §15.4): `All`, `Patch 1.9 · active`, `Patch 1.8 ✓`, `no task`. */
export function pickerName(tab: PhaseTab): string {
  if (tab.kind === 'all') return 'All'
  if (tab.kind === 'active') return `${tab.name} · active`
  if (tab.kind === 'done') return `${tab.name} ✓`
  return tab.name
}
```

In `mods/taskmaster-mods/hooks/vocab.tsx`, replace:

```tsx
import { fit, HOTKEY, KEYCAP_FACE, type Kit, PANEL_TITLE, type Piece, stepUp } from './kit'
import { truncate } from './model'
```

with:

```tsx
import { fit, HOTKEY, KEYCAP_FACE, type Kit, PANEL_TITLE, type Piece, stepUp, type StripHandlers, type StripView } from './kit'
import { truncate } from './model'
import { atEnd, isDoneShown, orderPhases, pickerName, shownTabs, step } from './phases'
```

Append to `mods/taskmaster-mods/hooks/vocab.tsx`:

```tsx
// ── The phase picker and its dropdown (spec §15.4) ─────────────────────────────────────────────────────────────────

export const MENU_W = 44

function tabsOf(s: StripView) {
  const tabs = orderPhases(s.view.phases, s.allCount, s.view.choice)
  const showDone = isDoneShown(tabs, s.view.showDone)
  return { tabs, showDone, shown: shownTabs(tabs, s.view.choice, showDone) }
}

/** `‹ [ Patch 1.9 · active  3 ▾ ] ›`: quiet Buttons; `h` / `l` / `f` are the footer's (phaseFooter). */
export function phasePicker(k: Kit, s: StripView, on: StripHandlers): Piece {
  const { Box, Text, Button } = k.ui
  const { t } = k
  const { tabs, shown } = tabsOf(s)
  const choice = s.view.choice
  const chosen = tabs.find(tab => tab.id === choice) ?? tabs[0]
  const value = ` ${chosen === undefined ? 'All' : pickerName(chosen)}  ${chosen?.count ?? 0} ${s.view.overlay ? '▴' : '▾'} `
  return {
    el: (
      <Box flexDirection="row">
        {quietButton(k, 'phase-prev', '‹', t.fg.subtle, () => on.choose(step(shown, choice, -1)), atEnd(shown, choice, -1) ? 'Already on the first phase' : '')}
        <Text> </Text>
        <Box key="phase-field-box" flexDirection="row" hover={{ backgroundColor: stepUp(t, 'overlay') }}>
          <Button key="phase-field" plain label={value} onPress={() => on.overlay(!s.view.overlay)}>
            <Text backgroundColor={t.surface.overlay} color={t.fg.bold}>
              {value}
            </Text>
          </Button>
        </Box>
        <Text> </Text>
        {quietButton(k, 'phase-next', '›', t.fg.subtle, () => on.choose(step(shown, choice, 1)), atEnd(shown, choice, 1) ? 'Already on the last phase' : '')}
      </Box>
    ),
    width: value.length + 4,
  }
}

/** The footer's phase keys: `h/l phase` (h back, l on; at an end it says why) and `f phases` (opens or closes the list). */
export function phaseFooter(k: Kit, s: StripView, on: StripHandlers): FooterItem[] {
  const { shown } = tabsOf(s)
  const choice = s.view.choice
  const move = (dir: -1 | 1) => (): void => {
    if (atEnd(shown, choice, dir)) k.say(dir === -1 ? 'Already on the first phase' : 'Already on the last phase')
    else on.choose(step(shown, choice, dir))
  }
  return [
    { keys: 'h/l', word: 'phase', press: [{ key: 'h', onPress: move(-1) }, { key: 'l', onPress: move(1) }] },
    { keys: 'f', word: 'phases', press: [{ key: 'f', onPress: () => on.overlay(!s.view.overlay) }] },
  ]
}

/** The open list: each shown phase with its count, `0`–`9` on the first ten, the chosen row tinted and focused, done rows dim. */
export function phaseMenu(k: Kit, s: StripView, on: StripHandlers): RenderNode {
  const { Box, Text, Button } = k.ui
  const { t } = k
  const { shown, showDone } = tabsOf(s)
  const room = MENU_W - 4 - 3
  const rows = shown.map((tab, i) => {
    const digit = i <= 9 ? String(i) : undefined
    const name = pickerName(tab)
    const count = String(tab.count)
    const text = `${truncate(name, room - count.length - 1).padEnd(room - count.length)}${count}`
    const chosen = tab.id === s.view.choice
    const id = `phase-pick:${tab.id}`
    return (
      <Box key={`${id}-box`} flexDirection="row" {...(chosen ? { backgroundColor: t.tint24.overlay.signature } : {})} hover={{ backgroundColor: chosen ? t.tint24.overlay.signature : stepUp(t, 'overlay') }}>
        <Button
          key={id}
          plain
          {...(digit === undefined ? {} : { hotkey: digit })}
          label={digit === undefined ? `   ${text}` : text}
          {...(tab.dim && !chosen ? { dimColor: true } : {})}
          {...(chosen ? { autoFocus: true as const } : {})}
          onPress={() => on.choose(tab.id)}
        >
          {chosen ? (
            <Text color={t.fg.bold} bold>
              {text}
            </Text>
          ) : null}
        </Button>
        {digit === undefined || KEYCAP_FACE !== 'overlay' ? null : (
          <Box position="absolute" left={0} top={0}>
            <Text color={chosen ? t.fg.bold : t.fg.disabled}>{`${digit}  `}</Text>
          </Box>
        )}
      </Box>
    )
  })
  const toggle = (
    <Box flexDirection="row">
      {quietButton(k, 'phase-done', '◐ show done phases', t.signatureText, on.toggleDone)}
      <Box flexGrow={1} />
      <Text color={t.fg.subtle}>{showDone ? 'on' : 'off'}</Text>
    </Box>
  )
  const close = SIDEBAR_ESC === 'refuse' ? 'esc' : 'f'
  return (
    <Box flexDirection="column" borderStyle="round" borderColor={t.border.strong} backgroundColor={t.surface.overlay} paddingX={1} width={MENU_W}>
      {rows}
      <Text> </Text>
      {toggle}
      {hints(k, [{ keys: 'Tab', word: 'move' }, { keys: 'enter', word: 'pick' }, { keys: '0-9', word: 'pick' }, { keys: close, word: 'close' }], MENU_W - 4)}
    </Box>
  )
}

/** A list pane's body under row 1: as it is while the list is closed; with the list floating over it (or replacing it) while open. */
export function withPhaseMenu(k: Kit, s: StripView, on: StripHandlers, body: readonly RenderNode[]): RenderNode[] {
  const { Box } = k.ui
  if (!s.view.overlay) return [...body]
  const menu = phaseMenu(k, s, on)
  if (MENU_PLACEMENT === 'swap') return [menu]
  return [
    <Box flexDirection="column">
      {[...body]}
      <Box position="absolute" top={0} left={2}>
        {menu}
      </Box>
    </Box>,
  ]
}
```

- [ ] **Step 4: Run the checks**

Expected: `tests/vocab-picker.test.ts` passes (+6); everything else unchanged; validate ✔; tsc clean.

- [ ] **Step 5: Commit**

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
git -C $WT add mods/taskmaster-mods/hooks/vocab.tsx mods/taskmaster-mods/hooks/phases.ts mods/taskmaster-mods/tests/vocab-picker.test.ts
git -C $WT commit -m "feat(taskmaster-mods): the phase picker and its dropdown — ‹ [ phase count ▾ ] ›, digits only while open"
```

---

### Task A4: The navigation model (`hooks/nav.ts`) and the sidebar drawing

**Files:**
- Modify: `mods/taskmaster-mods/types/index.d.ts` (add the `TmNav` type only; the PluginState key comes in Task B1)
- Create: `mods/taskmaster-mods/hooks/nav.ts`
- Modify: `mods/taskmaster-mods/hooks/vocab.tsx` (imports; append the sidebar section)
- Create: `mods/taskmaster-mods/tests/nav.test.ts`

**Interfaces:**
- Consumes: `TmPane` (flows.ts: `'review' | 'handovers' | 'session' | 'restore'`); `REVIEW`, `HANDOVERS`, `SESSION`, `RESTORE`, `ageLabel` (model.ts); `reviewKind` (review-list.ts); `TmSnapshot`, `TmRestorePending` (types).
- Produces (exact):
  - `types/index.d.ts`: `export type TmNav = { readonly sidebar: boolean; readonly last: 'review' | 'handovers' | 'session' | 'restore' }`
  - `nav.ts`: `FRESH_NAV: TmNav = { sidebar: false, last: 'session' }`; `RESTORE_BUILT = false` (Task E7 flips it); `PANE_IDS: Readonly<Record<TmPane, string>>`; `PANE_TITLES: Readonly<Record<TmPane, string>>`; `TM_PANE_IDS: readonly string[]`; `paneOf(requestId: string): TmPane | null`; `type SidebarEntry = { pane; key; name; count; note; dim? }`; `type SidebarView = { current: TmPane; entries: readonly SidebarEntry[]; context: string }`; `sidebarView(a: { current: TmPane; snapshot: TmSnapshot | null; restore: TmRestorePending | null; now: number }): SidebarView`; `hhmm(epochSeconds: number): string`; `type EscLayer = 'sidebar' | 'menu' | 'pane'`; `escLayer(nav: TmNav, menuOpen: boolean): EscLayer`
  - `vocab.tsx`: `SIDEBAR_W = 34`; `sidebar(k, v: SidebarView, go: (pane: TmPane) => void): RenderNode` (rows `nav:<pane>` with hotkeys `1`–`4`, the current one tinted and autoFocus); `withSidebar(k, body: RenderNode, v: SidebarView | null, go): RenderNode`

- [ ] **Step 1: Write the failing tests**

`mods/taskmaster-mods/tests/nav.test.ts`:

```ts
// User intent: pin the Taskmaster sidebar (spec §15.8) — four entries with their counts and notes, the current pane marked,
// 1–4 jumping, Restore dim until it is built — and the navigation rules behind it: which pane an id is, which layer Esc closes.
import { describe, expect, test } from 'claude-code/testing'

import { demoSnapshot } from '../hooks/demo'
import { escLayer, FRESH_NAV, hhmm, paneOf, RESTORE_BUILT, sidebarView, TM_PANE_IDS } from '../hooks/nav'
import { SIDEBAR_W, sidebar, withSidebar } from '../hooks/vocab'
import { byKey, testKit, textsOf, walk } from './fixtures/kit-ui'
import { widthOf } from './fixtures/measure'
import { STUB_TOKENS as T } from './fixtures/tokens'

const NOW = Date.UTC(2026, 9, 12, 12, 0)

describe('the navigation model', () => {
  test('pane ids and their panes; a fresh nav has the sidebar shut and Session as the last pane', () => {
    expect(TM_PANE_IDS).toEqual(['tm-review', 'tm-handovers', 'tm-session', 'tm-restore'])
    expect([paneOf('tm-review'), paneOf('tm-session'), paneOf('tm-kit')]).toEqual(['review', 'session', null])
    expect(FRESH_NAV).toEqual({ sidebar: false, last: 'session' })
  })

  test('Esc closes the sidebar first, then the phase list, then the pane', () => {
    expect(escLayer({ sidebar: true, last: 'review' }, true)).toBe('sidebar')
    expect(escLayer({ sidebar: false, last: 'review' }, true)).toBe('menu')
    expect(escLayer({ sidebar: false, last: 'review' }, false)).toBe('pane')
  })

  test('entries: Review counts what waits with its blocked/action note, Handovers the open total and newest age', () => {
    const v = sidebarView({ current: 'session', snapshot: demoSnapshot(NOW), restore: null, now: NOW })
    expect(v.entries.map(e => [e.key, e.name, e.count, e.note])).toEqual([
      ['1', 'Review', '5', '▲1 ◆4'],
      ['2', 'Handovers', '6', 'newest 2h'],
      ['3', 'Session', '', ''],
      ['4', 'Restore', '', ''],
    ])
    expect(v.context).toBe('unified-chat-022 · in review')
    expect(v.entries[3]?.dim).toBe(RESTORE_BUILT ? undefined : 'Restore is not built yet')
  })

  test('offline counts read –; no snapshot reads as no task bound; a restore burst gives its count and time', () => {
    const offline = sidebarView({ current: 'review', snapshot: { ...demoSnapshot(NOW), reachable: false }, restore: null, now: NOW })
    expect(offline.entries.slice(0, 2).map(e => e.count)).toEqual(['–', '–'])
    expect(offline.context).toBe('tm offline')
    expect(sidebarView({ current: 'review', snapshot: null, restore: null, now: NOW }).context).toBe('no task bound')
    const epoch = new Date(2026, 9, 10, 16, 14).getTime() / 1000
    expect(hhmm(epoch)).toBe('16:14')
    if (RESTORE_BUILT) {
      const r = sidebarView({ current: 'review', snapshot: null, restore: { epoch, unfinished: 3 }, now: NOW }).entries[3]
      expect([r?.count, r?.note]).toEqual(['3', 'burst 16:14'])
    }
  })
})

describe('the sidebar drawing', () => {
  test('34 cells on the overlay ground; entries 1–4 as Buttons nav:<pane>; the current one tinted, bold and focused', () => {
    const k = testKit()
    const v = sidebarView({ current: 'session', snapshot: demoSnapshot(NOW), restore: null, now: NOW })
    const tree = sidebar(k, v, () => undefined)
    expect(walk(tree)[0]?.props).toMatchObject({ width: SIDEBAR_W, backgroundColor: T.surface.overlay, borderStyle: 'round', borderColor: T.border.strong })
    for (const [pane, key] of [['review', '1'], ['handovers', '2'], ['session', '3'], ['restore', '4']] as const) {
      expect(byKey(tree, `nav:${pane}`)?.props?.hotkey, pane).toBe(key)
    }
    expect(byKey(tree, 'nav:session')?.props?.autoFocus).toBe(true)
    expect(byKey(tree, 'nav:session-box')?.props?.backgroundColor).toBe(T.tint24.overlay.signature)
    expect(String(byKey(tree, 'nav:review')?.props?.label)).toMatch(/^Review +▲1 ◆4 +5$/)
    expect(textsOf(tree)).toEqual(expect.arrayContaining(['◆ ', 'TASKMASTER', 'unified-chat-022 · in review']))
    for (const row of walk(tree).filter(e => e.type === 'Box' && String(e.props?.key ?? '').startsWith('nav:'))) expect(widthOf(row)).toBeLessThanOrEqual(SIDEBAR_W - 4)
  })

  test('a press goes to that pane; a dim entry says why; withSidebar floats it at the top-left, absent when closed', () => {
    const said: string[] = []
    const k = testKit('terminal', said)
    const went: string[] = []
    const v = sidebarView({ current: 'session', snapshot: demoSnapshot(NOW), restore: null, now: NOW })
    const tree = sidebar(k, v, pane => void went.push(pane))
    const press = (key: string) => (byKey(tree, key) as { press?: unknown } | undefined)?.press
    expect(press('nav:review')).toBeDefined()
    const { Text } = k.ui
    const body = <Text>body</Text>
    expect(withSidebar(k, body, null, () => undefined)).toBe(body)
    const open = withSidebar(k, body, v, () => undefined)
    expect(walk(open).some(e => e.props?.position === 'absolute' && e.props?.top === 0 && e.props?.left === 0)).toBe(true)
  })
})
```

- [ ] **Step 2: Run the suite to see the new tests fail**

Expected: `tests/nav.test.ts` fails on the missing modules.

- [ ] **Step 3: Implement**

In `mods/taskmaster-mods/types/index.d.ts`, replace:

```ts
/** The newest closing burst the restore scan found with unfinished sessions (Stage 5 writes it): the band's `4 N restore`. */
```

with:

```ts
/** The Taskmaster sidebar (spec §15.8): whether it is open over the pane, and the pane `m` on the band reopens. */
export type TmNav = { readonly sidebar: boolean; readonly last: 'review' | 'handovers' | 'session' | 'restore' }
/** The newest closing burst the restore scan found with unfinished sessions (Stage 5 writes it): the sidebar's Restore entry. */
```

`mods/taskmaster-mods/hooks/nav.ts`:

```ts
// User intent: the Taskmaster sidebar's navigation model (spec §15.8) — which pane an id is, what each sidebar entry counts,
// which layer Esc closes first — as plain functions, so the band, every pane and the restore module agree without drawing.
import type { TmNav, TmRestorePending, TmSnapshot } from '../types'
import type { TmPane } from './flows'
import { ageLabel, HANDOVERS, RESTORE, REVIEW, SESSION } from './model'
import { reviewKind } from './review-list'

export const FRESH_NAV: TmNav = { sidebar: false, last: 'session' }
/** False until the restore pane ships (Task E7 flips it): its sidebar entry draws dim and says so. */
export const RESTORE_BUILT = false
export const PANE_IDS: Readonly<Record<TmPane, string>> = { review: REVIEW, handovers: HANDOVERS, session: SESSION, restore: RESTORE }
export const PANE_TITLES: Readonly<Record<TmPane, string>> = { review: 'Review', handovers: 'Handovers', session: 'Session', restore: 'Restore' }
const PANES: readonly TmPane[] = ['review', 'handovers', 'session', 'restore']
export const TM_PANE_IDS: readonly string[] = PANES.map(pane => PANE_IDS[pane])

/** The Taskmaster pane a requestId is, or null (the kit gallery, another plugin's). */
export function paneOf(requestId: string): TmPane | null {
  return PANES.find(pane => PANE_IDS[pane] === requestId) ?? null
}

/** One sidebar row: its digit, name, an optional note and a count (both may be ''), and why it is dim (absent: live). */
export type SidebarEntry = {
  readonly pane: TmPane
  readonly key: string
  readonly name: string
  readonly count: string
  readonly note: string
  readonly dim?: string
}
export type SidebarView = { readonly current: TmPane; readonly entries: readonly SidebarEntry[]; readonly context: string }

/** `16:14`: an epoch in seconds as local hours and minutes. */
export function hhmm(epochSeconds: number): string {
  const d = new Date(epochSeconds * 1000)
  return `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`
}

/** What the sidebar lists (spec §15.8 S3): Review's waiting total and `▲ blocked ◆ action`, Handovers' open total and newest age, Restore's burst. */
export function sidebarView(a: {
  readonly current: TmPane
  readonly snapshot: TmSnapshot | null
  readonly restore: TmRestorePending | null
  readonly now: number
}): SidebarView {
  const s = a.snapshot
  const live = s !== null && s.reachable
  const kinds = { blocked: 0, action: 0 }
  for (const item of live ? s.queue : []) {
    const kind = reviewKind(item)
    if (kind === 'blocked' || kind === 'action') kinds[kind] += 1
  }
  const newest = live ? ageLabel(s.handovers[0]?.created ?? '', a.now) : ''
  const restore: SidebarEntry = RESTORE_BUILT
    ? { pane: 'restore', key: '4', name: 'Restore', count: a.restore === null ? '' : String(a.restore.unfinished), note: a.restore === null ? '' : `burst ${hhmm(a.restore.epoch)}` }
    : { pane: 'restore', key: '4', name: 'Restore', count: '', note: '', dim: 'Restore is not built yet' }
  const bound = s?.bound ?? null
  const context = s === null ? 'no task bound' : !s.reachable ? 'tm offline' : bound === null ? 'no task bound' : [bound.taskId, (bound.detail?.status ?? '').replace(/-/g, ' ')].filter(Boolean).join(' · ')
  return {
    current: a.current,
    context,
    entries: [
      {
        pane: 'review',
        key: '1',
        name: 'Review',
        count: live ? `${s.queueTotal}${s.queueCapped === true ? '+' : ''}` : '–',
        note: [kinds.blocked > 0 ? `▲${kinds.blocked}` : '', kinds.action > 0 ? `◆${kinds.action}` : ''].filter(Boolean).join(' '),
      },
      { pane: 'handovers', key: '2', name: 'Handovers', count: live ? String(s.handoversTotal) : '–', note: newest === '' ? '' : `newest ${newest}` },
      { pane: 'session', key: '3', name: 'Session', count: '', note: '' },
      restore,
    ],
  }
}

export type EscLayer = 'sidebar' | 'menu' | 'pane'

/** What the person's Esc closes (spec §15.8 S4): the sidebar, else the open phase list, else the pane itself. */
export function escLayer(nav: TmNav, menuOpen: boolean): EscLayer {
  if (nav.sidebar) return 'sidebar'
  return menuOpen ? 'menu' : 'pane'
}
```

In `mods/taskmaster-mods/hooks/vocab.tsx`, replace:

```tsx
import { atEnd, isDoneShown, orderPhases, pickerName, shownTabs, step } from './phases'
```

with:

```tsx
import type { TmPane } from './flows'
import type { SidebarView } from './nav'
import { atEnd, isDoneShown, orderPhases, pickerName, shownTabs, step } from './phases'
```

Append to `mods/taskmaster-mods/hooks/vocab.tsx`:

```tsx
// ── The Taskmaster sidebar (spec §15.8): an overlay at the pane body's top-left ────────────────────────────────────────

export const SIDEBAR_W = 34

/** The sidebar: one row per pane (`1`–`4`), the current one tinted and focused, the bound task, then its hints. */
export function sidebar(k: Kit, v: SidebarView, go: (pane: TmPane) => void): RenderNode {
  const { Box, Text, Button } = k.ui
  const { t } = k
  const inner = SIDEBAR_W - 4
  const room = inner - 3
  const rows = v.entries.map(entry => {
    const current = entry.pane === v.current
    const right = [entry.note, entry.count.padStart(2)].filter(part => part.trim() !== '').join('  ')
    const text = `${truncate(entry.name, room - right.length - 1).padEnd(room - right.length)}${right}`
    const id = `nav:${entry.pane}`
    const why = entry.dim
    return (
      <Box key={`${id}-box`} flexDirection="row" {...(current ? { backgroundColor: t.tint24.overlay.signature } : {})} hover={{ backgroundColor: current ? t.tint24.overlay.signature : stepUp(t, 'overlay') }}>
        <Button
          key={id}
          plain
          hotkey={entry.key}
          label={text}
          {...(why !== undefined ? { dimColor: true } : {})}
          {...(current ? { autoFocus: true as const } : {})}
          onPress={why === undefined ? () => go(entry.pane) : () => k.say(why)}
        >
          {current ? (
            <Text color={t.fg.bold} bold>
              {text}
            </Text>
          ) : null}
        </Button>
        {KEYCAP_FACE !== 'overlay' ? null : (
          <Box position="absolute" left={0} top={0}>
            <Text color={current ? t.fg.bold : t.fg.disabled}>{`${entry.key}  `}</Text>
          </Box>
        )}
      </Box>
    )
  })
  const close = SIDEBAR_ESC === 'refuse' ? 'esc' : 'm'
  const title = (
    <Text>
      <Text color={t.signatureText}>◆ </Text>
      <Text color={t.fg.bold} bold>
        TASKMASTER
      </Text>
    </Text>
  )
  const desktop = PANEL_TITLE[k.surface === 'desktop' ? 'desktop' : 'terminal'] === 'row'
  return (
    <Box flexDirection="column" width={SIDEBAR_W} borderStyle="round" borderColor={t.border.strong} backgroundColor={t.surface.overlay} paddingX={1}>
      {desktop ? (
        title
      ) : (
        <Box position="absolute" top={-1} left={1} backgroundColor={t.surface.overlay} paddingX={1}>
          {title}
        </Box>
      )}
      <Text> </Text>
      {rows}
      <Text> </Text>
      {line(k, [{ text: v.context, color: t.fg.disabled }], inner).el}
      <Text> </Text>
      {hints(k, [{ keys: 'Tab', word: 'move' }, { keys: 'enter', word: 'open' }], inner)}
      {hints(k, [{ keys: '1-4', word: 'jump' }, { keys: close, word: 'close' }], inner)}
    </Box>
  )
}

/** A pane's body with the sidebar floating over its top-left while open; the body alone while it is shut. */
export function withSidebar(k: Kit, body: RenderNode, v: SidebarView | null, go: (pane: TmPane) => void): RenderNode {
  const { Box } = k.ui
  if (v === null) return body
  return (
    <Box flexDirection="column" flexGrow={1}>
      {body}
      <Box position="absolute" top={0} left={0}>
        {sidebar(k, v, go)}
      </Box>
    </Box>
  )
}
```

`vocab.tsx` imports `TmPane` from `flows.ts` as a type only, and `flows.ts` never imports `vocab.tsx`, so there is no runtime cycle. `nav.ts` imports `review-list.ts`, which imports only `model.ts`.

- [ ] **Step 4: Run the checks**

Expected: `tests/nav.test.ts` passes (+6); everything else unchanged; validate ✔; tsc clean.

- [ ] **Step 5: Commit**

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
git -C $WT add mods/taskmaster-mods/hooks/nav.ts mods/taskmaster-mods/hooks/vocab.tsx mods/taskmaster-mods/types/index.d.ts mods/taskmaster-mods/tests/nav.test.ts
git -C $WT commit -m "feat(taskmaster-mods): the navigation model and the Taskmaster sidebar drawing"
```

---

### Task A5: `/tm-kit` becomes the component sheet

**Files:**
- Modify: `mods/taskmaster-mods/hooks/kit-gallery.tsx` (whole file)
- Modify: `mods/taskmaster-mods/tests/kit-gallery.test.ts` (whole file)

**Interfaces:**
- Consumes: everything Tasks A1–A4 produce; `demoSnapshot` (demo.ts); `StripView`, `StripHandlers`.
- Produces: `KIT = 'tm-kit'` (unchanged); `kitGalleryTree(k: Kit, strip: StripView, on: StripHandlers, say: (id: string) => void, width: number): RenderNode`, the component sheet in the prototype's order, each element once with its rule beside it.

- [ ] **Step 1: Write the failing tests**

Replace the whole of `mods/taskmaster-mods/tests/kit-gallery.test.ts` with:

```ts
// User intent: /tm-kit shows the rework's component sheet in demo mode so the vocabulary is judged by eye — it opens, draws
// every element once on the terminal and the desktop, its keys press, a disabled key says why, its picker really moves.
import { describe, expect, mock, test } from 'claude-code/testing'

import { command, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const KIT_PANE = { ...pane('tm-kit'), props: { ...pane('tm-kit').props, bodyColumns: 120 } }

describe('/tm-kit, the component sheet', () => {
  test('demo: /tm-kit opens the sheet; every element draws on the terminal and the desktop', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect(await $.command.run(command('tm-kit'))).toEqual({ text: 'Kit gallery opened.' })
    expect(world.opened).toEqual(['tm-kit'])
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...KIT_PANE, surface })
      for (const text of ['FRAME', 'BAND', 'SIDEBAR', 'TOOLBAR', 'SUMMARY', 'BULK', 'SECTION', 'BADGE', 'ITEM', 'CARD', 'FIELD', 'SELECTION', 'KEY 1', 'KEY 2', 'KEY 3', 'KEY 4', 'KEY 5', 'FOOTER', 'EMPTY', 'OFFLINE']) {
        expect(await ui.find({ type: 'Text', text: text.padEnd(12) }), `${surface} ${text}`).toBeDefined()
      }
      for (const key of ['g-primary', 'g-secondary', 'g-disabled', 'g-destructive', 'phase-field', 'nav:review', 'sel:docs-012', 'page-next']) {
        expect(await ui.find({ type: 'Button', key }), `${surface} ${key}`).toBeDefined()
      }
      await ui.unmount()
    }
  })

  test('keys toast that they were pressed; a disabled key says why', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...KIT_PANE })
    await ui.press({ key: 'g-disabled' })
    await ui.press({ key: 'g-primary' })
    await ui.press({ key: 'page-prev' })
    expect(world.toasts).toEqual(['Nothing selected to close', 'pressed g-primary', 'Already on the first page'])
  })

  test('the picker on the sheet moves the review phase, in demo only (no store, no tm)', DEMO, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...KIT_PANE })
    expect((await ui.find({ type: 'Button', key: 'phase-field' }))?.props?.label).toBe(' All  5 ▾ ')
    await ui.press({ key: 'phase-next' })
    await clock.settle()
    await ui.redraw()
    expect((await ui.find({ type: 'Button', key: 'phase-field' }))?.props?.label).toBe(' Patch 1.9 · active  3 ▾ ')
    expect(world.calls).toEqual([])
  })

  test('tm mode: /tm-kit only says the gallery is a demo surface', { plugins: [RR_STUB] }, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect((await $.command.run(command('tm-kit'))).text).toMatch(/demo mode/)
    expect(world.opened).toEqual([])
  })
})
```

- [ ] **Step 2: Run the suite to see the new tests fail**

Expected: the first three `/tm-kit` tests fail (the old gallery draws neither the sheet's names nor `g-primary`).

- [ ] **Step 3: Implement**

Replace the whole of `mods/taskmaster-mods/hooks/kit-gallery.tsx` with:

```tsx
// User intent: /tm-kit — the visual rework's component sheet (spec §15.2, prototype Main.dc.html) drawn with the real
// vocabulary and sample data (demo source only): each element once, its rule beside it, so the parts are judged by eye.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode } from 'claude-code'

import { demoSnapshot } from './demo'
import { badge, type Kit, type Piece, type StripHandlers, type StripView, TM_TAG } from './kit'
import { sidebarView } from './nav'
import {
  actionKey,
  bulkRow,
  emptyRow,
  fieldSegs,
  focusedItem,
  footer,
  glyphBadge,
  hints,
  item,
  itemRule,
  line,
  offlineRow,
  pagerPiece,
  paneToolbar,
  phasePicker,
  refreshPiece,
  sectionRow,
  sidebar,
  summaryRow,
  togglePiece,
} from './vocab'

export const KIT = 'tm-kit'
const NAME_W = 12

/** One sheet entry: the name, the element (one or more rows), and its rule in subtle ink. */
function entry(k: Kit, name: string, rows: readonly RenderNode[], rule: string, width: number): RenderNode {
  const { Box, Text } = k.ui
  return (
    <Box flexDirection="row" columnGap={2} alignItems="flex-start">
      <Text color={k.t.fg.bold} bold>
        {name.padEnd(NAME_W)}
      </Text>
      <Box flexDirection="column" width={Math.min(70, Math.max(30, width - NAME_W - 34))}>
        {[...rows]}
      </Box>
      <Text color={k.t.fg.subtle} wrap="truncate-end">
        {rule}
      </Text>
    </Box>
  )
}

/** The component sheet. `say(id)` answers every sample key; the picker moves the review phase for real (demo state only). */
export function kitGalleryTree(k: Kit, strip: StripView, on: StripHandlers, say: (id: string) => void, width: number): RenderNode {
  const { Box, Text } = k.ui
  const { t } = k
  const w = Math.min(70, Math.max(30, width - NAME_W - 34))
  const press = (id: string) => () => say(id)
  const key = (id: string, letter: string, label: string, extra: { rung?: 'primary' | 'secondary' | 'destructive'; dim?: string } = {}): Piece =>
    actionKey(k, { id, key: letter, label, onPress: press(id), ...extra })
  const head = { mark: { key: 'sel:docs-012', selected: false, onPress: press('sel:docs-012') }, badge: 'ACTION', ident: 'docs-012', title: 'README mentions the review queue', age: '30h' }
  const chip = badge(k, TM_TAG, 'signature', { on: 'raised' })
  const nav = sidebarView({ current: 'session', snapshot: demoSnapshot(Date.UTC(2026, 9, 12, 12)), restore: null, now: Date.UTC(2026, 9, 12, 12) })
  const rows: RenderNode[] = [
    entry(k, 'FRAME', [line(k, [{ text: '╭─ ', color: t.border.default }, { text: '◆ ', color: t.signatureText }, { text: 'TASKMASTER · ', color: t.fg.subtle }, { text: 'REVIEW', color: t.fg.bold, bold: true }, { text: ' ─── ● tm ok ─╮', color: t.border.default }], w).el], 'no tab row; pane named in title', width),
    entry(
      k,
      'BAND',
      [
        <Box flexDirection="row" columnGap={2} backgroundColor={t.surface.raised}>
          {chip.el}
          {glyphBadge(k, 'IN REVIEW').el}
          <Text color={t.fg.bold} bold>
            Unified chat…
          </Text>
          {key('g-band-d', 'd', 'mark task done').el}
        </Box>,
      ],
      'subject left; keys in a right column',
      width,
    ),
    entry(k, 'SIDEBAR', [sidebar(k, nav, pane => say(`nav:${pane}`))], 'm opens; key · pane · note · count', width),
    entry(k, 'TOOLBAR', [paneToolbar(k, { left: phasePicker(k, strip, on), right: [togglePiece(k, { id: 'g-toggle', label: '◐ show closed', on: false, onPress: press('g-toggle') }), pagerPiece(k, { page: 0, pages: 2, onTurn: () => say('page') }), refreshPiece(k, { label: 'refresh', onPress: press('refresh') })], width: w })], 'row 1: phase left; toggle, pager, u', width),
    entry(k, 'SUMMARY', [summaryRow(k, '5 waiting on you · newest 1h ago', [{ word: 'BLOCKED', count: 1 }], w)], 'row 2: sentence, then tallies', width),
    entry(k, 'BULK', [bulkRow(k, { keys: [key('g-bulk-off', 'b', 'mark 0 tasks done', { dim: 'Nothing selected' }), key('g-bulk', 'g', 'send 2 tasks to agent')], scopes: [{ id: 'g-all', label: 'all', onPress: press('g-all') }, { id: 'g-none', label: 'none', onPress: press('g-none') }], width: w })], 'row 3: count in the label; dim at 0', width),
    entry(k, 'SECTION', [sectionRow(k, 'HANDED OFF', '1 · open handover', w)], 'label column, dim note', width),
    entry(k, 'BADGE', [<Box flexDirection="row" columnGap={2}>{['BLOCKED', 'ACTION', 'IN REVIEW'].map(word => glyphBadge(k, word).el)}</Box>], 'symbol + word, semantic fg', width),
    entry(k, 'ITEM', [...item(k, head, [[{ text: 'patch-1-9 · lane light', color: t.fg.subtle }], fieldSegs(k, 'human', 'Proof-read the paragraph')], w), itemRule(k, w)], 'title / context / detail; ┄; no keys', width),
    entry(k, 'CARD', [focusedItem(k, { id: 'g-card', head: { ...head, mark: null, badge: 'BLOCKED', ident: 'uc-022', title: 'Unified chat pre-build', age: '1h' }, lines: [[{ text: 'patch-1-9 · lane full', color: t.fg.subtle }]], actions: [<Box flexDirection="row" columnGap={3}>{key('g-card-d', 'w', 'mark task done', { rung: 'primary' }).el}{key('g-card-a', 'a', 'send to agent').el}</Box>] }, w)], 'focused only: box + action row', width),
    entry(k, 'FIELD', [line(k, fieldSegs(k, 'next', 'Record the merge'), w).el], 'lowercase label beside its value', width),
    entry(k, 'SELECTION', [line(k, [{ text: '■ ', color: t.border.focus }, { text: 'picked   ', color: t.fg.default }, { text: '□ ', color: t.fg.disabled }, { text: 'not', color: t.fg.subtle }], w).el], 'mark column only', width),
    entry(k, 'KEY 1', [key('g-primary', 'e', 'mark task done', { rung: 'primary' }).el], 'primary: one fill per context', width),
    entry(k, 'KEY 2', [key('g-secondary', 'c', 'copy handover').el], 'secondary: lit letter, plain label', width),
    entry(k, 'KEY 3', [hints(k, [{ keys: 'j/k', word: 'move' }, { keys: 'p/n', word: 'page' }], w)], 'nav/meta: footer only, never capped', width),
    entry(k, 'KEY 4', [key('g-disabled', 'q', 'close 0 handovers', { dim: 'Nothing selected to close' }).el], 'disabled: whole cap dim', width),
    entry(k, 'KEY 5', [key('g-destructive', 'x', 'close handover', { rung: 'destructive' }).el], 'destructive: warning letter, y/n, last', width),
    entry(k, 'LABELS', [line(k, [{ text: 'c', color: t.signatureText, bold: true }, { text: ' copy handover', color: t.fg.default }, { text: '  not  ', color: t.fg.disabled }, { text: 'c copy', color: t.border.strong }], w).el], 'verb + object, beside its subject', width),
    entry(k, 'FOOTER', [footer(k, [{ keys: 'j/k', word: 'move', press: [{ key: 'j', onPress: press('key:j') }, { key: 'k', onPress: press('key:k') }] }, { keys: 's', word: 'select', press: [{ key: 's', onPress: press('key:s') }] }, { keys: 'm', word: 'sidebar' }], w)], 'last row; same order everywhere', width),
    entry(k, 'EMPTY', [emptyRow(k, 'Nothing waiting in Patch 1.8 · done 10-01', key('g-empty', '0', 'show all phases'), w)], 'sentence + one way out', width),
    entry(k, 'OFFLINE', [offlineRow(k, 'last ok 2m ago', key('g-retry', 'r', 'retry tm connection'), w)], 'critical word + retry', width),
  ]
  return (
    <Box flexDirection="column" rowGap={1} paddingX={1}>
      {rows}
    </Box>
  )
}
```

Every sample key on the sheet has its own hotkey (`d`, `b`, `g`, `w`, `a`, `e`, `c`, `q`, `x`, `0`, `r`, the footer's `j` `k` `s`, the sidebar's `1`–`4`), so no hotkey is drawn twice. `register.tsx` needs no edit: `kitGalleryTree`'s signature is unchanged, and its `KIT` render already passes the review strip with `allCount: 5` and `id => say(`pressed ${id}`)`.

- [ ] **Step 4: Run the checks**

Expected: the four `/tm-kit` tests pass (old gallery tests replaced: net 0); validate ✔; tsc clean.

- [ ] **Step 5: Commit**

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
git -C $WT add mods/taskmaster-mods/hooks/kit-gallery.tsx mods/taskmaster-mods/tests/kit-gallery.test.ts
git -C $WT commit -m "feat(taskmaster-mods): /tm-kit shows the rework's component sheet"
```

---

### Task A6: Live check with the user (Stage A)

Orchestrator task; no subagent.

- [ ] **Step 1: Smoke**

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
env -u ANTHROPIC_API_KEY claude -p --model haiku --plugin-dir $WT/mods/rr-tui --plugin-dir $WT/mods/taskmaster-mods "Reply with the single word ok."
```

Expected: `ok`.

- [ ] **Step 2: Ask the user**

Ask the user to set taskmaster-mods' Data source to `demo` (`/config`), run `/tm-kit` in a terminal at least 120 columns wide, and compare it with `Main.dc.html`'s component sheet: key faces (one fill; letters lit; disabled dim; destructive warning), badges, the item and card, the footer reading as plain text, the picker field, the sidebar.

- [ ] **Step 3: Record**

Ledger line: `Task A6: Stage A live check — <user's words>; fixes: <none | list>`. Fix anything the user rejects in a follow-up task before Stage B (same files, tests first). Stop until the user signs off.

---

## Stage B: The band (direction C)

**Goal.** The band reads as the prototype's direction C: subject text on the left, keys in one right column, no counts, `m open sidebar`; the states no handover, nothing bound, offline, connecting, confirm and refused. Until Stage C draws the sidebar, `m` opens the last Taskmaster pane (Session at first) with `nav.sidebar` already set.

### Task B1: Navigation state, `openSidebar` and `retry`

**Files:**
- Modify: `mods/taskmaster-mods/types/index.d.ts` (PluginState `nav`)
- Modify: `mods/taskmaster-mods/hooks/flows.ts` (`TmWriter.nav`; the private `open` records the last pane; `openPane` hoisted; `openSidebar`, `retry`)
- Modify: `mods/taskmaster-mods/hooks/register.tsx` (`NAV` atom; `writerOf().nav`)
- Create: `mods/taskmaster-mods/tests/nav-flows.test.ts`

**Interfaces:**
- Consumes: `TmNav`, `FRESH_NAV`, `paneOf` (Task A4).
- Produces: PluginState `nav: TmNav`; `TmWriter.nav: (change: (n: TmNav) => TmNav) => Promise<void>`; `TmFlows.openSidebar(): Promise<void>` (sets `nav.sidebar`, opens `nav.last`); `TmFlows.retry(): Promise<void>` (asks tm for a fresh snapshot; demo toasts it has nothing newer); every opening of a Taskmaster pane records `nav.last`.

- [ ] **Step 1: Write the failing tests**

`mods/taskmaster-mods/tests/nav-flows.test.ts`:

```ts
// User intent: the navigation flows behind the band's `m` and `r` (spec §15.5, §15.8) — opening any Taskmaster pane remembers
// it, `m` reopens the last one with the sidebar open, `r` asks tm again — driven directly over a recording host.
import { describe, expect, test } from 'claude-code/testing'

import type { TmNav } from '../types'
import { createFlows, type TmWriter } from '../hooks/flows'
import type { TmHost } from '../hooks/host'
import { FRESH_NAV } from '../hooks/nav'

function rig(a: { refresh?: () => void } = {}) {
  const seen = { opened: [] as string[], closed: [] as string[], toasts: [] as string[], nav: FRESH_NAV as TmNav }
  const host = new Proxy(
    {
      openPane: async (id: string) => (seen.opened.push(id), true),
      closePane: async (id: string) => void seen.closed.push(id),
      toast: (text: string) => void seen.toasts.push(text),
      after: () => undefined,
    },
    { get: (target, key) => (key in target ? target[key as keyof typeof target] : async () => undefined) },
  ) as unknown as TmHost
  const write = new Proxy(
    { nav: async (change: (n: TmNav) => TmNav) => void (seen.nav = change(seen.nav)) },
    { get: (target, key) => (key in target ? target[key as keyof typeof target] : async () => undefined) },
  ) as unknown as TmWriter
  const flows = createFlows({
    host,
    write,
    actions: { done: async () => ({ ok: true }), backToAgent: async () => ({ ok: true }) },
    afterWrite: () => undefined,
    boundId: async () => null,
    ...(a.refresh === undefined ? { source: 'demo' as const } : { refresh: a.refresh }),
  })
  return { flows, seen }
}

describe('navigation flows', () => {
  test('opening a pane remembers it as the last one', async () => {
    const { flows, seen } = rig()
    await flows.openPane('handovers')
    expect(seen.nav).toEqual({ sidebar: false, last: 'handovers' })
    await flows.openPane('review')
    expect(seen.nav.last).toBe('review')
  })

  test('m on the band opens the last pane (Session at first) with the sidebar open', async () => {
    const { flows, seen } = rig()
    await flows.openSidebar()
    expect(seen.opened).toEqual(['tm-session'])
    expect(seen.nav).toEqual({ sidebar: true, last: 'session' })
    await flows.openPane('review')
    seen.nav = { ...seen.nav, sidebar: false }
    await flows.openSidebar()
    expect(seen.opened).toEqual(['tm-session', 'tm-review', 'tm-review'])
    expect(seen.nav).toEqual({ sidebar: true, last: 'review' })
  })

  test('r asks tm again; demo says it has nothing newer', async () => {
    let asked = 0
    const live = rig({ refresh: () => void (asked += 1) })
    await live.flows.retry()
    expect(asked).toBe(1)
    const demo = rig()
    await demo.flows.retry()
    expect(demo.seen.toasts).toEqual(['taskmaster-mods: demo data has nothing newer to read'])
  })
})
```

- [ ] **Step 2: Run the suite to see the new tests fail**

Expected: `tests/nav-flows.test.ts` fails (`openSidebar`, `retry` missing; `nav` never written).

- [ ] **Step 3: Implement**

In `mods/taskmaster-mods/types/index.d.ts`, replace:

```ts
      /** Written by the restore scan (Stage 5); the Session tab's SESSION section reads it. Default null. */
      sessionBurst: TmSessionBurst | null
```

with:

```ts
      /** Written by the restore scan (Stage 5); the Session tab's SESSION section reads it. Default null. */
      sessionBurst: TmSessionBurst | null
      /** The Taskmaster sidebar: open over the pane or not, and the pane the band's `m` reopens (spec §15.8). */
      nav: TmNav
```

In `mods/taskmaster-mods/hooks/flows.ts`, replace:

```ts
  TmHandoverSummary,
  TmPhase,
```

with:

```ts
  TmHandoverSummary,
  TmNav,
  TmPhase,
```

and replace:

```ts
import { launchAll, type LaunchResult, type LaunchSpec, launchSummary, resumePrompt } from './launch'
```

with:

```ts
import { launchAll, type LaunchResult, type LaunchSpec, launchSummary, resumePrompt } from './launch'
import { paneOf } from './nav'
```

and replace:

```ts
  restoreHidden: (epoch: number) => Promise<void>
}
```

with:

```ts
  restoreHidden: (epoch: number) => Promise<void>
  nav: (change: (n: TmNav) => TmNav) => Promise<void>
}
```

and replace:

```ts
  const open = async (id: string, title: string): Promise<void> => {
    if (!(await d.host.openPane(id, title))) d.host.toast('taskmaster-mods: widen the terminal to see the pane')
  }
```

with:

```ts
  // Every Taskmaster pane opens through here: it becomes the pane the band's `m` reopens (spec §15.8).
  const open = async (id: string, title: string): Promise<void> => {
    const pane = paneOf(id)
    if (pane !== null) await d.write.nav(n => (n.last === pane ? n : { ...n, last: pane }))
    if (!(await d.host.openPane(id, title))) d.host.toast('taskmaster-mods: widen the terminal to see the pane')
  }
```

and replace:

```ts
  return {
    openReview,
    openHandovers,
    /** Band and Session `1`–`4`: the pane, opened as its own command opens it. */
    openPane: async (pane: TmPane): Promise<void> => {
      if (pane === 'review') return openReview()
      if (pane === 'handovers') return openHandovers()
      return pane === 'session' ? open(SESSION, 'Session') : open(RESTORE, 'Restore')
    },
```

with:

```ts
  /** A pane, opened as its own command opens it. */
  const openPane = async (pane: TmPane): Promise<void> => {
    if (pane === 'review') return openReview()
    if (pane === 'handovers') return openHandovers()
    return pane === 'session' ? open(SESSION, 'Session') : open(RESTORE, 'Restore')
  }

  return {
    openReview,
    openHandovers,
    openPane,
    /** The band's `m`: the last Taskmaster pane (Session at first), opened with the sidebar already open over it. */
    openSidebar: async (): Promise<void> => {
      const last: { pane: TmPane } = { pane: 'session' }
      await d.write.nav(n => {
        last.pane = n.last
        return n.sidebar ? n : { ...n, sidebar: true }
      })
      await openPane(last.pane)
    },
    /** The band's `r` (tm offline) and Session's `u`: a fresh snapshot from tm; demo says it has nothing newer. */
    retry: async (): Promise<void> => {
      if (d.refresh === undefined) d.host.toast('taskmaster-mods: demo data has nothing newer to read')
      else d.refresh()
    },
```

In `mods/taskmaster-mods/hooks/register.tsx`, replace:

```tsx
  TmHandoverSummary,
  TmPhasePane,
```

with:

```tsx
  TmHandoverSummary,
  TmNav,
  TmPhasePane,
```

and replace:

```tsx
import { kitOf, type Ui } from './kit'
```

with:

```tsx
import { kitOf, type Ui } from './kit'
import { FRESH_NAV } from './nav'
```

and replace:

```tsx
const SESSION_BURST = atom({ plugin: 'taskmaster-mods', key: 'sessionBurst' } as const, null as TmSessionBurst | null)
```

with:

```tsx
const SESSION_BURST = atom({ plugin: 'taskmaster-mods', key: 'sessionBurst' } as const, null as TmSessionBurst | null)
const NAV = atom({ plugin: 'taskmaster-mods', key: 'nav' } as const, FRESH_NAV as TmNav)
```

and replace:

```tsx
    restoreHidden: async epoch => {
      await update($, RESTORE_HIDDEN, () => epoch)
    },
  }
}
```

with:

```tsx
    restoreHidden: async epoch => {
      await update($, RESTORE_HIDDEN, () => epoch)
    },
    nav: async change => {
      await update($, NAV, change)
    },
  }
}
```

Every test rig that builds a `TmWriter` with a Proxy (`tests/session-flows.test.ts`, `tests/handover-flows.test.ts`, `tests/bulk.test.ts`) answers unknown writers with a no-op, so they need no change.

- [ ] **Step 4: Run the checks**

Expected: +3 tests pass; nothing else changes; validate ✔ (the new `nav` key is declared); tsc clean.

- [ ] **Step 5: Commit**

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
git -C $WT add mods/taskmaster-mods/types/index.d.ts mods/taskmaster-mods/hooks/flows.ts mods/taskmaster-mods/hooks/register.tsx mods/taskmaster-mods/tests/nav-flows.test.ts
git -C $WT commit -m "feat(taskmaster-mods): navigation state — panes remember the last one; m reopens it with the sidebar; r retries tm"
```

---

### Task B2: The band redrawn (direction C)

**Files:**
- Modify: `mods/taskmaster-mods/hooks/band.tsx` (whole file)
- Modify: `mods/taskmaster-mods/hooks/register.tsx` (the `AbovePrompt` render)
- Modify: `mods/taskmaster-mods/tests/band.test.ts` (whole file)
- Modify: `mods/taskmaster-mods/tests/live.test.ts`, `reload.test.ts`, `session-tab.test.ts`, `session.test.ts`, `retry.test.ts`, `refresh.test.ts` (band expectations), `tests/fixtures/inputs.ts` (`waitingOf`)
- Modify: `mods/taskmaster-mods/hooks/review.tsx` (the summary line keeps a capped total's `+`)

**Interfaces:**
- Consumes: `bandModel`, `BandModel`, `CONNECTING`, `ageLabel`, `oneLine` (model.ts, unchanged); `badge`, `TM_TAG` (kit.tsx: the chip); `actionKey`, `line`, `Seg`, `BADGES`, `badgeInk`, `statusBadgeWord` (vocab.tsx); `TmFlows.openSidebar`, `retry` (Task B1).
- Produces: `bandTree(k: Kit, m: BandModel, a: BandArgs, on: BandHandlers): RenderNode` with `BandArgs = { mode: TmBandMode; width: number; maxRows: number; now: number; reason: string }` and `BandHandlers = { openSidebar(); askDone(id); confirmDone(id); cancel(); copy(id, surface); retry() }`; `RCW = 35` (the prototype's 34 plus the key face's leading cell); `HANDOVER_TAG` (unchanged export, Session imports it). Button keys: `band-done` (d), `band-sidebar` (m), `band-copy` (c), `band-retry` (r), `band-yes` (y), `band-no` (n).

- [ ] **Step 1: Write the failing tests**

Replace the whole of `mods/taskmaster-mods/tests/band.test.ts` with:

```ts
// User intent: the band as the approved prototype draws it (spec §15.5, direction C) — at most two lines on their own raised
// ground; subject text left, keys in one right column; d mark task done, m open sidebar, c copy handover; no counts; and
// its states: no handover, nothing bound, offline with r, connecting, the y/n and a refusal — sized to the room.
import { describe, expect, mock, test } from 'claude-code/testing'

import { RCW } from '../hooks/band'
import type { TmSnapshot } from '../types'
import { BAND, PLUGIN, SESSION } from './fixtures/inputs'
import { type Drawn, isEl, widthOf } from './fixtures/measure'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const TM = { plugins: [RR_STUB] }
const SNAP = `${PLUGIN}.snapshot`
const AUDIT = '2026-10-04-audit-fixes-landed'
const AUDIT_BLOCK = `Audit fixes landed on the native branch\nC:\\Users\\demo\\project\\.taskmaster\\handovers\\${AUDIT}.md\nResume: tm-audit — Record the merge for tm-audit-030`
const OFFLINE: TmSnapshot = {
  reachable: false,
  reason: 'tm: Connection closed',
  fetchedAt: 0,
  queue: [],
  queueTotal: 0,
  handovers: [],
  handoversTotal: 0,
  bound: { taskId: 'tm-audit-030', inferred: false, detail: null, pipeline: null },
}

async function bandOf(ui: { drawn: () => Promise<unknown> }): Promise<{ outer: Drawn; ground: Drawn; lines: Drawn[] }> {
  const drawn = (await ui.drawn()) as Drawn
  const outer = drawn.children?.[0]
  const ground = isEl(outer) ? outer.children?.[0] : undefined
  if (!isEl(outer) || !isEl(ground)) throw new Error(`no band drawn: ${JSON.stringify(drawn)}`)
  return { outer, ground, lines: (ground.children ?? []).filter(isEl) }
}

describe('band', () => {
  test('two lines on their own raised ground, one blank row above, above whatever the other mods drew', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    const { outer, ground, lines } = await bandOf(ui)
    expect(outer.props?.paddingTop).toBe(1)
    expect(ground.props).toMatchObject({ backgroundColor: '#1d1d1b', paddingX: 1 })
    expect(lines).toHaveLength(2)
    for (const text of [' ◆ TASKMASTER ', '● IN REVIEW', 'unified-chat-022', '↳ resumed from ', AUDIT, ' · 20h', /^Audit fixes landed/]) {
      expect(await ui.find({ type: 'Text', text }), text).toBeDefined()
    }
    const tree = JSON.stringify(await ui.drawn())
    expect(tree.indexOf('◆ TASKMASTER')).toBeLessThan(tree.indexOf('BELOW'))
  })

  test('yields entirely to a survey', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const tree = JSON.stringify(await $.ui.render({ ...BAND, props: { ...BAND.props, hasSurvey: true } }))
    expect(tree).not.toContain('TASKMASTER')
    expect(tree).toContain('BELOW')
  })

  test('hidden when nothing is bound, no handover and nothing counted', TM, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect(await $.ui.render(BAND)).toEqual({ type: 'Text', children: ['BELOW'] })
  })

  test('keys: d mark task done and m open sidebar on line 1, c copy handover on line 2; no counts, no pane digits', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    for (const [key, hotkey, label] of [
      ['band-done', 'd', 'mark task done'],
      ['band-sidebar', 'm', 'open sidebar'],
      ['band-copy', 'c', 'copy handover'],
    ] as const) {
      expect((await ui.find({ type: 'Button', key }))?.props, key).toMatchObject({ hotkey, label, plain: true })
    }
    const hotkeys = (await ui.findAll({ type: 'Button' })).map(b => b.props.hotkey)
    expect(hotkeys.sort()).toEqual(['c', 'd', 'm'])
    // Secondary keys only: the band has no filled key.
    expect(JSON.stringify(await ui.drawn())).not.toContain('"backgroundColor":"#8a9eeb"')
  })

  test('the keys sit in one right column of RCW cells on both lines while the band is wide', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    const { lines } = await bandOf(ui)
    for (const l of lines) {
      const column = (l.children ?? []).filter(isEl).at(-1)
      expect(column?.props?.width).toBe(RCW)
    }
  })

  test('m opens the last pane (Session at first) with the sidebar open; c copies the handover block', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await ui.press({ key: 'band-sidebar' })
    await ui.press({ key: 'band-copy' })
    expect(world.opened).toEqual(['tm-session'])
    expect(state.get(`${PLUGIN}.nav`)).toEqual({ sidebar: true, last: 'session' })
    expect(world.copies).toEqual([AUDIT_BLOCK])
    expect(world.toasts).toEqual([`Copied handover ${AUDIT}`])
  })

  test('no handover: line 2 says so with no key; nothing bound: one line with m', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    state.set(`${PLUGIN}.sessionHandover`, null)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    expect((await bandOf(ui)).lines).toHaveLength(2)
    expect(await ui.find({ type: 'Text', text: '↳ no handover yet for this task' })).toBeDefined()
    expect(await ui.find({ type: 'Button', key: 'band-copy' })).toBeUndefined()
    state.set(SNAP, { ...(state.get(SNAP) as TmSnapshot), bound: null })
    await ui.redraw()
    expect((await bandOf(ui)).lines).toHaveLength(1)
    expect(await ui.find({ type: 'Text', text: 'no task bound to this session' })).toBeDefined()
    expect(await ui.find({ type: 'Button', key: 'band-done' })).toBeUndefined()
    expect(await ui.find({ type: 'Button', key: 'band-sidebar' })).toBeDefined()
  })

  test('offline: ▲ tm offline and its reason with r retry tm connection; r asks tm again', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    const state = stateOf(on)
    await $.session.start(SESSION)
    await clock.settle()
    state.set(SNAP, OFFLINE)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    expect(await ui.find({ type: 'Text', text: '▲ tm offline' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: ' · tm: Connection closed' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'band-retry' }))?.props).toMatchObject({ hotkey: 'r', label: 'retry tm connection' })
    expect(await ui.find({ type: 'Text', text: 'tm-audit-030' })).toBeUndefined()
    const before = world.calls.length
    await ui.press({ key: 'band-retry' })
    await clock.settle()
    expect(world.calls.length).toBeGreaterThan(before)
  })

  test('d asks on line 2 (y yes, n no focused); a refusal shows ▲ REFUSED and the words', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await ui.press({ key: 'band-done' })
    expect(await ui.find({ type: 'Text', text: 'mark unified-chat-022 done?' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'band-no' }))?.props).toMatchObject({ hotkey: 'n', autoFocus: true })
    expect(await ui.find({ type: 'Button', key: 'band-yes' })).toBeDefined()
    expect(await ui.find({ type: 'Button', key: 'band-copy' })).toBeUndefined()
    state.set(`${PLUGIN}.band`, { confirmingId: '', refusal: 'Cannot complete: review-gate outstanding', refusalId: 'unified-chat-022' })
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: '▲ REFUSED  ' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'Cannot complete: review-gate outstanding' })).toBeDefined()
  })

  test('every line fits from 30 to 120 columns; labels shorten before a key goes', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const columns of [30, 44, 60, 90, 120]) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND, props: { ...BAND.props, bodyColumns: columns } })
      const { ground, lines } = await bandOf(ui)
      expect(widthOf(ground), `${columns}`).toBeLessThanOrEqual(columns)
      for (const l of lines) expect(widthOf(l), `${columns}: ${JSON.stringify(l)}`).toBeLessThanOrEqual(columns - 2)
      for (const key of ['band-done', 'band-sidebar', 'band-copy']) expect(await ui.find({ type: 'Button', key }), `${columns} ${key}`).toBeDefined()
      if (columns === 60) expect((await ui.find({ type: 'Button', key: 'band-sidebar' }))?.props.label).toBe('sidebar')
      await ui.unmount()
    }
  })

  test('the desktop draws the same keys', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND, surface: 'desktop' })
    for (const key of ['band-done', 'band-sidebar', 'band-copy']) expect(await ui.find({ type: 'Button', key }), key).toBeDefined()
  })
})
```

In `mods/taskmaster-mods/tests/live.test.ts`, in the test `the main agent writing a handover puts it on the band as written here; c copies its block and the row stays`, replace:

```ts
    expect(await ui.find({ type: 'Text', text: '↳ HANDOVER' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'written here ·' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'Live data wired into the band' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'band-copy' }))?.props).toMatchObject({ hotkey: 'c', label: 'copy' })
```

with:

```ts
    expect(await ui.find({ type: 'Text', text: '↳ written here ' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: '2026-10-06-live-data-wired' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'Live data wired into the band' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'band-copy' }))?.props).toMatchObject({ hotkey: 'c', label: 'copy handover' })
```

and replace:

```ts
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: 'written here ·' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'band-review' }))?.props.label).toBe('4 waiting on you')
```

with:

```ts
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: '↳ written here ' })).toBeDefined()
    expect(await ui.find({ type: 'Button', key: 'band-review' })).toBeUndefined()
```

In the test `with no clipboard the toast carries the path and the row stays`, replace:

```ts
    expect(await ui.find({ type: 'Text', text: '↳ HANDOVER' })).toBeDefined()
  })
```

with:

```ts
    expect(await ui.find({ type: 'Text', text: '↳ written here ' })).toBeDefined()
  })
```

In the test `offline, the handover written here still shows with c, beside the offline signal, and a 30-column band fits`, replace:

```ts
    for (const row of elementsOf(tree).filter(e => e.type === 'Box' && e.props?.flexDirection === 'row' && e.props?.columnGap === 2)) {
      expect(widthOf(row)).toBeLessThanOrEqual(28)
    }
    for (const key of ['band-copy', 'band-review', 'band-handovers', 'band-session']) expect(await narrow.find({ type: 'Button', key }), key).toBeDefined()
    await narrow.unmount()
    const wide = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    expect(await wide.find({ type: 'Text', text: ' offline' })).toBeDefined()
    expect(await wide.find({ type: 'Text', text: 'written here ·' })).toBeDefined()
```

with:

```ts
    for (const row of elementsOf(tree).filter(e => e.type === 'Box' && e.props?.flexDirection === 'row')) {
      expect(widthOf(row)).toBeLessThanOrEqual(28)
    }
    for (const key of ['band-copy', 'band-retry']) expect(await narrow.find({ type: 'Button', key }), key).toBeDefined()
    await narrow.unmount()
    const wide = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    expect(await wide.find({ type: 'Text', text: '▲ tm offline' })).toBeDefined()
    expect(await wide.find({ type: 'Text', text: '↳ written here ' })).toBeDefined()
```

In `mods/taskmaster-mods/tests/reload.test.ts`, replace:

```ts
    await ui.press({ key: 'band-yes' })
    expect((await ui.find({ type: 'Button', key: 'band-review' }))?.props.label).toBe('4 waiting on you')
```

with:

```ts
    await ui.press({ key: 'band-yes' })
    expect(await ui.find({ type: 'Button', key: 'band-yes' })).toBeUndefined()
```

In `mods/taskmaster-mods/tests/session-tab.test.ts`, replace:

```ts
  test('/tm-session and the band 3 open it', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect(await $.command.run(command('tm-session'))).toEqual({ text: 'Session opened.' })
    const band = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await band.press({ key: 'band-session' })
```

with:

```ts
  test('/tm-session and the band m open it', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect(await $.command.run(command('tm-session'))).toEqual({ text: 'Session opened.' })
    const band = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await band.press({ key: 'band-sidebar' })
```

The band no longer carries the waiting count, so the data tests that read "N waiting on you" off the band read it off the review pane's summary line instead (the sidebar shows the same total). Add to `mods/taskmaster-mods/tests/fixtures/inputs.ts` (after `pane`):

```ts
/** "N waiting on you" as the person reads it now the band has no counts: the review pane's summary line, drawn as JSON. */
export const waitingOf = async ($: { ui: { render: (input: RenderInput<'Pane'>) => Promise<unknown> } }): Promise<string> =>
  JSON.stringify(await $.ui.render(pane('tm-review')))
```

In `mods/taskmaster-mods/hooks/review.tsx` (the summary line keeps a capped total's `+`, as the band did), replace:

```tsx
    `${s.queueTotal} waiting on you`,
```

with:

```tsx
    `${s.queueTotal}${s.queueCapped === true ? '+' : ''} waiting on you`,
```

Then, in the tests:

- `tests/session.test.ts`: replace `expect(tree).toContain(' IN PROGRESS ')` with `expect(tree).toContain('● IN PROGRESS')`; replace `expect(tree).toContain(' IN REVIEW ')` with `expect(tree).toContain('● IN REVIEW')`; replace **both** `expect(tree).toContain('4 waiting on you')` with `expect(tree).toContain('no task bound to this session')`.
- `tests/live.test.ts`: add `waitingOf` to the `./fixtures/inputs` import; replace `expect(await band($)).toContain('357 waiting on you')` with `expect(await waitingOf($)).toContain('357 waiting on you')`; in the `50+` test replace `const tree = await band($)` with `const tree = await waitingOf($)`; replace `expect(await band($)).toContain('11 waiting on you')` and `expect(await band($)).toContain('4 waiting on you')` with the same calls on `waitingOf($)`; replace every `'HANDOVER')` in `toContain('HANDOVER')` / `not.toContain('HANDOVER')` with `'↳ written here')` (four lines in `a failed or subagent handover_create …`).
- `tests/retry.test.ts`: add `waitingOf` to the `./fixtures/inputs` import; replace every `expect(JSON.stringify(await $.ui.render(BAND))).toContain('4 waiting on you')` (three) with `expect(await waitingOf($)).toContain('4 waiting on you')`.
- `tests/refresh.test.ts`, test `a refused connection: …`: replace

```ts
    const tree = JSON.stringify(await $.ui.render(BAND))
    expect(tree).toContain('tm-audit-030')
    expect(tree).toContain(' offline')
    expect(tree).toContain('– waiting on you')
```

with

```ts
    const tree = JSON.stringify(await $.ui.render(BAND))
    expect(tree).toContain('▲ tm offline')
    expect(tree).toContain('retry tm connection')
```

`RenderInput` is already imported as a type in `tests/fixtures/inputs.ts`. If `tsc` reports `BAND` unused in `retry.test.ts` after the edit, drop it from that import.

- [ ] **Step 2: Run the suite to see the new tests fail**

Expected: most of `tests/band.test.ts` fails (no `band-sidebar`, counts still drawn, `RCW` not exported), and the edited live/reload/session-tab expectations fail.

- [ ] **Step 3: Implement**

Replace the whole of `mods/taskmaster-mods/hooks/band.tsx` with:

```tsx
// User intent: the band above the prompt (spec §15.5, direction C) — at most two lines on their own raised ground: this
// session's task and its handover as subject text on the left, their keys in one fixed right column, no counts — so it is
// never mistaken for transcript text and never competes with it.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode, RenderSurface } from 'claude-code'

import type { TmBandMode, TmSessionHandover } from '../types'
import { badge, type Kit, type Piece, TM_TAG } from './kit'
import { ageLabel, type BandModel, CONNECTING, oneLine } from './model'
import { actionKey, BADGES, badgeInk, line, type Seg, statusBadgeWord } from './vocab'

export type BandHandlers = {
  readonly openSidebar: () => void
  readonly askDone: (taskId: string) => void
  readonly confirmDone: (taskId: string) => void
  readonly cancel: () => void
  readonly copy: (handoverId: string, surface: RenderSurface) => void
  readonly retry: () => void
}

/** What the band draws besides the model: the y/n or refusal state, its room, the time (handover age) and the offline reason. */
export type BandArgs = {
  readonly mode: TmBandMode
  readonly width: number
  readonly maxRows: number
  readonly now: number
  readonly reason: string
}

/** The right column's cells while the band is at least 80 cells wide: the keys line up under each other. */
export const RCW = 35
/** The subject keeps this many cells before the labels shorten. */
const MIN_TEXT = 12

/** How the band names a D5 handover. */
export const HANDOVER_TAG: Readonly<Record<TmSessionHandover['tag'], string>> = {
  'written here': 'written here',
  'resumed from': 'resumed from',
  task: 'for this task',
}

/** Each key's label at each squeeze level: whole, short, then the key alone. */
const LABELS = {
  done: ['mark task done', 'done', ''],
  sidebar: ['open sidebar', 'sidebar', ''],
  copy: ['copy handover', 'copy', ''],
  retry: ['retry tm connection', 'retry', ''],
  yes: ['yes', 'yes', ''],
  no: ['no', 'no', ''],
} as const
type Level = 0 | 1 | 2 | 3
const LEVELS: readonly Level[] = [0, 1, 2, 3]
const labelAt = (name: keyof typeof LABELS, level: Level): string => LABELS[name][Math.min(level, 2)] ?? ''

/** One line: what leads it (the chip, or the indent of line 2), its subject segments, and its keys. */
type BandLine = { readonly lead: Piece; readonly segs: readonly Seg[]; readonly keys: readonly Piece[] }

const keysWidth = (keys: readonly Piece[]): number => keys.reduce((n, p) => n + p.width, 0) + 3 * Math.max(0, keys.length - 1)

function badgeSeg(k: Kit, status: string): Seg {
  const word = statusBadgeWord(status)
  const spec = BADGES[word]
  return { text: `${spec?.glyph ?? '·'} ${word}`, color: badgeInk(k, word), bold: spec?.bold ?? false }
}

/** The band (spec §15.5). Squeezes level by level (short labels, the short chip, keys alone) until every subject keeps MIN_TEXT. */
export function bandTree(k: Kit, m: BandModel, a: BandArgs, on: BandHandlers): RenderNode {
  const { Box, Text } = k.ui
  const { t } = k
  const inner = Math.max(1, a.width - 2)
  const task = m.task
  const offline = m.fault === 'offline'
  const confirming = task !== null && a.mode.confirmingId === task.id
  const refused = task !== null && a.mode.refusal !== '' && a.mode.refusalId === task.id
  const sp = (n: number): Seg => ({ text: ' '.repeat(n), color: t.fg.default })

  const build = (level: Level): BandLine[] => {
    const chip = badge(k, level <= 1 ? TM_TAG : '◆ TM', 'signature', { on: 'raised' })
    const lead1: Piece = {
      el: (
        <Box flexDirection="row">
          {chip.el}
          <Text>{'  '}</Text>
        </Box>
      ),
      width: chip.width + 2,
    }
    const lead2: Piece = { el: <Text>{'  '}</Text>, width: 2 }
    const key = (id: string, letter: string, name: keyof typeof LABELS, press: Parameters<typeof actionKey>[1]['onPress'], extra: { autoFocus?: boolean } = {}): Piece =>
      actionKey(k, { id, key: letter, label: labelAt(name, level), on: 'raised', onPress: press, ...extra })
    const sidebar = key('band-sidebar', 'm', 'sidebar', () => on.openSidebar())
    const lines: BandLine[] = []

    if (offline) {
      lines.push({
        lead: lead1,
        segs: [
          { text: '▲ tm offline', color: t.tone.critical, bold: true },
          { text: a.reason === '' ? '' : ` · ${oneLine(a.reason)}`, color: t.fg.subtle },
        ],
        keys: [key('band-retry', 'r', 'retry', () => on.retry())],
      })
    } else if (m.fault === 'connecting' && task === null) {
      lines.push({ lead: lead1, segs: [{ text: CONNECTING, color: t.fg.subtle }], keys: [sidebar] })
    } else if (task === null) {
      lines.push({ lead: lead1, segs: [{ text: 'no task bound to this session', color: t.fg.disabled }], keys: [sidebar] })
    } else {
      const title = oneLine(task.title)
      lines.push({
        lead: lead1,
        segs: [
          ...(task.status === '' || level >= 3 ? [] : [badgeSeg(k, task.status), sp(2)]),
          { text: task.id, color: t.fg.subtle },
          ...(title === '' ? [] : [sp(2), { text: title, color: task.inferred ? t.fg.subtle : t.fg.bold, bold: !task.inferred }]),
        ],
        keys: [...(task.ready ? [key('band-done', 'd', 'done', () => on.askDone(task.id))] : []), sidebar],
      })
    }

    if (task !== null && confirming) {
      lines.push({
        lead: lead2,
        segs: [{ text: `mark ${task.id} done?`, color: t.tone.warning, bold: true }],
        keys: [key('band-yes', 'y', 'yes', () => on.confirmDone(task.id)), key('band-no', 'n', 'no', () => on.cancel(), { autoFocus: true })],
      })
    } else if (task !== null && refused) {
      lines.push({
        lead: lead2,
        segs: [
          { text: '▲ REFUSED  ', color: t.tone.critical, bold: true },
          { text: oneLine(a.mode.refusal), color: t.fg.default },
        ],
        keys: [],
      })
    } else if (m.handover !== null) {
      const ho = m.handover
      const age = ageLabel(ho.created, a.now)
      lines.push({
        lead: lead2,
        segs: [
          { text: `↳ ${HANDOVER_TAG[ho.tag]} `, color: t.fg.subtle },
          { text: ho.id, color: t.signatureText },
          { text: age === '' ? '' : ` · ${age}`, color: t.fg.subtle },
          { text: ' · ', color: t.fg.subtle },
          { text: oneLine(ho.tldr), color: t.fg.default },
        ],
        keys: [key('band-copy', 'c', 'copy', press => on.copy(ho.id, press.surface))],
      })
    } else if (task !== null && !offline) {
      lines.push({ lead: lead2, segs: [{ text: '↳ no handover yet for this task', color: t.fg.disabled }], keys: [] })
    }
    return lines
  }

  const subjectLen = (l: BandLine): number => l.segs.reduce((n, s) => n + s.text.length, 0)
  const columnOf = (lines: readonly BandLine[], level: Level): number => {
    const natural = Math.max(0, ...lines.map(l => keysWidth(l.keys)))
    return level === 0 && inner >= 80 ? Math.max(RCW, natural) : natural
  }
  const roomOf = (l: BandLine, column: number): number => inner - l.lead.width - column - 2
  let level: Level = 3
  for (const at of LEVELS) {
    const lines = build(at)
    const column = columnOf(lines, at)
    if (lines.every(l => roomOf(l, column) >= Math.min(MIN_TEXT, subjectLen(l)))) {
      level = at
      break
    }
  }
  const lines = build(level)
  const column = columnOf(lines, level)
  const drawn = lines.map(l => {
    const subject = line(k, l.segs, Math.max(0, roomOf(l, column)))
    return (
      <Box flexDirection="row">
        {l.lead.el}
        {subject.el}
        <Box flexGrow={1} />
        <Text>{'  '}</Text>
        <Box flexDirection="row" columnGap={3} width={column}>
          {l.keys.map(p => p.el)}
        </Box>
      </Box>
    )
  })

  // The rows the prompt area gives: a blank row above only with room to spare; a confirm keeps its y/n over line 1.
  const blank = a.maxRows >= drawn.length + 1 && a.maxRows >= 3
  const room = Math.max(1, a.maxRows - (blank ? 1 : 0))
  const kept = drawn.length <= room ? drawn : confirming ? drawn.slice(-room) : drawn.slice(0, room)
  return (
    <Box flexDirection="column" paddingTop={blank ? 1 : 0}>
      <Box flexDirection="column" backgroundColor={t.surface.raised} paddingX={1}>
        {kept}
      </Box>
    </Box>
  )
}
```

`measure.ts`'s `widthOf` sums a row's children, so a line measures lead + subject + 2 + the keys (the spacer and the column's `width` prop count 0); `roomOf` keeps that within `inner`.

In `mods/taskmaster-mods/hooks/register.tsx`, replace:

```tsx
    const ours = bandTree(k, model, await read($, BAND), e.props.bodyColumns, e.props.maxRows, {
      open: pane => act(f => f.openPane(pane)),
      askDone: id => act(f => f.bandAskDone(id)),
      confirmDone: id => act(f => f.bandConfirmDone(id)),
      cancel: () => act(f => f.bandCancel()),
      copy: (id, surface) => act(f => f.copyHandover(id, surface)),
      hideRestore: () => act(f => (pending === null ? Promise.resolve() : f.hideRestore(pending.epoch))),
    })
```

with:

```tsx
    const ours = bandTree(
      k,
      model,
      { mode: await read($, BAND), width: e.props.bodyColumns, maxRows: e.props.maxRows, now: await $.clock.now(), reason: snapshot?.reason ?? '' },
      {
        openSidebar: () => act(f => f.openSidebar()),
        askDone: id => act(f => f.bandAskDone(id)),
        confirmDone: id => act(f => f.bandConfirmDone(id)),
        cancel: () => act(f => f.bandCancel()),
        copy: (id, surface) => act(f => f.copyHandover(id, surface)),
        retry: () => act(f => f.retry()),
      },
    )
```

`pending` stays: `bandModel` still uses the restore burst to decide whether the band shows.

- [ ] **Step 4: Run the checks**

Expected: `tests/band.test.ts` passes (11 tests, replacing the old file's); the edited live, reload and session-tab tests pass; nothing else changes; validate ✔; tsc clean. A failing `the keys sit in one right column` test means the line's last child is not the key column: keep the column Box last in the row.

- [ ] **Step 5: Commit**

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
git -C $WT add mods/taskmaster-mods/hooks/band.tsx mods/taskmaster-mods/hooks/register.tsx mods/taskmaster-mods/hooks/review.tsx mods/taskmaster-mods/tests
git -C $WT commit -m "feat(taskmaster-mods): the band in direction C — subject left, keys in one right column, no counts, m open sidebar"
```

---

### Task B3: Live check with the user (Stage B)

Orchestrator task; no subagent.

- [ ] **Step 1: Smoke** — as Task A6 Step 1.
- [ ] **Step 2: Ask the user** to open a demo session and a real one (claude-tools or CodeMaestro, where tm answers) at 120+ columns and compare the band with `Main.dc.html`'s BAND and BAND · STATES: the right column, `d` only when ready, `↳ resumed from …`, `c copy handover`; ctrl+x tab to the band and press `m` (it opens the Session pane; the sidebar itself arrives in Stage C); narrow the terminal to ~60 columns and check the labels shorten; if possible stop the tm server to see the offline line and `r`.
- [ ] **Step 3: Record** — ledger line `Task B3: Stage B live check — <words>`. Stop until the user signs off.

---

## Stage C: Sidebar and navigation

**Goal.** One Taskmaster pane at a time, no engine tab row, and the sidebar drawn over the pane (spec §15.8 S1–S4): `1`–`4` jump, the current pane marked, Esc closes the sidebar before the pane.

### Task C1: One pane at a time, `goTo`, `toggleSidebar`, and Esc

**Files:**
- Modify: `mods/taskmaster-mods/hooks/host.ts` (`panes?`)
- Modify: `mods/taskmaster-mods/hooks/flows.ts` (`closeOthers` in `open`; `goTo`, `toggleSidebar`)
- Modify: `mods/taskmaster-mods/hooks/register.tsx` (`hostOf().panes`; the `ui.close` hook)
- Modify: `mods/taskmaster-mods/tests/fixtures/world.ts` (open panes; `ui.panes`)
- Modify: `mods/taskmaster-mods/tests/nav-flows.test.ts` (append)
- Create: `mods/taskmaster-mods/tests/nav-pane.test.ts`

**Interfaces:**
- Consumes: `TM_PANE_IDS`, `PANE_IDS`, `paneOf`, `escLayer` (nav.ts); `SIDEBAR_ESC` (vocab.tsx); Task B1's `nav` writer.
- Produces: `TmHost.panes?: () => Promise<readonly string[]>` (the ids of this plugin's open panes; absent: nothing is closed); every Taskmaster open first closes the plugin's other open Taskmaster panes; `TmFlows.goTo(pane: TmPane): Promise<void>` (closes the sidebar; opens that pane, or only closes the sidebar when it is already open); `TmFlows.toggleSidebar(): Promise<void>` (flips `nav.sidebar`; opening it closes every phase list and disarms review's ticking); the `ui.close` hook (person's Esc closes the sidebar, else an open phase list, else the pane; any pane close lets `nav.sidebar` go).

- [ ] **Step 1: Write the failing tests**

In `mods/taskmaster-mods/tests/fixtures/world.ts`, replace:

```ts
  let queue: Promise<void> = Promise.resolve()
```

with:

```ts
  let queue: Promise<void> = Promise.resolve()
  // The panes open now (`$.ui.panes()`), as the engine keeps them: one per id, in open order.
  const open = new Set<string>()
```

and replace:

```ts
  on('ui.open', ($, e) => {
    world.opened.push(e.id)
    return { value: { isPlaced: true } } as never
  })
  on('ui.close', ($, e) => {
    world.closed.push(e.id)
    return { value: undefined }
  })
```

with:

```ts
  on('ui.open', ($, e) => {
    world.opened.push(e.id)
    open.add(e.id)
    return { value: { isPlaced: true } } as never
  })
  on('ui.close', ($, e) => {
    world.closed.push(e.id)
    open.delete(e.id)
    return { value: undefined }
  })
  on('ui.panes', () => ({ value: [...open].map(id => ({ id, title: id, isShown: true, isFocused: false, isPlaced: true })) }) as never)
```

Append to `mods/taskmaster-mods/tests/nav-flows.test.ts` (and add `panes` to its rig: replace `closePane: async (id: string) => void seen.closed.push(id),` with the two lines below):

```ts
      closePane: async (id: string) => void seen.closed.push(id),
      panes: async () => seen.openNow,
```

and in `rig`, replace `const seen = { opened: [] as string[], closed: [] as string[], toasts: [] as string[], nav: FRESH_NAV as TmNav }` with:

```ts
  const seen = { opened: [] as string[], closed: [] as string[], toasts: [] as string[], nav: FRESH_NAV as TmNav, openNow: [] as string[] }
```

then append:

```ts
describe('one pane at a time', () => {
  test('opening a Taskmaster pane closes the other open Taskmaster panes first, never another plugin pane or itself', async () => {
    const { flows, seen } = rig()
    seen.openNow = ['tm-review', 'tm-kit', 'tm-handovers']
    await flows.openPane('handovers')
    expect(seen.closed).toEqual(['tm-review'])
    expect(seen.opened).toEqual(['tm-handovers'])
  })

  test('goTo closes the sidebar and opens the pane; the pane already open only loses its sidebar', async () => {
    const { flows, seen } = rig()
    seen.nav = { sidebar: true, last: 'session' }
    seen.openNow = ['tm-session']
    await flows.goTo('session')
    expect(seen.opened).toEqual([])
    expect(seen.nav).toEqual({ sidebar: false, last: 'session' })
    seen.nav = { sidebar: true, last: 'session' }
    await flows.goTo('review')
    expect(seen.closed).toEqual(['tm-session'])
    expect(seen.opened).toEqual(['tm-review'])
    expect(seen.nav).toEqual({ sidebar: false, last: 'review' })
  })

  test('m in a pane flips the sidebar', async () => {
    const { flows, seen } = rig()
    await flows.toggleSidebar()
    expect(seen.nav.sidebar).toBe(true)
    await flows.toggleSidebar()
    expect(seen.nav.sidebar).toBe(false)
  })

  test('a host without panes (older rigs) closes nothing', async () => {
    const { flows, seen } = rig()
    seen.openNow = undefined as unknown as string[]
    await flows.openPane('review')
    expect(seen.closed).toEqual([])
  })
})
```

`mods/taskmaster-mods/tests/nav-pane.test.ts`:

```ts
// User intent: one Taskmaster pane at a time through the real hooks (spec §15.8 S2, S4) — opening a pane closes the other,
// so the engine never draws its tab row; the plugin's own closes pass; any close lets the sidebar go.
import { describe, expect, mock, test } from 'claude-code/testing'

import { command, PLUGIN, SESSION } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }

describe('one pane at a time', () => {
  test('/tm-review then /tm-handovers: review closes before handovers opens', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    await $.command.run(command('tm-review'))
    await $.command.run(command('tm-handovers'))
    expect(world.opened).toEqual(['tm-review', 'tm-handovers'])
    expect(world.closed).toEqual(['tm-review'])
  })

  test("the plugin's own close passes, and a closed pane leaves the sidebar shut", DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    await $.command.run(command('tm-session'))
    state.set(`${PLUGIN}.nav`, { sidebar: true, last: 'session' })
    await $.ui.close({ id: 'tm-session' })
    expect(world.closed).toEqual(['tm-session'])
    expect(state.get(`${PLUGIN}.nav`)).toEqual({ sidebar: false, last: 'session' })
  })
})
```

- [ ] **Step 2: Run the suite to see the new tests fail**

Expected: the new `one pane at a time` tests fail (`goTo`, `toggleSidebar` missing; nothing closes).

- [ ] **Step 3: Implement**

In `mods/taskmaster-mods/hooks/host.ts`, replace:

```ts
  closePane: (id: string) => Promise<void>
```

with:

```ts
  closePane: (id: string) => Promise<void>
  /** The ids of this plugin's open panes (`$.ui.panes()`); absent (older rigs): nothing is closed before an open. */
  panes?: () => Promise<readonly string[]>
```

In `mods/taskmaster-mods/hooks/flows.ts`, replace:

```ts
import { paneOf } from './nav'
```

with:

```ts
import { PANE_IDS, paneOf, TM_PANE_IDS } from './nav'
```

and replace:

```ts
  // Every Taskmaster pane opens through here: it becomes the pane the band's `m` reopens (spec §15.8).
  const open = async (id: string, title: string): Promise<void> => {
    const pane = paneOf(id)
```

with:

```ts
  // One Taskmaster pane at a time (spec §15.8 S2): the plugin's other open Taskmaster panes close first, so the engine never
  // draws its tab row. A host without `panes` (or one answering something else) closes nothing.
  const closeOthers = async (keep: string): Promise<void> => {
    const open = await d.host.panes?.()
    if (!Array.isArray(open)) return
    for (const id of open) if (id !== keep && TM_PANE_IDS.includes(id)) await d.host.closePane(id)
  }
  // Every Taskmaster pane opens through here: it becomes the pane the band's `m` reopens (spec §15.8).
  const open = async (id: string, title: string): Promise<void> => {
    await closeOthers(id)
    const pane = paneOf(id)
```

and replace:

```ts
    /** The band's `r` (tm offline) and Session's `u`: a fresh snapshot from tm; demo says it has nothing newer. */
```

with:

```ts
    /** A sidebar entry: the sidebar closes and that pane opens; the pane already open only loses its sidebar. */
    goTo: async (pane: TmPane): Promise<void> => {
      await d.write.nav(n => (n.sidebar ? { ...n, sidebar: false } : n))
      const open = await d.host.panes?.()
      if (Array.isArray(open) && open.includes(PANE_IDS[pane])) return
      await openPane(pane)
    },
    /** `m` in a pane: the sidebar opens or closes; opening it closes every phase list and lets review's ticking go. */
    toggleSidebar: async (): Promise<void> => {
      const was = { open: false }
      await d.write.nav(n => {
        was.open = n.sidebar
        return { ...n, sidebar: !n.sidebar }
      })
      if (was.open) return
      for (const pane of ['review', 'handovers', 'restore'] as const) await d.write.phaseView(pane, v => (v.overlay ? { ...v, overlay: false } : v))
      await d.write.reviewList(l => (l.ticking ? { ...l, ticking: false } : l))
    },
    /** The band's `r` (tm offline) and Session's `u`: a fresh snapshot from tm; demo says it has nothing newer. */
```

and, in `setOverlay`, replace:

```ts
    setOverlay: async (pane: TmPhasePane, open: boolean): Promise<void> => {
      await d.write.phaseView(pane, v => ({ ...v, overlay: open }))
```

with:

```ts
    setOverlay: async (pane: TmPhasePane, open: boolean): Promise<void> => {
      await d.write.phaseView(pane, v => ({ ...v, overlay: open }))
      // The phase list and the sidebar never stand open together: their digits would clash.
      if (open) await d.write.nav(n => (n.sidebar ? { ...n, sidebar: false } : n))
```

In `mods/taskmaster-mods/hooks/register.tsx`, replace:

```tsx
    closePane: id => $.ui.close({ id }),
```

with:

```tsx
    closePane: id => $.ui.close({ id }),
    panes: async () => (await $.ui.panes()).map(pane => pane.id),
```

and replace:

```tsx
import { FRESH_NAV } from './nav'
```

with:

```tsx
import { escLayer, FRESH_NAV, paneOf } from './nav'
import { SIDEBAR_ESC } from './vocab'
```

and, after the `ui.focus` hook for `tm-handovers` (the last hook in `register`), add before the closing `}` of `register`:

```tsx

  // Esc closes the innermost layer first (spec §15.8 S4): the person's close of a Taskmaster pane is refused while its sidebar
  // or phase list is open, and closes that instead. Every close that goes through lets the sidebar go with its pane.
  on('ui.close', async ($, e, next) => {
    const pane = paneOf(e.id)
    if (pane === null) return next(e)
    if (e.origin.kind === 'person' && SIDEBAR_ESC === 'refuse') {
      const nav = await read($, NAV)
      const views = await read($, PHASE_VIEWS)
      const menuOpen = pane !== 'session' && views[pane].overlay
      const layer = escLayer(nav, menuOpen)
      if (layer === 'sidebar') {
        await update($, NAV, n => ({ ...n, sidebar: false }))
        return { value: undefined }
      }
      if (layer === 'menu' && pane !== 'session') {
        await update($, PHASE_VIEWS, all => ({ ...all, [pane]: { ...all[pane], overlay: false } }))
        return { value: undefined }
      }
    }
    const closed = await next(e)
    await update($, NAV, n => (n.sidebar ? { ...n, sidebar: false } : n))
    return closed
  }).catch(($, e, next) => next(e)) // a failed read or write never keeps a pane open: replay what the chain beneath settled
```

If `tsc` rejects `{ value: undefined }` as the hook's answer, read the `ui.close` result type in `.claude-plugin/types/claude-code/index.d.ts` (`'ui.close': void` in the results table) and answer in that shape; the test fixture's bottom hook answers `{ value: undefined }`, which compiles there.

- [ ] **Step 4: Run the checks**

Expected: +6 tests pass; every older test still passes (rigs without `panes` close nothing; the world now answers `ui.panes`); validate ✔; tsc clean. If an older test's `world.closed` now lists an extra pane, that test opened two Taskmaster panes in turn: the new list is correct; update that expectation to the ids it now shows and name the test in the commit message.

- [ ] **Step 5: Commit**

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
git -C $WT add mods/taskmaster-mods/hooks/host.ts mods/taskmaster-mods/hooks/flows.ts mods/taskmaster-mods/hooks/register.tsx mods/taskmaster-mods/tests/fixtures/world.ts mods/taskmaster-mods/tests/nav-flows.test.ts mods/taskmaster-mods/tests/nav-pane.test.ts
git -C $WT commit -m "feat(taskmaster-mods): one Taskmaster pane at a time; goTo and m toggle the sidebar; Esc closes the innermost layer"
```

---

### Task C2: The sidebar drawn over every Taskmaster pane

**Files:**
- Modify: `mods/taskmaster-mods/hooks/register.tsx` (a `sidebarFor` helper; the review, handovers and session pane renders wrap their tree with `withSidebar`)
- Create: `mods/taskmaster-mods/tests/sidebar.test.ts`

**Interfaces:**
- Consumes: `sidebarView`, `PANE_IDS` (nav.ts); `withSidebar` (vocab.tsx); `TmFlows.goTo` (Task C1); `NAV`, `restoreShown`, `dataOf` (register.tsx).
- Produces: in register.tsx, `async function sidebarFor($: EngineInterface, current: TmPane): Promise<SidebarView | null>` (null while `nav.sidebar` is off); each of the three pane renders returns `withSidebar(k, tree, await sidebarFor($, '<pane>'), pane => act(f => f.goTo(pane)))`. Stage D keeps this wrapper when it swaps the trees; Task E7 adds it to the restore pane.

- [ ] **Step 1: Write the failing tests**

`mods/taskmaster-mods/tests/sidebar.test.ts`:

```ts
// User intent: the Taskmaster sidebar as the person meets it (spec §15.8) — m on the band opens the pane with the sidebar over
// it, 1–4 jump (the current one marked, Restore dim until built), a jump closes the sidebar and the pane before opening the next.
import { describe, expect, mock, test } from 'claude-code/testing'

import { BAND, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }

describe('the sidebar', () => {
  test('band m: the Session pane opens with the sidebar over it, Session marked and focused', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    stateOf(on)
    await $.session.start(SESSION)
    const band = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await band.press({ key: 'band-sidebar' })
    expect(world.opened).toEqual(['tm-session'])
    const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-session') })
    for (const [key, hotkey] of [['nav:review', '1'], ['nav:handovers', '2'], ['nav:session', '3'], ['nav:restore', '4']] as const) {
      expect((await ui.find({ type: 'Button', key }))?.props?.hotkey, key).toBe(hotkey)
    }
    expect((await ui.find({ type: 'Button', key: 'nav:session' }))?.props?.autoFocus).toBe(true)
    expect((await ui.find({ type: 'Button', key: 'nav:restore' }))?.props?.dimColor).toBe(true)
  })

  test('2 jumps to Handovers: the sidebar closes, Session closes, Handovers opens without it', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    const band = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await band.press({ key: 'band-sidebar' })
    const session = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-session') })
    await session.press({ key: 'nav:handovers' })
    expect(world.closed).toEqual(['tm-session'])
    expect(world.opened).toEqual(['tm-session', 'tm-handovers'])
    expect(state.get(`${PLUGIN}.nav`)).toEqual({ sidebar: false, last: 'handovers' })
    const handovers = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-handovers') })
    expect(await handovers.find({ type: 'Button', key: 'nav:review' })).toBeUndefined()
  })

  test('the review pane draws it too while it is open, Review marked', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    state.set(`${PLUGIN}.nav`, { sidebar: true, last: 'review' })
    const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
    expect((await ui.find({ type: 'Button', key: 'nav:review' }))?.props?.autoFocus).toBe(true)
  })
})
```

- [ ] **Step 2: Run the suite to see the new tests fail**

Expected: the three sidebar tests fail (no `nav:*` Buttons drawn).

- [ ] **Step 3: Implement**

In `mods/taskmaster-mods/hooks/register.tsx`, replace:

```tsx
import { escLayer, FRESH_NAV, paneOf } from './nav'
import { SIDEBAR_ESC } from './vocab'
```

with:

```tsx
import type { TmPane } from './flows'
import { escLayer, FRESH_NAV, paneOf, type SidebarView, sidebarView } from './nav'
import { SIDEBAR_ESC, withSidebar } from './vocab'
```

(`flows.ts` is already imported for `createFlows`; merge `type TmPane` into that import line if the linter prefers one import per module: `import { createFlows, type TmFlows, type TmPane, type TmWriter } from './flows'`.)

After `restoreShown` (the function `async function restoreShown($: EngineInterface)`), add:

```tsx

/** The sidebar over a pane while `nav.sidebar` is on (null: off): its entries from the snapshot and the pending restore burst. */
async function sidebarFor($: EngineInterface, current: TmPane): Promise<SidebarView | null> {
  if (!(await read($, NAV)).sidebar) return null
  const { snapshot } = await dataOf($)
  return sidebarView({ current, snapshot, restore: await restoreShown($), now: await $.clock.now() })
}
```

In the `tm-review` pane render, replace:

```tsx
    return reviewPaneTree(
      $.ui.resolve(e) as unknown as Ui,
      rrOf($),
      view,
```

with:

```tsx
    const ui = $.ui.resolve(e) as unknown as Ui
    const tree = await reviewPaneTree(
      ui,
      rrOf($),
      view,
```

and replace the end of that call:

```tsx
        bulkSendBack: (ids, note, actions) => act(f => f.bulkSendBack(ids, note, actions)),
      },
      e.props.bodyColumns,
    )
  })
```

with:

```tsx
        bulkSendBack: (ids, note, actions) => act(f => f.bulkSendBack(ids, note, actions)),
      },
      e.props.bodyColumns,
    )
    const k = await kitOf(ui, rrOf($), e.surface, text => mod.host?.toast(text))
    return withSidebar(k, tree, await sidebarFor($, 'review'), pane => act(f => f.goTo(pane)))
  })
```

In the `tm-handovers` pane render, replace:

```tsx
    return (
      <Box {...ground} flexGrow={1}>
        {tree}
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: SESSION }, async ($, e) => {
```

with:

```tsx
    return withSidebar(
      k,
      <Box {...ground} flexGrow={1}>
        {tree}
      </Box>,
      await sidebarFor($, 'handovers'),
      pane => act(f => f.goTo(pane)),
    )
  })

  on('ui.render', { component: 'Pane', requestId: SESSION }, async ($, e) => {
```

In the `SESSION` pane render, replace:

```tsx
    const ground = await $.rr.surfaceProps({ level: 'page' })
    const { Box } = ui
    return (
      <Box {...ground} flexGrow={1}>
        {tree}
      </Box>
    )
  })

  on('ui.render', { component: 'Pane', requestId: KIT }, async ($, e) => {
```

with:

```tsx
    const ground = await $.rr.surfaceProps({ level: 'page' })
    const { Box } = ui
    return withSidebar(
      k,
      <Box {...ground} flexGrow={1}>
        {tree}
      </Box>,
      await sidebarFor($, 'session'),
      pane => act(f => f.goTo(pane)),
    )
  })

  on('ui.render', { component: 'Pane', requestId: KIT }, async ($, e) => {
```

- [ ] **Step 4: Run the checks**

Expected: +3 tests pass; nothing else changes; validate ✔; tsc clean.

- [ ] **Step 5: Commit**

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
git -C $WT add mods/taskmaster-mods/hooks/register.tsx mods/taskmaster-mods/tests/sidebar.test.ts
git -C $WT commit -m "feat(taskmaster-mods): the Taskmaster sidebar over every pane — 1–4 jump, the current pane marked"
```

---

### Task C3: Live check with the user (Stage C)

Orchestrator task; no subagent.

- [ ] **Step 1: Smoke** — as Task A6 Step 1.
- [ ] **Step 2: Ask the user** (demo and real, 120+ columns, fullscreen): band `m` → Session with the sidebar over it; `1` → Review opens and Session closes (no tab row); Esc with the sidebar open closes only the sidebar; `/tm-review` then `/tm-handovers` leaves one pane. Inside a pane `m` is wired in Stage D (the footers); until then the sidebar is reached from the band.
- [ ] **Step 3: Record** — ledger line `Task C3: Stage C live check — <words>`. If Esc closed the pane, set `SIDEBAR_ESC = 'pane'` in a follow-up commit (hints switch to `m close`). Stop until the user signs off.

---

## Stage D: Session, Review and Handovers on the vocabulary

**Goal.** The three built panes look like their prototype artboards: the frame, row 1 (picker or Session's words, toggles, pager, `u`), the summary and bulk rows, 3-line items 3 a page with only the focused one boxed, labelled actions, one dim footer ending `m sidebar · esc close`. Their flows, data and tm calls do not change. The Session clipping bug is fixed (every row fits). The Task 4.4 close double-count is fixed where the close flow is touched (Task D3).

**How old tests move.** Element ids are kept wherever the element survives (card keys `done`, `back`, `open`, `viewer`, `copy`, `details`, `tick`, `tick-<i>`, `sel:<id>`, `confirm-yes/no`, `bulk-done`, `bulk-agent`, `sel-all`, `sel-none`, `note`, `note-cancel`, `bulk-note`, `bulk-cancel`, `page-prev`, `page-next`, `refresh`, `phase-prev`, `phase-next`, `phase-pick:<id>`, `phase-done`, `phase-all`; handovers' `ho:<id>`, `ho-sel:<id>`, `resume`, `new-tab`, `copy`, `summary`, `close`, `close-yes/no`, `close-selected`, `bulk-close-yes/no`, `select-all`, `select-none`, `show-closed`; `move-down`/`move-up` and `focus-next`/`focus-prev` become the footer's `j`/`k`). What changes is listed per task in a **migration table**: apply every row, then run the suite; a remaining failure in a listed file that no row covers means the old test pinned old presentation — rewrite that expectation to what spec §15 and this task's code draw, and name the test in the commit body. Never weaken a behavioural assertion (what a press does, what tm receives, what the store holds).

### Task D1: The Session tab on the vocabulary (and the clipping fix)

**Files:**
- Modify: `mods/taskmaster-mods/types/index.d.ts` (`TmSessionInfo`; PluginState `sessionInfo`)
- Modify: `mods/taskmaster-mods/hooks/vocab.tsx` (append `badgeSeg`)
- Modify: `mods/taskmaster-mods/hooks/band.tsx` (use vocab's `badgeSeg`, drop its local copy)
- Modify: `mods/taskmaster-mods/hooks/session.tsx` (whole file)
- Modify: `mods/taskmaster-mods/hooks/register.tsx` (`SESSION_INFO` atom written at `session.start`; the Session render)
- Modify: `mods/taskmaster-mods/tests/session-tab.test.ts` (whole file)

**Interfaces:**
- Consumes: vocab (`labelRow`, `underLabel`, `actionKey`, `actionRows`, `askRow`, `fieldSegs`, `footer`, `itemRule`, `line`, `paneFrame`, `paneToolbar`, `refreshPiece`, `Seg`, `Health`, `BADGES`, `badgeInk`, `statusBadgeWord`, `CONTENT_COL`); `TmFlows.goTo`, `toggleSidebar`, `retry`; `HANDOVER_TAG` (band.tsx).
- Produces: `types`: `TmSessionInfo = { readonly branch: string; readonly startedAt: number }`, PluginState `sessionInfo: TmSessionInfo | null`; `vocab.badgeSeg(k: Kit, word: string): Seg` (a badge as a line segment; a task status goes through `statusBadgeWord` first); `session.tsx`: `SessionView` (as before, minus `restore`, plus `info: TmSessionInfo | null`), `SessionHandlers` (`open` replaced by `goTo(pane)`; plus `refresh()` and `toggleSidebar()`), `sessionTree(k, v, on, width): RenderNode` (a `paneFrame`). Keys: `session-done` d (primary), `session-agent` a, `session-copy` c, `session-open` h, `session-launch` w, `session-summary` i, `session-restore` 4 (only while a burst is known), `session-yes`/`session-no`; footer `key:u`, `key:m`.

- [ ] **Step 1: Write the failing tests**

Replace the whole of `mods/taskmaster-mods/tests/session-tab.test.ts` with:

```ts
// User intent: the Session tab as the approved prototype draws it (spec §7, §15.7) — TASK, HANDOVER and SESSION in the label
// column, actions named for their subject under what they act on, one filled key (d mark task done), one dim footer — every
// key reaching the same flows as the band, and every row inside the pane (the clipping bug).
import { describe, expect, mock, test } from 'claude-code/testing'

import { DEMO_REASON } from '../hooks/demo'
import { resumePrompt, wtArgv } from '../hooks/launch'
import type { TmSnapshot } from '../types'
import { BAND, command, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { elementsOf, widthOf } from './fixtures/measure'
import * as R from './fixtures/replies'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const TM = { plugins: [RR_STUB] }
const TAB = { ...pane('tm-session'), props: { ...pane('tm-session').props, bodyColumns: 112 } }
const SNAP = `${PLUGIN}.snapshot`
const AUDIT = '2026-10-04-audit-fixes-landed'
const AUDIT_BLOCK = `Audit fixes landed on the native branch\nC:\\Users\\demo\\project\\.taskmaster\\handovers\\${AUDIT}.md\nResume: tm-audit — Record the merge for tm-audit-030`
const BOUND = { 'binding:sess-A': { taskId: 'tm-audit-030', at: 0 } }

// A demo-mode state no flow reaches: the bound task sits beyond the loaded window.
const FAR: TmSnapshot = {
  reachable: true,
  reason: DEMO_REASON,
  fetchedAt: 0,
  queue: [{ kind: 'task', id: 'docs-012', title: 'README', priority: 'low', humanAction: 'Read it', timestamp: '' }],
  queueTotal: 355,
  handovers: [],
  handoversTotal: 0,
  bound: {
    taskId: 'far-001',
    inferred: false,
    detail: { id: 'far-001', title: 'Bound task beyond the loaded window', status: 'in-review', priority: 'medium', lane: 'full', gateState: 'review-gate:pass', branch: '', humanAction: 'Check the far task' },
    pipeline: null,
  },
}

describe('session tab', () => {
  test('/tm-session and the band m open it', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect(await $.command.run(command('tm-session'))).toEqual({ text: 'Session opened.' })
    const band = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await band.press({ key: 'band-sidebar' })
    expect(world.opened).toEqual(['tm-session', 'tm-session'])
  })

  test('demo: the frame, the toolbar, TASK, HANDOVER and SESSION in the label column, on both surfaces', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB, surface })
      for (const text of [
        'SESSION',
        'demo',
        'this session',
        /^ · main · started \d+m ago$/,
        `  ${'TASK'.padEnd(17)}`,
        '● IN REVIEW',
        'unified-chat-022',
        'full · patch-1-9 · review-gate pass',
        'ready for done',
        'human  ',
        /^Live check on dev/,
        `  ${'HANDOVER'.padEnd(17)}`,
        '↳ RESUMED FROM',
        AUDIT,
        ' · 20h ago',
        'Audit fixes landed on the native branch',
        'next  ',
        'Record the merge for tm-audit-030',
        `  ${'SESSION'.padEnd(17)}`,
        'resumed from ',
      ]) {
        expect(await ui.find({ type: 'Text', text }), `${surface}: ${String(text)}`).toBeDefined()
      }
      await ui.unmount()
    }
  })

  test('every action names its subject; d is the one filled key; the footer holds u and m', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    for (const [key, hotkey, label] of [
      ['session-done', 'd', 'mark task done'],
      ['session-agent', 'a', 'send task to agent'],
      ['session-copy', 'c', 'copy handover'],
      ['session-open', 'h', 'show in handovers'],
      ['session-launch', 'w', 'resume in new tab'],
      ['session-summary', 'i', 'handover decisions·blockers ▸'],
      ['key:u', 'u', ''],
      ['key:m', 'm', ''],
    ] as const) {
      expect((await ui.find({ type: 'Button', key }))?.props, key).toMatchObject({ hotkey, plain: true, ...(label === '' ? {} : { label }) })
    }
    expect((await ui.find({ type: 'Box', key: 'session-done-box' }))?.props.backgroundColor).toBe('#8a9eeb')
    expect((await ui.find({ type: 'Box', key: 'session-copy-box' }))?.props.backgroundColor).toBeUndefined()
    expect(await ui.find({ type: 'Button', key: 'session-restore' })).toBeUndefined()
    expect(await ui.find({ type: 'Button', key: 'session-review' })).toBeUndefined()
  })

  test('every row fits the pane at 60 and 100 columns (no clipping at the right edge)', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const columns of [60, 100]) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB, props: { ...TAB.props, bodyColumns: columns } })
      const rows = elementsOf(await ui.drawn()).filter(e => e.type === 'Box' && e.props?.flexDirection === 'row' && e.props?.position !== 'absolute')
      for (const row of rows) expect(widthOf(row), `${columns}: ${JSON.stringify(row).slice(0, 120)}`).toBeLessThanOrEqual(columns - 4)
      await ui.unmount()
    }
  })

  test('d asks with y/n under the task and y signs it off, as the band does', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    await ui.press({ key: 'session-done' })
    expect(await ui.find({ type: 'Text', text: 'mark unified-chat-022 done?' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'session-no' }))?.props.autoFocus).toBe(true)
    await ui.press({ key: 'session-yes' })
    expect(await ui.find({ type: 'Text', text: 'No task bound to this session' })).toBeDefined()
  })

  test('a opens the review note on the bound task even when the loaded window does not hold it, and the note goes back on it', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    state.set(SNAP, FAR)
    const tab = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    await tab.press({ key: 'session-agent' })
    expect(world.opened).toEqual(['tm-review'])
    const review = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review'), surface: 'terminal' })
    expect(await review.find({ type: 'Text', text: 'far-001' })).toBeDefined()
    expect(await review.find({ text: 'Check the far task' })).toBeDefined()
    await review.input({ key: 'note', text: 'not yet' })
    expect(world.fills).toEqual(['Back to far-001: not yet'])
  })

  test('c copies the handover, h shows it in handovers, w says what demo would open, i shows decisions and blockers', DEMO, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    await ui.press({ key: 'session-copy' })
    await ui.press({ key: 'session-open' })
    await ui.press({ key: 'session-launch' })
    await ui.press({ key: 'session-summary' })
    await clock.settle()
    expect(world.copies).toEqual([AUDIT_BLOCK])
    expect(world.opened).toEqual(['tm-handovers'])
    expect(world.toasts).toEqual([`Copied handover ${AUDIT}`, `Demo: would open handover ${AUDIT}`])
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: '· Audit fixes ride the native branch; no backport to master' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: '· tm-audit-030 merge not recorded yet' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'session-summary' }))?.props.label).toBe('handover decisions·blockers ▾')
  })

  test('tm: w opens a Windows Terminal tab whose first prompt resumes from the handover', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock, BOUND)
    world.mcp = R.backlog
    world.env = { OS: 'Windows_NT' }
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    await ui.press({ key: 'session-launch' })
    await clock.settle()
    const path = `C:\\work\\proj\\.taskmaster\\handovers\\${AUDIT}.md`
    const spec = { cwd: 'C:\\work\\proj', title: `handover ${AUDIT}`, mode: 'fresh' as const, prompt: resumePrompt(AUDIT, path) }
    expect(world.runs.filter(argv => argv[0] === 'wt.exe')).toEqual([wtArgv(spec, undefined)])
    expect(world.toasts).toEqual(['Opened 1 tab'])
  })

  test('offline: the health says offline, d and a are disabled and say why, the empty sections say so', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock, BOUND)
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    expect(await ui.find({ type: 'Text', text: 'offline' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'tm-audit-030' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'No handover for this session yet' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'not resumed from a handover' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'session-done' }))?.props.dimColor).toBe(true)
    await ui.press({ key: 'session-done' })
    await ui.press({ key: 'session-agent' })
    expect(world.toasts).toEqual(['Taskmaster is offline: try again once it answers', 'Taskmaster is offline: try again once it answers'])
    expect(world.opened).toEqual([])
  })

  test('a task only inferred from the branch: d and a are disabled and name it; nothing bound says so', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    world.mcp = R.backlog
    world.branch = 'feat/tm-audit-030-fixes'
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    expect(await ui.find({ type: 'Text', text: /inferred from the branch/ })).toBeDefined()
    await ui.press({ key: 'session-done' })
    expect(world.toasts).toEqual(['tm-audit-030 is only inferred from the branch: pick it to bind it to this session'])
    world.branch = 'main'
    await $.turn.complete({ answer: '', reason: 'end_turn' } as never)
    await clock.settle()
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: 'No task bound to this session' })).toBeDefined()
  })

  test('a known burst adds its line and 4 show burst in restore, which goes to the restore pane', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock, BOUND)
    world.mcp = R.backlog
    stateOf(on, { [`${PLUGIN}.sessionBurst`]: { at: '16:14', others: 3 } })
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    expect(await ui.find({ type: 'Text', text: ' · part of the 16:14 burst · 3 others open' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'session-restore' }))?.props).toMatchObject({ hotkey: '4', label: 'show burst in restore' })
    await ui.press({ key: 'session-restore' })
    expect(world.opened).toEqual(['tm-restore'])
  })

  test('u asks tm again; m opens the sidebar over the tab', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...TAB })
    await ui.press({ key: 'key:u' })
    expect(world.toasts).toEqual(['taskmaster-mods: demo data has nothing newer to read'])
    await ui.press({ key: 'key:m' })
    expect(state.get(`${PLUGIN}.nav`)).toMatchObject({ sidebar: true })
    await ui.redraw()
    expect(await ui.find({ type: 'Button', key: 'nav:session' })).toBeDefined()
  })
})
```

- [ ] **Step 2: Run the suite to see the new tests fail**

Expected: most of `tests/session-tab.test.ts` fails (old labels and layout; no footer keys; no `sessionInfo`).

- [ ] **Step 3: Implement**

In `mods/taskmaster-mods/types/index.d.ts`, replace:

```ts
/** The Taskmaster sidebar (spec §15.8): whether it is open over the pane, and the pane `m` on the band reopens. */
```

with:

```ts
/** This session's branch and when it started, read once at `session.start`: the Session tab's toolbar words. */
export type TmSessionInfo = { readonly branch: string; readonly startedAt: number }
/** The Taskmaster sidebar (spec §15.8): whether it is open over the pane, and the pane `m` on the band reopens. */
```

and replace:

```ts
      nav: TmNav
```

with:

```ts
      nav: TmNav
      /** This session's branch and start time (null until session.start ran in this instance). */
      sessionInfo: TmSessionInfo | null
```

Append to `mods/taskmaster-mods/hooks/vocab.tsx`:

```tsx

/** A badge as a line segment (`● IN REVIEW` in its ink): for rows built from segments. Pass a task status through statusBadgeWord. */
export function badgeSeg(k: Kit, word: string): Seg {
  const upper = word.toUpperCase()
  const spec = BADGES[upper] ?? UNKNOWN_BADGE
  return { text: `${spec.glyph} ${upper}`, color: badgeInk(k, upper), bold: spec.bold }
}
```

In `mods/taskmaster-mods/hooks/band.tsx`, replace:

```tsx
import { actionKey, BADGES, badgeInk, line, type Seg, statusBadgeWord } from './vocab'
```

with:

```tsx
import { actionKey, badgeSeg, line, type Seg, statusBadgeWord } from './vocab'
```

delete the local function:

```tsx
function badgeSeg(k: Kit, status: string): Seg {
  const word = statusBadgeWord(status)
  const spec = BADGES[word]
  return { text: `${spec?.glyph ?? '·'} ${word}`, color: badgeInk(k, word), bold: spec?.bold ?? false }
}
```

and replace `badgeSeg(k, task.status)` with `badgeSeg(k, statusBadgeWord(task.status))`.

Replace the whole of `mods/taskmaster-mods/hooks/session.tsx` with:

```tsx
// User intent: the Session tab (spec §7, §15.7; band `m`, /tm-session) — this session's task, its handover and where it came
// from in the round-1 label column, each action named for its subject and drawn under what it acts on, one filled key, one
// dim footer — and every row inside the pane, so nothing is clipped at the right edge.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode, RenderSurface } from 'claude-code'

import type { TmBandMode, TmHandoverSummary, TmSessionBurst, TmSessionHandover, TmSessionInfo, TmSnapshot } from '../types'
import { HANDOVER_TAG } from './band'
import type { TmPane } from './flows'
import type { Kit } from './kit'
import { ageLabel, gateSignal, isReady, oneLine, wrapText } from './model'
import {
  actionKey,
  actionRows,
  askRow,
  badgeSeg,
  CONTENT_COL,
  fieldSegs,
  footer,
  type Health,
  itemRule,
  labelRow,
  line,
  paneFrame,
  paneToolbar,
  refreshPiece,
  type Seg,
  statusBadgeWord,
  underLabel,
} from './vocab'

export type SessionView = {
  readonly snapshot: TmSnapshot | null
  /** D5, with a newer notice already let in (withNotice). */
  readonly handover: TmSessionHandover | null
  readonly resumedFrom: string
  readonly burst: TmSessionBurst | null
  readonly band: TmBandMode
  /** The handover's decisions and blockers when read; shown while `summaryOpen`. */
  readonly summary: TmHandoverSummary | undefined
  readonly summaryOpen: boolean
  readonly connecting: boolean
  readonly demo: boolean
  readonly now: number
  /** This session's branch and start (null: not read yet): the toolbar's words. */
  readonly info: TmSessionInfo | null
}

export type SessionHandlers = {
  readonly goTo: (pane: TmPane) => void
  readonly askDone: (taskId: string) => void
  readonly confirmDone: (taskId: string) => void
  readonly cancel: () => void
  readonly sendBack: (taskId: string) => void
  readonly copy: (handoverId: string, surface: RenderSurface) => void
  readonly openHandover: (handoverId: string) => void
  readonly launch: (handover: TmSessionHandover, surface: RenderSurface) => void
  readonly toggleSummary: (handoverId: string, open: boolean) => void
  readonly refresh: () => void
  readonly toggleSidebar: () => void
}

/** Why `d` (and `a`) cannot act on the bound task, or '' when they can. */
function whyNot(snapshot: TmSnapshot, forDone: boolean): string {
  const b = snapshot.bound
  if (b === null) return 'No task bound to this session'
  if (!snapshot.reachable || b.detail === null) return 'Taskmaster is offline: try again once it answers'
  if (b.inferred) return `${b.taskId} is only inferred from the branch: pick it to bind it to this session`
  if (!forDone || isReady(b.detail, b.pipeline)) return ''
  const p = b.pipeline
  return p !== null && !p.laneless && p.outstanding.length > 0 ? `Not ready for done: ${p.outstanding[0]} is outstanding` : 'Not ready for done yet'
}

/** `full · patch-1-9 · review-gate pass ✓ · ready for done`: lane, phase and gate subtle, the pass and ready in success ink. */
function metaSegs(k: Kit, s: TmSnapshot): Seg[] {
  const { t } = k
  const b = s.bound
  const d = b?.detail ?? null
  if (b === null || d === null) return []
  const gate = gateSignal(d.gateState)
  const p = b.pipeline
  const segs: Seg[] = [{ text: [d.lane || 'no lane', d.phase ?? '', gate === null ? '' : gate.word].filter(Boolean).join(' · '), color: t.fg.subtle }]
  if (gate?.kind === 'success') segs.push({ text: ' ✓', color: t.tone.success })
  const stage = p === null ? '' : p.laneless ? 'no pipeline' : p.outstanding.length === 0 ? 'ready for done' : `next: ${p.outstanding[0]}`
  if (stage !== '') segs.push({ text: ' · ', color: t.fg.subtle }, { text: stage, color: stage === 'ready for done' ? t.tone.success : t.fg.subtle })
  if (b.inferred) segs.push({ text: ' · inferred from the branch', color: t.fg.subtle })
  return segs
}

const blank = (k: Kit): RenderNode => {
  const { Text } = k.ui
  return <Text> </Text>
}

function taskSection(k: Kit, v: SessionView, on: SessionHandlers, inner: number): RenderNode[] {
  const { t } = k
  const room = inner - CONTENT_COL
  const s = v.snapshot
  const b = s?.bound ?? null
  if (s === null || b === null) return [labelRow(k, 'TASK', [{ text: 'No task bound to this session', color: t.fg.disabled }], inner)]
  const d = b.detail
  const title = d === null ? '' : oneLine(d.title)
  const rows: RenderNode[] = [
    labelRow(
      k,
      'TASK',
      [
        ...(s.reachable && d !== null ? [badgeSeg(k, statusBadgeWord(d.status)), { text: '  ', color: t.fg.default }] : []),
        { text: b.taskId, color: t.fg.bold, bold: true },
        ...(title === '' ? [] : [{ text: `  ${title}`, color: b.inferred ? t.fg.subtle : t.fg.default }]),
      ],
      inner,
    ),
  ]
  const meta = metaSegs(k, s)
  if (meta.length > 0) rows.push(labelRow(k, '', meta, inner))
  const human = d === null ? '' : oneLine(d.humanAction)
  if (human !== '') rows.push(labelRow(k, '', fieldSegs(k, 'human', human), inner))
  rows.push(blank(k))
  if (v.band.confirmingId === b.taskId) {
    rows.push(underLabel(k, askRow(k, { id: 'session', question: `mark ${b.taskId} done?`, onYes: () => on.confirmDone(b.taskId), onNo: on.cancel, width: room })))
    return rows
  }
  const noDone = whyNot(s, true)
  const noAgent = whyNot(s, false)
  rows.push(
    underLabel(
      k,
      actionRows(
        k,
        [
          actionKey(k, { id: 'session-done', key: 'd', label: 'mark task done', rung: 'primary', onPress: () => on.askDone(b.taskId), ...(noDone === '' ? {} : { dim: noDone }) }),
          actionKey(k, { id: 'session-agent', key: 'a', label: 'send task to agent', onPress: () => on.sendBack(b.taskId), ...(noAgent === '' ? {} : { dim: noAgent }) }),
        ],
        room,
      ),
    ),
  )
  if (v.band.refusal !== '' && v.band.refusalId === b.taskId) {
    const words = wrapText(oneLine(v.band.refusal), Math.max(10, room - 11))
    words.forEach((text, i) =>
      rows.push(labelRow(k, '', [{ text: i === 0 ? '▲ REFUSED  ' : '           ', color: t.tone.critical, bold: true }, { text, color: t.fg.default }], inner)),
    )
  }
  return rows
}

function handoverSection(k: Kit, v: SessionView, on: SessionHandlers, inner: number): RenderNode[] {
  const { t } = k
  const room = inner - CONTENT_COL
  const ho = v.handover
  if (ho === null) return [labelRow(k, 'HANDOVER', [{ text: 'No handover for this session yet', color: t.fg.disabled }], inner)]
  const age = ageLabel(ho.created, v.now)
  const rows: RenderNode[] = [
    labelRow(
      k,
      'HANDOVER',
      [
        badgeSeg(k, HANDOVER_TAG[ho.tag]),
        { text: '  ', color: t.fg.default },
        { text: ho.id, color: t.fg.default },
        { text: age === '' ? '' : ` · ${age} ago`, color: t.fg.subtle },
      ],
      inner,
    ),
    labelRow(k, '', [{ text: oneLine(ho.tldr) || ho.id, color: t.fg.bold, bold: true }], inner),
  ]
  if (ho.next !== undefined && ho.next !== '') rows.push(labelRow(k, '', fieldSegs(k, 'next', oneLine(ho.next)), inner))
  rows.push(blank(k))
  rows.push(
    underLabel(
      k,
      actionRows(
        k,
        [
          actionKey(k, { id: 'session-copy', key: 'c', label: 'copy handover', onPress: press => on.copy(ho.id, press.surface) }),
          actionKey(k, { id: 'session-open', key: 'h', label: 'show in handovers', onPress: () => on.openHandover(ho.id) }),
          actionKey(k, { id: 'session-launch', key: 'w', label: 'resume in new tab', onPress: press => on.launch(ho, press.surface) }),
          actionKey(k, { id: 'session-summary', key: 'i', label: `handover decisions·blockers ${v.summaryOpen ? '▾' : '▸'}`, onPress: () => on.toggleSummary(ho.id, !v.summaryOpen) }),
        ],
        room,
      ),
    ),
  )
  if (v.summaryOpen) {
    const sum = v.summary
    if (sum === undefined) rows.push(labelRow(k, '', [{ text: 'loading decisions and blockers…', color: t.fg.subtle }], inner))
    else if (sum.unavailable === true) rows.push(labelRow(k, '', [{ text: 'Decisions and blockers unavailable: i asks again', color: t.fg.subtle }], inner))
    else {
      for (const [word, items] of [['decisions', sum.decisions], ['blockers', sum.blockers]] as const) {
        rows.push(labelRow(k, '', [{ text: word, color: t.signatureText }], inner))
        if (items.length === 0) rows.push(labelRow(k, '', [{ text: 'none', color: t.fg.subtle }], inner))
        for (const entry of items) rows.push(labelRow(k, '', [{ text: `· ${oneLine(entry)}`, color: t.fg.default }], inner))
      }
    }
  }
  return rows
}

function sessionSection(k: Kit, v: SessionView, on: SessionHandlers, inner: number): RenderNode[] {
  const { t } = k
  const where: Seg[] =
    v.resumedFrom === ''
      ? [{ text: 'not resumed from a handover', color: t.fg.disabled }]
      : [
          { text: 'resumed from ', color: t.fg.subtle },
          { text: v.resumedFrom, color: t.fg.default },
        ]
  if (v.burst !== null) where.push({ text: ` · part of the ${v.burst.at} burst · ${v.burst.others} others open`, color: t.fg.subtle })
  const rows: RenderNode[] = [labelRow(k, 'SESSION', where, inner)]
  if (v.burst !== null) {
    rows.push(blank(k))
    rows.push(underLabel(k, actionRows(k, [actionKey(k, { id: 'session-restore', key: '4', label: 'show burst in restore', onPress: () => on.goTo('restore') })], inner - CONTENT_COL)))
  }
  return rows
}

function healthOf(v: SessionView): Health {
  const s = v.snapshot
  if (v.connecting && (s === null || !s.reachable)) return { kind: 'info', word: 'connecting' }
  if (s === null) return { kind: 'info', word: 'loading' }
  if (!s.reachable) return { kind: 'critical', word: 'offline' }
  return v.demo ? { kind: 'info', word: 'demo' } : { kind: 'success', word: 'tm ok' }
}

/** The Session tab (spec §15.7): the frame, `this session · <branch> · started <age> ago`, TASK / HANDOVER / SESSION, the footer. */
export function sessionTree(k: Kit, v: SessionView, on: SessionHandlers, width: number): RenderNode {
  const { t } = k
  const inner = Math.max(CONTENT_COL + 12, width - 4)
  const started = v.info === null ? '' : ageLabel(new Date(v.info.startedAt).toISOString(), v.now)
  const words = line(
    k,
    [
      { text: 'this session', color: t.fg.subtle },
      { text: v.info === null ? '' : [` · ${v.info.branch || 'no branch'}`, started === '' ? '' : ` · started ${started} ago`].join(''), color: t.fg.disabled },
    ],
    Math.max(12, inner - 14),
  )
  const top = paneToolbar(k, { left: words, right: [refreshPiece(k, { label: 'refresh', onPress: on.refresh })], width: inner })
  const keys = footer(
    k,
    [
      { keys: 'u', word: 'refresh', press: [{ key: 'u', onPress: on.refresh }] },
      { keys: 'm', word: 'sidebar', press: [{ key: 'm', onPress: on.toggleSidebar }] },
      { keys: 'esc', word: 'close' },
    ],
    inner,
  )
  return paneFrame(k, { name: 'session', health: healthOf(v), width }, [
    top,
    blank(k),
    ...taskSection(k, v, on, inner),
    itemRule(k, inner),
    ...handoverSection(k, v, on, inner),
    itemRule(k, inner),
    ...sessionSection(k, v, on, inner),
    blank(k),
    keys,
  ])
}
```

In `mods/taskmaster-mods/hooks/register.tsx`:

1. Add `TmSessionInfo` to the `../types` import list.
2. After `const NAV = atom(…)`, add:

```tsx
const SESSION_INFO = atom({ plugin: 'taskmaster-mods', key: 'sessionInfo' } as const, null as TmSessionInfo | null)
```

3. In the `session.start` hook, replace:

```tsx
    await ready($)
    await pruneTicks($)
```

with:

```tsx
    await ready($)
    const branch = (await mod.host?.branch()) ?? ''
    const startedAt = await $.clock.now()
    await update($, SESSION_INFO, () => ({ branch, startedAt }))
    await pruneTicks($)
```

4. In the `SESSION` pane render, replace the `sessionTree(` call's view and handlers:

```tsx
      {
        snapshot,
        handover,
        resumedFrom: await read($, RESUMED_FROM),
        burst: await read($, SESSION_BURST),
        restore: pending?.unfinished ?? null,
        band: await read($, BAND),
        summary: held,
        summaryOpen: open,
        connecting: mod.source === 'tm' && (await read($, FAULT)) === 'connecting',
        demo: mod.source === 'demo',
        now: await $.clock.now(),
      },
      {
        open: pane => act(f => f.openPane(pane)),
```

with:

```tsx
      {
        snapshot,
        handover,
        resumedFrom: await read($, RESUMED_FROM),
        burst: await read($, SESSION_BURST),
        band: await read($, BAND),
        summary: held,
        summaryOpen: open,
        connecting: mod.source === 'tm' && (await read($, FAULT)) === 'connecting',
        demo: mod.source === 'demo',
        now: await $.clock.now(),
        info: await read($, SESSION_INFO),
      },
      {
        goTo: pane => act(f => f.goTo(pane)),
        refresh: () => act(f => f.retry()),
        toggleSidebar: () => act(f => f.toggleSidebar()),
```

and delete the now-unused line `const pending = await restoreShown($)` in that render. The render keeps its page-ground Box and its `withSidebar` wrapper (Task C2).

- [ ] **Step 4: Run the checks**

Expected: the 12 Session tests pass; `tests/band.test.ts` still passes (the badge segment moved, not changed); validate ✔ (`sessionInfo` declared); tsc clean.

- [ ] **Step 5: Commit**

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
git -C $WT add mods/taskmaster-mods/types/index.d.ts mods/taskmaster-mods/hooks/vocab.tsx mods/taskmaster-mods/hooks/band.tsx mods/taskmaster-mods/hooks/session.tsx mods/taskmaster-mods/hooks/register.tsx mods/taskmaster-mods/tests/session-tab.test.ts
git -C $WT commit -m "feat(taskmaster-mods): the Session tab on the rework vocabulary — label column, labelled actions, one filled key; no clipping"
```

---

### Task D2: The review pane on the vocabulary

**Files:**
- Modify: `mods/taskmaster-mods/hooks/review.tsx` (whole file)
- Modify: `mods/taskmaster-mods/hooks/review-list.ts` (`LIST_PAGE_SIZE`)
- Modify: `mods/taskmaster-mods/hooks/register.tsx` (the review render passes `demo` and `toggleSidebar`)
- Create: `mods/taskmaster-mods/tests/review-look.test.ts`
- Modify (migration table): `tests/review.test.ts`, `tests/card.test.ts`, `tests/tick.test.ts`, `tests/bulk.test.ts`, `tests/phase.test.ts`, `tests/review-list.test.ts`, `tests/live.test.ts`, `tests/actions.test.ts`, `tests/refresh.test.ts`, `tests/retry.test.ts`, `tests/reload.test.ts`

**Interfaces:**
- Consumes: vocab (picker, menu, footer, item, frame, toolbar pieces, summary, bulk, empty, offline, `askRow`, `actionKey`, `actionRows`, `fieldSegs`, `itemLine`, `line`, `CONTENT_COL`); review-list as before; `LIST_PAGE_SIZE` (model.ts); `TmFlows.toggleSidebar`.
- Produces: `reviewPaneTree(ui: Ui, rr: Rr, v: ReviewView, on: ReviewHandlers, width: number): Promise<RenderNode>` (a `paneFrame`); `ReviewView` gains `demo?: boolean`; `ReviewHandlers` gains `toggleSidebar: () => void`; `review-list.ts` pages by `LIST_PAGE_SIZE` (3). Footer Buttons: `focus-next` (j), `focus-prev` (k), `key:s`, `key:h`, `key:l`, `key:f`, `key:p`, `key:n` (absent while a y/n is up), `key:u`, `key:m`.

- [ ] **Step 1: Write the failing tests**

`mods/taskmaster-mods/tests/review-look.test.ts`:

```ts
// User intent: the review pane as the approved prototype draws it (spec §15.6–15.7, Review.dc.html) — the frame, the picker
// row, one summary sentence with its tallies, the bulk row with counted labels, 3 items a page with only the focused one boxed
// and one filled key, a dim footer that owns the navigation keys, no shared hotkey, and every row inside the pane.
import { describe, expect, mock, test } from 'claude-code/testing'

import { pane, PLUGIN, SESSION } from './fixtures/inputs'
import { elementsOf, widthOf } from './fixtures/measure'
import { RR_STUB } from './fixtures/rr-stub'
import { worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const WIDE = { ...pane('tm-review'), props: { ...pane('tm-review').props, bodyColumns: 112 } }
type Ui = { findAll: (q: { type: 'Button' }) => Promise<{ props: Record<string, unknown> }[]> }
const hotkeys = async (ui: Ui): Promise<Record<string, string[]>> => {
  const keys: Record<string, string[]> = {}
  for (const b of await ui.findAll({ type: 'Button' })) if (typeof b.props.hotkey === 'string') (keys[b.props.hotkey] ??= []).push(String(b.props.key))
  return keys
}

describe('the review pane, as drawn', () => {
  test('the frame names REVIEW; row 1 is the picker, the pager and u refresh; row 2 the sentence and tallies', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    for (const text of ['REVIEW', 'demo', ' All  5 ▾ ', ' 1 / 2 ', '5 waiting on you · newest 1h ago', '▲ BLOCKED', ' 1', '◆ ACTION', ' 4', '● IN REVIEW', ' 0']) {
      expect(await ui.find({ type: 'Text', text }), text).toBeDefined()
    }
    expect((await ui.find({ type: 'Button', key: 'refresh' }))?.props.label).toBe('u refresh')
  })

  test('3 items a page; only the focused one is boxed, its first action the one filled key; collapsed items carry no keys', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    const marks = (await ui.findAll({ type: 'Button' })).filter(b => String(b.props.key).startsWith('sel:'))
    expect(marks.map(b => b.props.key)).toEqual(['sel:unified-chat-022', 'sel:tm-audit-031', 'sel:docs-012'])
    const boxes = elementsOf(await ui.drawn()).filter(e => e.type === 'Box' && e.props?.borderStyle === 'round' && String(e.props?.key ?? '').startsWith('card:'))
    expect(boxes.map(b => b.props?.key)).toEqual(['card:unified-chat-022'])
    for (const [key, hotkey, label] of [
      ['done', 'd', 'mark task done'],
      ['back', 'a', 'send task to agent'],
      ['open', 'o', 'open task'],
      ['viewer', 'v', 'task in viewer'],
      ['details', 'i', 'task detail ▸'],
      ['tick', 't', 'tick check 1-9'],
      ['copy', 'c', 'copy live check'],
      ['bulk-done', 'b', 'mark 0 tasks done'],
      ['bulk-agent', 'g', 'send 0 tasks to agent'],
    ] as const) {
      expect((await ui.find({ type: 'Button', key }))?.props, key).toMatchObject({ hotkey, label })
    }
    const filled = elementsOf(await ui.drawn()).filter(e => e.type === 'Box' && e.props?.backgroundColor === '#8a9eeb')
    expect(filled.map(e => e.props?.key)).toEqual(['done-box'])
    expect(await ui.find({ type: 'Text', text: 'human  ' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^1 {2}$/ })).toBeDefined()
  })

  test('the footer owns j k s h l f p n u m; no hotkey is on two Buttons; the sel marks carry none', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    const keys = await hotkeys(ui)
    for (const [hotkey, id] of [['j', 'focus-next'], ['k', 'focus-prev'], ['s', 'key:s'], ['h', 'key:h'], ['l', 'key:l'], ['f', 'key:f'], ['p', 'key:p'], ['n', 'key:n'], ['u', 'key:u'], ['m', 'key:m']] as const) {
      expect(keys[hotkey], hotkey).toEqual([id])
    }
    expect(Object.entries(keys).filter(([, ids]) => ids.length > 1)).toEqual([])
    expect(keys['0']).toBeUndefined()
    for (const text of ['j/k', ' move', 's', ' select', 'm', ' sidebar', 'esc', ' close']) expect(await ui.find({ type: 'Text', text }), text).toBeDefined()
  })

  test('while a y/n is up the footer drops p/n, so only the y/n carries n', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: 'done' })
    expect((await hotkeys(ui)).n).toEqual(['confirm-no'])
    await ui.press({ key: 'confirm-no' })
    await ui.press({ key: 'sel:unified-chat-022' })
    await ui.press({ key: 'bulk-done' })
    expect((await hotkeys(ui)).n).toEqual(['bulk-no'])
  })

  test('f opens the dropdown over the list: digits pick only while it is open; s selects the focused task', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE })
    await ui.press({ key: 'key:f' })
    await ui.redraw()
    expect((await hotkeys(ui))['0']).toEqual(['phase-pick:'])
    await ui.press({ key: 'phase-pick:patch-1-9' })
    await ui.redraw()
    expect((await ui.find({ type: 'Button', key: 'phase-field' }))?.props.label).toBe(' Patch 1.9 · active  3 ▾ ')
    await ui.press({ key: 'key:s' })
    expect((await ui.find({ type: 'Button', key: 'sel:unified-chat-022' }))?.props.label).toBe('■')
  })

  test('every row fits the pane at 60 and 100 columns', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const columns of [60, 100]) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...WIDE, props: { ...WIDE.props, bodyColumns: columns } })
      const rows = elementsOf(await ui.drawn()).filter(e => e.type === 'Box' && e.props?.flexDirection === 'row' && e.props?.position !== 'absolute')
      for (const row of rows) expect(widthOf(row), `${columns}: ${JSON.stringify(row).slice(0, 120)}`).toBeLessThanOrEqual(columns - 4)
      await ui.unmount()
    }
  })
})
```

**Migration table** (apply to the files listed in **Files**; "→" is the replacement):

| Old | New |
|-----|-----|
| press `{ key: 'phase-more' }` or `{ key: 'phase-close' }` | press `{ key: 'phase-field' }` |
| `find({ type: 'Button', key: 'phase-more' })` … `hotkey: 'f'` | `find({ type: 'Button', key: 'key:f' })` … `hotkey: 'f'` |
| press `{ key: 'phase-tab:<id>' }` | press `{ key: 'phase-field' }`, then `{ key: 'phase-pick:<id>' }` |
| `phase-tab:` label `All N` / `phase-tab:<id>` label `<name> N` | `phase-field` label `` ` All  N ▾ ` `` / `` ` <name>  N ▾ ` `` (the chosen one); other phases: open the list and read `phase-pick:<id>`'s label, which matches `/^<name> +N$/` (`<name>` is `pickerName`: `Patch 1.9 · active`, `Patch 1.8 ✓`) |
| `phase-tab:<id>-box` `backgroundColor` (chosen) | `phase-field`'s label names the chosen phase |
| `phase-pick:<id>` label `'<name> · <status> · <count>…'` | `/^<pickerName> +<count>$/` |
| `phase-done` label `'◐ done'` / `'○ done'` (and pressing it with the list closed) | press `phase-field` first; `phase-done` label `'◐ show done phases'`; its state is the `Text` `'on'` / `'off'` beside it |
| `OVERLAY_PLACEMENT` (from `../hooks/kit`) | `MENU_PLACEMENT` (from `../hooks/vocab`); `'absolute'` keeps the list drawn under the dropdown |
| digits while unarmed and the list shut: `keys['0']` `['phase-tab:']`, `keys['1']` `['phase-tab:patch-1-9']` | no Button carries a digit (`undefined`) |
| `` `f` is on both `+N ▾` and the overlay's close `` (`shared(keys)` `['f']`) | `shared(keys)` `[]` |
| `focus-prev` / `focus-next` `dimColor: true` at an end | the footer key never dims: its press toasts `Already on the first card` / `Already on the last card` |
| `focus-next` / `focus-prev` `label: '↓'` / `'↑'` | `label: ''` (footer key) |
| `sel:<id>` `hotkey: 's'` | `key:s` `hotkey: 's'`; `sel:<id>` has no hotkey |
| `page-prev` / `page-next` `hotkey: 'p'` / `'n'` | `key:p` / `key:n`; `page-prev` / `page-next` keep `dimColor` at the ends and have no hotkey |
| `refresh` `{ hotkey: 'u', label: 'refresh' }` | `refresh` `{ label: 'u refresh' }` (no hotkey); `key:u` `{ hotkey: 'u' }` |
| offline `refresh` `{ hotkey: 'u', label: 'retry' }`; Text `' Taskmaster unreachable'` | `refresh` `{ hotkey: 'u', label: 'retry tm connection' }`; Text `'▲ tm offline'` |
| Text `'REVIEW'` (toolbar) | Text `'REVIEW'` (frame title): unchanged |
| Text `'1 / 1'`, `'2 / 3'` | `' 1 / 1 '`, `' 2 / 3 '` (and page counts follow 3 a page: 12 items → `/ 4`) |
| "five a page" (`TWELVE` → 3 pages; `t-006` on page 2) | three a page: 12 items → 4 pages; page 2 starts at `t-004`; `n` from page 1 focuses `t-004` |
| kind badges `' 2 BLOCKED '` row / card `' ACTION    '` / narrow `' A '` | tallies `'▲ BLOCKED'` + `' 2'`; card badge `'◆ ACTION       '` (padded to 15); a narrow card keeps the full badge and cuts the title |
| lane line `'FULL'` / gate signal | focused context `'patch-1-9 · lane full · '`, `'● '`, `'review-gate pass'` |
| check label row (`'LIVE CHECK ON DEV'` etc.) | `'human  '` + `'Live check on dev — (needs unifiedChatGenerate + unifiedChatBuild granted)'` |
| `details` label `'▸ more'` / `'▾ more'` | `'task detail ▸'` / `'task detail ▾'` |
| card labels `done`, `agent`, `open`, `viewer`, `copy check`, `tick` | `mark task done`, `send task to agent`, `open task`, `task in viewer`, `copy live check`, `tick check 1-9` |
| bulk labels `'Done N'`, `'To agent N'`; dim toast `'Nothing selected to done'` | `'mark N tasks done'` (`task` when 1), `'send N tasks to agent'`; toasts `'Nothing selected to mark done'`, `'Nothing selected to send to the agent'` |
| bulk question Text `' Done 2: a, b? '` | `'mark 2 tasks done: a, b?'` |
| confirm Text `' 2 of 2 unchecked — done anyway? '`, `' done <id>? '` (padded badge) | `'2 of 2 unchecked — done anyway?'`, `'done <id>?'` (no padding) |
| `' REFUSED '` | `'▲ REFUSED  '` |
| `' Queue clear'` + `'  0 done this pass'` | `'Queue clear · 0 done this pass'` |
| empty phase: `phase-all` `{ hotkey: '0', label: 'All' }` | `phase-all` `{ hotkey: '0', label: 'show all phases' }` |
| footer Text `/t\+1-9 tick/`, `/1-9 ticks a check · t or f cancels/` | `'t 1-9'` + `' tick'`; while armed `'1-9'` + `' tick a check'` |
| `review-list.test.ts` pages of 5 (`listView`, `neighbour` page numbers) | pages of 3 (`LIST_PAGE_SIZE`): an index `i` is on page `Math.floor(i / 3)` |
| issue card `open` `{ label: 'open in prompt' }` | unchanged label, now the primary (filled) key |

- [ ] **Step 2: Run the suite to see the new tests fail**

Expected: `tests/review-look.test.ts` fails, and so do the migrated expectations (the code still draws the old pane).

- [ ] **Step 3: Implement**

In `mods/taskmaster-mods/hooks/review-list.ts`, replace:

```ts
import { PAGE_SIZE, reviewQueue, truncate } from './model'
```

with:

```ts
import { LIST_PAGE_SIZE as PAGE_SIZE, reviewQueue, truncate } from './model'
```

Replace the whole of `mods/taskmaster-mods/hooks/review.tsx` with:

```tsx
// User intent: the review pane in the rework's vocabulary (spec §8, §15.6–15.7, Review.dc.html) — the phase picker, what waits
// on the person as one sentence and its tallies, a bulk row whose labels carry the count, three 3-line items a page with only
// the focused one boxed with its labelled actions (one filled), and one dim footer that owns the navigation keys.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode, RenderSurface } from 'claude-code'

import type { TmCursor, TmQueueItem, TmReviewList, TmSnapshot, TmTaskDetail } from '../types'
import { type Kit, kitOf, type StripHandlers, type StripView, type Ui } from './kit'
import { ageLabel, cardMode, CONNECTING, gateSignal, oneLine, splitCheck, type TmCheck, truncate, wrapText } from './model'
import { ALL, emptyLine, orderPhases } from './phases'
import {
  doneQuestion,
  isSelectable,
  KIND_WORD,
  KINDS,
  kindCounts,
  listView,
  neighbour,
  newestStamp,
  reviewItems,
  reviewKind,
  selectedTasks,
} from './review-list'
import type { Rr } from './rr'
import {
  actionKey,
  actionRows,
  askRow,
  bulkRow,
  CONTENT_COL,
  emptyRow,
  fieldSegs,
  focusedItem,
  footer,
  type FooterItem,
  type HeadSpec,
  type Health,
  item,
  itemLine,
  line,
  type ListEntry,
  listing,
  offlineRow,
  pagerPiece,
  paneFrame,
  paneToolbar,
  phaseFooter,
  phasePicker,
  refreshPiece,
  type Seg,
  summaryRow,
  withPhaseMenu,
} from './vocab'

export type ReviewView = {
  snapshot: TmSnapshot | null
  cursor: TmCursor
  list: TmReviewList
  details: Readonly<Record<string, TmTaskDetail>>
  now: number
  /** The ticked check items of a task (local UI state, never Taskmaster's). */
  ticks: (taskId: string) => Promise<readonly string[]>
  detailsOpen: boolean
  /** The phase picker (spec §15.4): this pane's phase view and counts. */
  strip: StripView
  surface: RenderSurface
  /** Toasts a line: a disabled key's why, a footer key at an end. */
  say: (text: string) => void
  /** tm is being retried after a transient failure: with no good snapshot the pane says "Connecting…". */
  connecting?: boolean
  /** Demo data: the frame's health says demo. */
  demo?: boolean
}

export type ReviewHandlers = {
  askDone: (id: string) => void
  confirmDone: (id: string) => void
  cancel: () => void
  askNote: (id: string) => void
  /** `humanAction`: the check the card shows, so the clear can tell an already-empty one from a failed clear. */
  sendBack: (id: string, note: string, humanAction: string) => void
  fill: (text: string) => void
  toggleTick: (id: string, item: string) => void
  toggleDetails: () => void
  openViewer: (id: string) => void
  copyCheck: (id: string, text: string, surface: RenderSurface) => void
  strip: StripHandlers
  /** j / k (or the Tab ring): this card takes the focus; `page` is its page. */
  focus: (id: string, page: number) => void
  /** p / n: show this page, its first card focused. */
  turnPage: (page: number) => void
  /** s, or a click on a card's mark: select or unselect that task. */
  select: (id: string) => void
  /** `all`: select these tasks (this page's); `none`: clear the selection. */
  selectAll: (ids: readonly string[]) => void
  selectNone: () => void
  /** u: read the queue and the phases again (retry while offline). */
  refresh: () => void
  /** t: arm (or disarm) the focused card's checks for one digit. */
  armTick: () => void
  /** b / g: ask (y/n, or the shared note) for exactly these task ids. */
  bulkAsk: (kind: 'done' | 'agent', ids: readonly string[]) => void
  bulkCancel: () => void
  bulkDone: (ids: readonly string[]) => void
  /** `actions`: the check each card showed, by task id. */
  bulkSendBack: (ids: readonly string[], note: string, actions: Readonly<Record<string, string>>) => void
  /** m: the sidebar over the pane. */
  toggleSidebar: () => void
}

const NOT_SELECTABLE = 'Issues and decisions are not signed off from the list'
const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`

function healthOf(v: ReviewView): Health {
  const s = v.snapshot
  if (v.connecting === true && (s === null || !s.reachable)) return { kind: 'info', word: 'connecting' }
  if (s === null) return { kind: 'info', word: 'loading' }
  if (!s.reachable) return { kind: 'critical', word: 'offline' }
  return v.demo === true ? { kind: 'info', word: 'demo' } : { kind: 'success', word: 'tm ok' }
}

/** An item's dim context: collapsed `patch-1-9 · lane full · in review for 5h`; focused, the gate instead of the age. */
function contextOf(k: Kit, it: TmQueueItem, v: ReviewView, focused: boolean): Seg[] {
  const subtle = k.t.fg.subtle
  const age = ageLabel(it.timestamp, v.now)
  if (it.kind === 'issue') return [{ text: [`open ${it.severity} issue`, age === '' ? '' : `${age} old`].filter(Boolean).join(' · '), color: subtle }]
  if (it.kind === 'decision') return [{ text: ['open decision', age === '' ? '' : `${age} old`].filter(Boolean).join(' · '), color: subtle }]
  const d = v.details[it.id]
  if (d === undefined) return [{ text: [focused ? 'loading…' : `${it.priority} priority`, age === '' ? '' : `in review for ${age}`].filter(Boolean).join(' · '), color: subtle }]
  const head = [d.phase ?? '', `lane ${d.lane || 'none'}`].filter(Boolean).join(' · ')
  if (!focused) return [{ text: [head, age === '' ? '' : `in review for ${age}`].filter(Boolean).join(' · '), color: subtle }]
  const gate = gateSignal(d.gateState)
  return gate === null
    ? [{ text: head, color: subtle }]
    : [
        { text: `${head} · `, color: subtle },
        { text: '● ', color: k.t.tone[gate.kind] },
        { text: gate.word, color: subtle },
      ]
}

/** The `human` field: the check's label (and detail) when it lists items, else the whole human action on one line. */
function humanOf(check: TmCheck, action: string): string {
  if (check.items.length > 0) return [check.label, check.detail].filter(Boolean).join(' — ')
  return oneLine(action) || '(no human_action recorded)'
}

const actionOf = (it: TmQueueItem, v: ReviewView): string => (it.kind === 'task' ? it.humanAction || v.details[it.id]?.humanAction || '' : '')

/** The checks: `1  ☐ text` a row in the content column; armed by `t`, the first nine press by their digit. */
function checkRows(k: Kit, it: TmQueueItem, check: TmCheck, ticked: readonly string[], armed: boolean, ticking: boolean, on: ReviewHandlers, width: number): RenderNode[] {
  const { Box, Text, Button } = k.ui
  const { t } = k
  const room = Math.max(8, width - CONTENT_COL - 3)
  return check.items.map((entry, i) => {
    const label = `${ticked.includes(entry) ? '☑' : '☐'} ${entry}`
    const shown = truncate(label, room)
    const digit = i < 9 ? String(i + 1) : ' '
    const key = armed && ticking && i < 9 ? digit : undefined
    if (!armed) {
      return (
        <Box paddingLeft={CONTENT_COL} flexDirection="row">
          <Text color={t.fg.disabled}>{`${digit}  `}</Text>
          <Text color={t.fg.default}>{shown}</Text>
        </Box>
      )
    }
    return (
      <Box paddingLeft={CONTENT_COL} flexDirection="row">
        {key === undefined ? <Text color={t.fg.disabled}>{`${digit}  `}</Text> : null}
        <Box key={`tick-${i}-col`} flexDirection="row">
          <Button key={`tick-${i}`} plain {...(key === undefined ? {} : { hotkey: key })} label={label} onPress={() => on.toggleTick(it.id, entry)}>
            <Text color={t.fg.default}>{shown}</Text>
          </Button>
          {key === undefined ? null : (
            <Box position="absolute" left={0} top={0}>
              <Text color={t.signatureText} bold>{`${key}  `}</Text>
            </Box>
          )}
        </Box>
      </Box>
    )
  })
}

/** The focused item: its context, the `human` field, the checks with `t` and `c` under them, details, a refusal, then its actions or y/n. */
async function focusedCard(k: Kit, it: TmQueueItem, head: HeadSpec, v: ReviewView, on: ReviewHandlers, width: number): Promise<RenderNode> {
  const { t } = k
  const room = width - 4
  if (it.kind !== 'task') {
    const what = it.kind === 'issue' ? `An open ${it.severity} issue: look at it with the agent.` : 'An open decision: resolve it with the decision skill (taskmaster:decision).'
    const open = actionKey(k, {
      id: 'open',
      key: 'o',
      label: 'open in prompt',
      rung: 'primary',
      onPress: () => on.fill(it.kind === 'decision' ? `Resolve decision ${it.id} with the taskmaster:decision skill` : `Look at ${it.id}`),
    })
    const lines: Seg[][] = [contextOf(k, it, v, true), ...wrapText(what, room - CONTENT_COL).map(text => [{ text, color: t.fg.default }])]
    return focusedItem(k, { id: it.id, head, lines, actions: actionRows(k, [open], room - 1) }, width)
  }
  const action = actionOf(it, v)
  const check = splitCheck(action)
  const ticked = await v.ticks(it.id)
  const mode = cardMode(v.cursor, it)
  const armed = mode === 'card'
  const lines: Seg[][] = [contextOf(k, it, v, true), fieldSegs(k, 'human', humanOf(check, action))]
  const extra: RenderNode[] = checkRows(k, it, check, ticked, armed, v.list.ticking, on, room)
  if (armed) {
    const keys = [
      ...(check.items.length === 0 ? [] : [actionKey(k, { id: 'tick', key: 't', label: 'tick check 1-9', onPress: on.armTick })]),
      actionKey(k, { id: 'copy', key: 'c', label: 'copy live check', onPress: press => on.copyCheck(it.id, action, press.surface) }),
    ]
    const { Box } = k.ui
    extra.push(
      <Box paddingLeft={CONTENT_COL} flexDirection="column">
        {actionRows(k, keys, room - CONTENT_COL)}
      </Box>,
    )
  }
  if (v.detailsOpen) {
    const d = v.details[it.id]
    const facts =
      d === undefined
        ? ['loading details…']
        : [`branch: ${d.branch || 'no branch'}`, ...(d.pr ? [`PR: ${d.pr}`] : []), ...(d.notes ?? []).map(note => `note: ${note}`), ...(d.links ?? []).map(link => `link: ${link}`)]
    for (const fact of facts) extra.push(itemLine(k, [{ text: fact, color: t.fg.subtle }], room))
  }
  if (v.cursor.refusal !== '' && v.cursor.currentId === it.id) {
    wrapText(oneLine(v.cursor.refusal), Math.max(10, room - CONTENT_COL - 11)).forEach((text, i) =>
      extra.push(itemLine(k, [{ text: i === 0 ? '▲ REFUSED  ' : '           ', color: t.tone.critical, bold: true }, { text, color: t.fg.default }], room)),
    )
  }
  if (mode === 'confirm') {
    const open = check.items.filter(entry => !ticked.includes(entry)).length
    const question = doneQuestion(open, check.items.length, it.id, room - 16)
    return focusedItem(k, { id: it.id, head, lines, extra, actions: askRow(k, { id: 'confirm', question, onYes: () => on.confirmDone(it.id), onNo: on.cancel, width: room - 1 }) }, width)
  }
  const actions = actionRows(
    k,
    [
      actionKey(k, { id: 'done', key: 'd', label: 'mark task done', rung: 'primary', onPress: () => on.askDone(it.id) }),
      actionKey(k, { id: 'back', key: 'a', label: 'send task to agent', onPress: () => on.askNote(it.id) }),
      actionKey(k, { id: 'open', key: 'o', label: 'open task', onPress: () => on.fill(`Look at ${it.id}`) }),
      actionKey(k, { id: 'viewer', key: 'v', label: 'task in viewer', onPress: () => on.openViewer(it.id) }),
      actionKey(k, { id: 'details', key: 'i', label: `task detail ${v.detailsOpen ? '▾' : '▸'}`, onPress: on.toggleDetails }),
    ],
    room - 1,
  )
  return focusedItem(k, { id: it.id, head, lines, extra, actions }, width)
}

/** The note for the agent, on its own: the card it answers and the Input, nothing else with a key. */
function composeNote(k: Kit, it: TmQueueItem, action: string, on: ReviewHandlers, inner: number): RenderNode[] {
  const { Text, Input, Button } = k.ui
  const { t } = k
  return [
    line(k, [{ text: 'note for the agent', color: t.signatureText, bold: true }], inner).el,
    line(k, [{ text: it.id, color: t.fg.bold, bold: true }, { text: `  ${oneLine(it.title)}`, color: t.fg.default }], inner).el,
    ...(action === '' ? [] : wrapText(action, inner).map(text => <Text color={t.fg.subtle}>{text}</Text>)),
    <Input key="note" label="note for the agent" placeholder="what should change" submitLabel="send back" autoFocus onSubmit={value => on.sendBack(it.id, value, action)} />,
    <Button key="note-cancel" plain label="cancel" onPress={on.cancel} />,
  ]
}

/** The shared note for `g`, on its own like a card's note: the tasks it goes to and the Input, nothing else with a key. */
function composeBulkNote(k: Kit, items: readonly TmQueueItem[], v: ReviewView, on: ReviewHandlers, inner: number): RenderNode[] {
  const { Input, Button } = k.ui
  const { t } = k
  const ids = v.list.bulkIds
  const actions: Record<string, string> = {}
  for (const it of items) if (it.kind === 'task' && ids.includes(it.id)) actions[it.id] = actionOf(it, v)
  return [
    line(k, [{ text: `one note for ${plural(ids.length, 'task')}`, color: t.signatureText, bold: true }], inner).el,
    ...ids.map(id => {
      const it = items.find(x => x.id === id)
      return line(k, [{ text: id, color: t.fg.bold, bold: true }, { text: `  ${it === undefined ? '(no longer listed)' : oneLine(it.title)}`, color: t.fg.default }], inner).el
    }),
    <Input key="bulk-note" label={`note for the agent (${plural(ids.length, 'task')})`} placeholder="what should change" submitLabel="send back" autoFocus onSubmit={value => on.bulkSendBack(ids, value, actions)} />,
    <Button key="bulk-cancel" plain label="cancel" onPress={on.bulkCancel} />,
  ]
}

export async function reviewPaneTree(ui: Ui, rr: Rr, v: ReviewView, on: ReviewHandlers, width: number): Promise<RenderNode> {
  const k = await kitOf(ui, rr, v.surface, v.say)
  const { Text } = ui
  const { t } = k
  const s = v.snapshot
  const inner = Math.max(30, width - 4)
  const frame = (children: readonly RenderNode[]): RenderNode => paneFrame(k, { name: 'review', health: healthOf(v), width }, children)
  const tail: FooterItem[] = [
    { keys: 'm', word: 'sidebar', press: [{ key: 'm', onPress: on.toggleSidebar }] },
    { keys: 'esc', word: 'close' },
  ]
  if (s === null || !s.reachable) {
    if (v.connecting === true) return frame([<Text color={t.fg.subtle}>{CONNECTING}</Text>, <Text> </Text>, footer(k, tail, inner)])
    if (s === null) return frame([<Text color={t.fg.subtle}>Loading…</Text>, <Text> </Text>, footer(k, tail, inner)])
    const retry = actionKey(k, { id: 'refresh', key: 'u', label: 'retry tm connection', onPress: () => on.refresh() })
    return frame([offlineRow(k, s.reason, retry, inner), <Text> </Text>, footer(k, [{ keys: 'u', word: 'retry' }, ...tail], inner)])
  }
  const items = reviewItems(s, v.cursor, v.details)
  const view = listView(items, v.cursor.currentId, v.list.page)
  const focused = view.focused

  if (v.list.bulk === 'agent') return frame(composeBulkNote(k, items, v, on, inner))
  if (focused !== null && focused.kind === 'task' && cardMode(v.cursor, focused) === 'note') return frame(composeNote(k, focused, actionOf(focused, v), on, inner))

  const top = paneToolbar(k, {
    left: phasePicker(k, v.strip, on.strip),
    right: [pagerPiece(k, { page: view.page, pages: view.pages, onTurn: page => on.turnPage(page) }), refreshPiece(k, { label: 'refresh', onPress: () => on.refresh() })],
    width: inner,
  })
  const asking = v.list.bulk === 'done' || (focused !== null && cardMode(v.cursor, focused) === 'confirm')
  const prev = neighbour(items, view, -1)
  const next = neighbour(items, view, 1)
  const keys: FooterItem[] = [
    {
      keys: 'j/k',
      word: 'move',
      press: [
        { id: 'focus-next', key: 'j', onPress: () => (next === null ? k.say('Already on the last card') : on.focus(next.id, next.page)) },
        { id: 'focus-prev', key: 'k', onPress: () => (prev === null ? k.say('Already on the first card') : on.focus(prev.id, prev.page)) },
      ],
    },
    {
      keys: 's',
      word: 'select',
      press: [
        {
          key: 's',
          onPress: () => {
            if (focused === null) return
            if (isSelectable(focused)) on.select(focused.id)
            else k.say(NOT_SELECTABLE)
          },
        },
      ],
    },
    v.list.ticking ? { keys: '1-9', word: 'tick a check' } : { keys: 't 1-9', word: 'tick' },
    ...phaseFooter(k, v.strip, on.strip),
    ...(asking
      ? []
      : [
          {
            keys: 'p/n',
            word: 'page',
            press: [
              { key: 'p', onPress: () => (view.page === 0 ? k.say('Already on the first page') : on.turnPage(view.page - 1)) },
              { key: 'n', onPress: () => (view.page >= view.pages - 1 ? k.say('Already on the last page') : on.turnPage(view.page + 1)) },
            ],
          },
        ]),
    { keys: 'u', word: 'refresh', press: [{ key: 'u', onPress: () => on.refresh() }] },
    ...tail,
  ]
  const done = v.cursor.done.length

  if (focused === null) {
    const tab = orderPhases(v.strip.view.phases, v.strip.allCount, v.strip.view.choice).find(x => x.id === v.strip.view.choice)
    const empty =
      v.strip.view.choice === ALL
        ? emptyRow(k, `Queue clear · ${done} done this pass`, null, inner)
        : emptyRow(k, emptyLine(v.strip.empty, tab), actionKey(k, { id: 'phase-all', key: '0', label: 'show all phases', onPress: () => on.strip.choose(ALL) }), inner)
    return frame([top, ...withPhaseMenu(k, v.strip, on.strip, [<Text> </Text>, empty]), <Text> </Text>, footer(k, keys, inner)])
  }

  const newest = ageLabel(newestStamp(items), v.now)
  const sentence = [
    `${s.queueTotal}${s.queueCapped === true ? '+' : ''} waiting on you`,
    ...(newest === '' ? [] : [`newest ${newest} ago`]),
    ...(s.queueTotal > items.length ? [`${items.length} listed`] : []),
    ...(done > 0 ? [`${done} done this pass`] : []),
  ].join(' · ')
  const counts = kindCounts(items)
  const summary = summaryRow(k, sentence, KINDS.map(kind => ({ word: KIND_WORD[kind].toUpperCase(), count: counts[kind] })), inner)
  const pageTasks = view.entries.filter(isSelectable).map(it => it.id)
  const chosen = selectedTasks(items, v.list.selected).map(it => it.id)
  const n = chosen.length
  const bulk: RenderNode[] =
    v.list.bulk === 'done'
      ? askRow(k, {
          id: 'bulk',
          question: `mark ${plural(v.list.bulkIds.length, 'task')} done: ${v.list.bulkIds.join(', ')}?`,
          onYes: () => on.bulkDone(v.list.bulkIds),
          onNo: on.bulkCancel,
          width: inner,
        })
      : v.list.bulk === 'busy'
        ? [line(k, [{ text: `working · one task at a time: ${v.list.bulkIds.length}`, color: t.tone.info }], inner).el]
        : [
            bulkRow(k, {
              keys: [
                actionKey(k, { id: 'bulk-done', key: 'b', label: `mark ${plural(n, 'task')} done`, onPress: () => on.bulkAsk('done', chosen), ...(n === 0 ? { dim: 'Nothing selected to mark done' } : {}) }),
                actionKey(k, { id: 'bulk-agent', key: 'g', label: `send ${plural(n, 'task')} to agent`, onPress: () => on.bulkAsk('agent', chosen), ...(n === 0 ? { dim: 'Nothing selected to send to the agent' } : {}) }),
              ],
              scopes: [
                { id: 'sel-all', label: 'all', onPress: () => on.selectAll(pageTasks) },
                { id: 'sel-none', label: 'none', onPress: on.selectNone },
              ],
              width: inner,
            }),
          ]
  const entries: ListEntry[] = []
  for (const it of view.entries) {
    const head: HeadSpec = {
      mark: { key: `sel:${it.id}`, selected: v.list.selected.includes(it.id), onPress: () => on.select(it.id), ...(isSelectable(it) ? {} : { dim: NOT_SELECTABLE }) },
      badge: KIND_WORD[reviewKind(it)].toUpperCase(),
      ident: it.id,
      title: oneLine(it.title),
      age: ageLabel(it.timestamp, v.now),
    }
    if (it.id === focused.id) {
      entries.push({ focused: true, nodes: [await focusedCard(k, it, head, v, on, inner)] })
    } else {
      const human = it.kind === 'task' ? humanOf(splitCheck(actionOf(it, v)), actionOf(it, v)) : ''
      entries.push({ focused: false, nodes: item(k, head, [contextOf(k, it, v, false), ...(human === '' ? [] : [fieldSegs(k, 'human', human)])], inner) })
    }
  }
  return frame([top, ...withPhaseMenu(k, v.strip, on.strip, [summary, ...bulk, <Text> </Text>, ...listing(k, entries, inner)]), <Text> </Text>, footer(k, keys, inner)])
}
```

`TmCheck` is the type `splitCheck` returns; if `model.ts` does not export it under that name, export it (`export type TmCheck = …` already exists at the top of the review-card section of `model.ts`).

In `mods/taskmaster-mods/hooks/register.tsx`, in the `tm-review` render: add `demo: mod.source === 'demo',` to `view` (after `say`), and add to the handlers object (after `bulkSendBack`):

```tsx
        toggleSidebar: () => act(f => f.toggleSidebar()),
```

The `withSidebar` wrapper from Task C2 stays as it is.

- [ ] **Step 4: Run the checks**

Expected: `tests/review-look.test.ts` passes (+6); every migrated file passes; validate ✔; tsc clean. Run a second time if only `timed out after 5000 ms` failures remain.

- [ ] **Step 5: Commit**

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
git -C $WT add mods/taskmaster-mods/hooks/review.tsx mods/taskmaster-mods/hooks/review-list.ts mods/taskmaster-mods/hooks/register.tsx mods/taskmaster-mods/tests
git -C $WT commit -m "feat(taskmaster-mods): the review pane on the rework vocabulary — picker, sentence and tallies, 3 items a page, one filled key, a dim footer"
```

---

### Task D3: The handovers pane on the vocabulary, and the close double-count fix

**Files:**
- Modify: `mods/taskmaster-mods/hooks/handovers.tsx` (whole file)
- Modify: `mods/taskmaster-mods/hooks/model.ts` (`handoverPage` pages by `LIST_PAGE_SIZE`)
- Modify: `mods/taskmaster-mods/hooks/handover-list.ts` (`moveTarget`, `arrival` page by `LIST_PAGE_SIZE`)
- Modify: `mods/taskmaster-mods/hooks/flows.ts` (`closeHandovers` counts off only handovers that were open)
- Modify: `mods/taskmaster-mods/hooks/register.tsx` (the handovers render: the frame replaces the page-ground Box; `demo`, `toggleSidebar`)
- Create: `mods/taskmaster-mods/tests/handovers-look.test.ts`
- Modify: `mods/taskmaster-mods/tests/handover-flows.test.ts` (one test appended)
- Modify (migration table): `tests/handovers.test.ts`, `tests/handovers-draw.test.ts`, `tests/handover-list.test.ts`, `tests/handover-data.test.ts`, `tests/session-flows.test.ts`, `tests/reload.test.ts`, `tests/live.test.ts`

**Interfaces:**
- Consumes: vocab as in Task D2, plus `togglePiece`, `badgeSeg`, `statusBadgeWord`; `handover-list.ts` rules (unchanged apart from paging); `TmFlows.toggleSidebar`.
- Produces: `handoversPaneTree(k: Kit, v: HandoversView, on: HandoversHandlers, width: number): RenderNode` (now one `paneFrame`, no longer `RenderNode[]`); `HandoversView` gains `demo?: boolean`; `HandoversHandlers` gains `toggleSidebar: () => void`. Footer Buttons: `move-down` (j), `move-up` (k), `key:s`, `key:h`, `key:l`, `key:f`, `key:p`, `key:n` (absent while a y/n is up), `key:u`, `key:m`. Card keys `resume` r (primary), `new-tab` w, `copy` c, `summary` i, `close` x (destructive, last).

- [ ] **Step 1: Write the failing tests**

`mods/taskmaster-mods/tests/handovers-look.test.ts`:

```ts
// User intent: the handovers pane as the approved prototype draws it (spec §9, §15.6–15.7, Handovers.dc.html) — row 1 the
// picker with ◐ show closed, the pager and u; the sentence; the bulk close row; 3 items a page (title / slug · branch · linked
// tasks / next), the focused one boxed with r resume handover here filled and x close handover last; a dim footer.
import { describe, expect, test } from 'claude-code/testing'

import { DEMO_SUMMARIES, demoHandoverList, demoPhases, demoSnapshot } from '../hooks/demo'
import { FRESH_MARKS } from '../hooks/handover-list'
import { type HandoversHandlers, type HandoversView, handoversPaneTree } from '../hooks/handovers'
import { FRESH_PHASE_VIEW } from '../hooks/phases'
import { byKey, testKit, textsOf, walk } from './fixtures/kit-ui'
import { elementsOf, widthOf } from './fixtures/measure'
import { STUB_TOKENS as T } from './fixtures/tokens'

const HOUR = 3_600_000
const NOW = 200 * HOUR
const SNAP = demoSnapshot(NOW)
const LIST = demoHandoverList(NOW)
const [FIRST, SECOND] = SNAP.handovers
const noop = (): void => undefined
const ON: HandoversHandlers = {
  pick: noop,
  move: noop,
  turnPage: noop,
  select: noop,
  selectOnly: noop,
  toggleSummary: noop,
  resume: noop,
  newTab: noop,
  copy: noop,
  askClose: noop,
  confirmClose: noop,
  cancelClose: noop,
  toggleClosed: noop,
  refresh: noop,
  strip: { choose: noop, toggleDone: noop, overlay: noop },
  toggleSidebar: noop,
}
const view = (over: Partial<HandoversView> = {}): HandoversView => ({
  snapshot: SNAP,
  list: LIST,
  marks: FRESH_MARKS,
  phaseView: { ...FRESH_PHASE_VIEW, phases: demoPhases('handovers'), loaded: true },
  pick: '',
  page: 0,
  summaries: DEMO_SUMMARIES,
  summaryOpen: '',
  now: NOW,
  focused: true,
  ...over,
})
const keysOf = (tree: unknown): Record<string, string[]> => {
  const keys: Record<string, string[]> = {}
  for (const b of walk(tree).filter(e => e.type === 'Button')) if (typeof b.props?.hotkey === 'string') (keys[b.props.hotkey] ??= []).push(String(b.props.key))
  return keys
}

describe('the handovers pane, as drawn', () => {
  test('the frame, row 1, the sentence and the bulk close row', () => {
    const tree = handoversPaneTree(testKit(), view(), ON, 116)
    expect(walk(tree)[0]?.props).toMatchObject({ borderStyle: 'round', borderColor: T.border.default })
    for (const text of ['HANDOVERS', ' All  6 ▾ ', '◐ show closed', ' 1 / 2 ', '5 open · newest 2h ago']) expect(textsOf(tree), text).toContain(text)
    expect(byKey(tree, 'show-closed')?.props?.hotkey).toBeUndefined()
    expect(byKey(tree, 'close-selected')?.props).toMatchObject({ hotkey: 'b', label: 'close 0 handovers', dimColor: true })
  })

  test('3 items a page; the focused one is boxed with its linked tasks and keys; collapsed ones carry none', () => {
    const tree = handoversPaneTree(testKit(), view(), ON, 116)
    expect(walk(tree).filter(e => String(e.props?.key ?? '').startsWith('ho-sel:')).filter(e => e.type === 'Button')).toHaveLength(3)
    expect(walk(tree).filter(e => e.type === 'Box' && e.props?.borderStyle === 'round' && String(e.props?.key ?? '').startsWith('card:')).map(e => e.props?.key)).toEqual([`card:${FIRST!.id}`])
    for (const [key, hotkey, label] of [
      ['resume', 'r', 'resume handover here'],
      ['new-tab', 'w', 'resume in new tab'],
      ['copy', 'c', 'copy handover'],
      ['summary', 'i', 'decisions·blockers ▸'],
      ['close', 'x', 'close handover'],
    ] as const) {
      expect(byKey(tree, key)?.props, key).toMatchObject({ hotkey, label })
    }
    expect(byKey(tree, 'resume-box')?.props?.backgroundColor).toBe(T.keycap.signature.bg)
    expect(walk(tree).find(e => (e.children ?? []).includes(' x '))?.props?.color).toBe(T.tone.warning)
    const texts = textsOf(tree)
    for (const text of ['○ OPEN         ', 'Shipped unified-chat-022 to dev', `${FIRST!.id} · main · 1 linked task`, 'next  ', 'task  ', '● IN REVIEW', 'unified-chat-022']) {
      expect(texts, text).toContain(text)
    }
    expect(byKey(tree, `ho:${SECOND!.id}`)?.props?.hotkey).toBeUndefined()
  })

  test('the footer owns j k s h l f p n u m, no hotkey on two Buttons; a y/n takes n from the footer', () => {
    const keys = keysOf(handoversPaneTree(testKit(), view(), ON, 116))
    for (const [hotkey, id] of [['j', 'move-down'], ['k', 'move-up'], ['s', 'key:s'], ['f', 'key:f'], ['n', 'key:n'], ['m', 'key:m']] as const) expect(keys[hotkey], hotkey).toEqual([id])
    expect(Object.entries(keys).filter(([, ids]) => ids.length > 1)).toEqual([])
    const asked = keysOf(handoversPaneTree(testKit(), view({ marks: { ...FRESH_MARKS, ask: { kind: 'one', ids: [FIRST!.id] } } }), ON, 116))
    expect(asked.n).toEqual(['close-no'])
  })

  test('a closed handover draws faint with ✓ CLOSED, and its x is disabled', () => {
    const closed = LIST.handovers.find(h => h.status === 'closed')!
    const tree = handoversPaneTree(testKit(), view({ marks: { ...FRESH_MARKS, showClosed: true }, pick: closed.id, page: 2 }), ON, 116)
    expect(textsOf(tree)).toContain('✓ CLOSED       ')
    expect(byKey(tree, 'close')?.props?.dimColor).toBe(true)
  })

  test('every row fits the pane at 60 and 100 columns', () => {
    for (const columns of [60, 100]) {
      const tree = handoversPaneTree(testKit(), view(), ON, columns)
      const rows = elementsOf(tree).filter(e => e.type === 'Box' && e.props?.flexDirection === 'row' && e.props?.position !== 'absolute')
      for (const row of rows) expect(widthOf(row), `${columns}`).toBeLessThanOrEqual(columns - 4)
    }
  })
})
```

In `mods/taskmaster-mods/tests/handover-flows.test.ts`, append inside `describe('closing handovers', …)`, after its last test (the file's `rig` seeds `handoverList` from `demoHandoverList`, whose closed handovers include `2026-09-30-row-counter-ported`; `closer` is the file's own helper):

```ts
  test('a handover already closed counts nothing off the totals when its close is confirmed again (Task 4.4 double-count)', async () => {
    const { close } = closer()
    const { flows, state } = rig({ closeHandover: close })
    const listTotal = state.handoverList?.total
    const snapshotTotal = state.snapshot?.handoversTotal
    await flows.closeHandovers(['2026-09-30-row-counter-ported'])
    expect(state.handoverList?.total).toBe(listTotal)
    expect(state.snapshot?.handoversTotal).toBe(snapshotTotal)
  })
```

**Migration table** (apply to the files in **Files**):

| Old | New |
|-----|-----|
| `handoversPaneTree(...)` returns an array (tests index it or spread it) | one node: pass it to `walk` / `textsOf` / `byKey` directly |
| press `{ key: 'phase-tab:<id>' }`; `phase-tab:` label `All N`; `phase-tab:~none` label `no task N` | press `phase-field` then `phase-pick:<id>`; `phase-field` label `` ` All  N ▾ ` ``; open the list for `phase-pick:~none`, label `/^no task +N$/` |
| `phase-tab:` `{ hotkey: '0', … }` (strip digits) | no digit outside the open list |
| `page-prev` / `page-next` `{ hotkey: 'p' / 'n', dimColor: true }` | `page-prev` / `page-next` `{ dimColor: true }` (no hotkey); `key:p` / `key:n` carry `p` / `n` |
| `refresh` `{ hotkey: 'u', label: 'refresh' }`; offline `{ hotkey: 'u', label: 'retry' }` | `refresh` `{ label: 'u refresh' }` + `key:u`; offline `refresh` `{ hotkey: 'u', label: 'retry tm connection' }` |
| `close-selected` `{ hotkey: 'b', label: 'Close N' }` | `{ hotkey: 'b', label: 'close N handovers' }` (`handover` when 1) |
| Text `' Close 2 handovers? '` | `'close 2 handovers?'` |
| `ho-sel:<id>` `{ hotkey: 's', label: '□' }` | `ho-sel:<id>` `{ label: '□' }` (no hotkey); `key:s` selects the picked one |
| `move-down` `{ hotkey: 'j', label: '↓' }`, `move-up` `{ hotkey: 'k', label: '↑', dimColor: true }` | `{ hotkey: 'j', label: '' }`, `{ hotkey: 'k', label: '' }`; at an end the press toasts `Already on the last handover` / `Already on the first handover` |
| `phase-all` `{ hotkey: '0', label: 'All' }` | `{ hotkey: '0', label: 'show all phases' }` |
| card labels `resume here`, `new tab`, `copy`, `▸ decisions·blockers`, `close` | `resume handover here`, `resume in new tab`, `copy handover`, `decisions·blockers ▸` (`▾` open), `close handover` |
| linked task badge `' IN REVIEW '`, `' IN PROGRESS '` | `'● IN REVIEW'`, `'● IN PROGRESS'` |
| closed mark `' CLOSED '` badge | head badge `'✓ CLOSED       '` |
| head text `'<age> ago · <tldr>'` and refs `'<branch> · N tasks'` | title `'<tldr>'` with the age at the right (`'   2h'`); line 2 `'<id> · <branch> · N linked task(s)'` (`'no linked task'` when none) |
| `NEXT` label row | field `'next  '` + the next action |
| five a page (`PAGE_SIZE`; 7 open → 2 pages of 5 + 2; `page: 1` holds the 6th) | three a page (`LIST_PAGE_SIZE`): 7 → 3 pages; page `1` holds the 4th–6th; `openHandover('h-7')` lands on page `2` (`session-flows.test.ts`: `[seen.page, …]` `[2, 'h-7', …]`) |
| `withClosed` counts on `phase-tab:` labels | the same counts on `phase-pick:<id>` labels and `phase-field` |

- [ ] **Step 2: Run the suite to see the new tests fail**

Expected: `tests/handovers-look.test.ts` and the double-count test fail; the migrated expectations fail.

- [ ] **Step 3: Implement**

In `mods/taskmaster-mods/hooks/model.ts`, in `handoverPage`, replace every `PAGE_SIZE` with `LIST_PAGE_SIZE` (three occurrences in that function's body), and in its doc comment replace `(PAGE_SIZE a page` with `(LIST_PAGE_SIZE a page`.

In `mods/taskmaster-mods/hooks/handover-list.ts`, replace:

```ts
import { ageLabel, handoverPage, PAGE_SIZE } from './model'
```

with:

```ts
import { ageLabel, handoverPage, LIST_PAGE_SIZE as PAGE_SIZE } from './model'
```

In `mods/taskmaster-mods/hooks/flows.ts`, in `closeHandovers`, replace:

```ts
    if (closed.length > 0) {
      await d.write.handoverMarks(m => ({ ...m, selected: m.selected.filter(id => !closed.includes(id)) }))
      await d.write.handoverList(l => (l === null ? l : markClosed(l, closed)))
      await d.write.snapshot(s => (s === null ? s : withoutHandovers(s, closed)))
    }
```

with:

```ts
    if (closed.length > 0) {
      // Only handovers that were open count off the totals: one already closed (its x under ◐ closed, a stale selection) that
      // the server confirms again must not (Task 4.4's double-count). One the lists do not hold counts as open.
      const before = await handoverState()
      const held = [...(before.list?.handovers ?? []), ...(before.snapshot?.handovers ?? [])]
      const opened = closed.filter(id => held.find(h => h.id === id)?.status !== 'closed')
      await d.write.handoverMarks(m => ({ ...m, selected: m.selected.filter(id => !closed.includes(id)) }))
      await d.write.handoverList(l => (l === null ? l : markClosed(l, opened)))
      await d.write.snapshot(s => (s === null ? s : withoutHandovers(s, opened)))
    }
```

Replace the whole of `mods/taskmaster-mods/hooks/handovers.tsx` with:

```tsx
// User intent: the handovers pane in the rework's vocabulary (spec §9, §15.6–15.7, Handovers.dc.html) — the phase picker with
// ◐ show closed, one sentence, a bulk close row, three 3-line items a page (title / slug · branch · linked tasks / next), the
// focused one boxed with its linked tasks' live status and `r resume handover here` filled, `x close handover` last.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode, RenderSurface } from 'claude-code'

import type { TmCloseAsk, TmHandover, TmHandoverList, TmHandoverMarks, TmHandoverSummary, TmLinkedTask, TmPhase, TmPhaseView, TmSnapshot } from '../types'
import { allCount, closableSelection, isClosed, listedHandovers, moveTarget, phaseHandovers, tallyLine } from './handover-list'
import type { Kit, StripHandlers, StripView } from './kit'
import { ageLabel, CONNECTING, dateOf, handoverPage, LIST_PAGE_SIZE, oneLine, phaseName } from './model'
import { ALL, emptyLine, orderPhases, withClosed } from './phases'
import {
  actionKey,
  actionRows,
  askRow,
  badgeSeg,
  bulkRow,
  emptyRow,
  fieldSegs,
  focusedItem,
  footer,
  type FooterItem,
  type HeadSpec,
  type Health,
  item,
  itemLine,
  type ListEntry,
  listing,
  offlineRow,
  pagerPiece,
  paneFrame,
  paneToolbar,
  phaseFooter,
  phasePicker,
  refreshPiece,
  type Seg,
  statusBadgeWord,
  summaryRow,
  togglePiece,
  withPhaseMenu,
} from './vocab'

export type HandoversView = {
  readonly snapshot: TmSnapshot | null
  /** The pane's own read (null: not yet; the snapshot's window is drawn meanwhile). */
  readonly list: TmHandoverList | null
  readonly marks: TmHandoverMarks
  readonly phaseView: TmPhaseView
  readonly pick: string
  readonly page: number
  readonly summaries: Readonly<Record<string, TmHandoverSummary>>
  readonly summaryOpen: string
  readonly now: number
  /** The pane holds the keyboard. */
  readonly focused: boolean
  /** tm is being retried after a transient failure: with no good snapshot the pane says so. */
  readonly connecting?: boolean
  /** Demo data: the frame's health says demo. */
  readonly demo?: boolean
}

export type HandoversHandlers = {
  readonly pick: (id: string) => void
  /** j / k: show this page and focus this handover. */
  readonly move: (page: number, id: string) => void
  /** p / n: show this page and pick its first handover. */
  readonly turnPage: (page: number, firstId: string) => void
  readonly select: (id: string) => void
  /** `all` (this page's open ones) and `none` ([]). */
  readonly selectOnly: (ids: readonly string[]) => void
  readonly toggleSummary: (handover: TmHandover, open: boolean) => void
  readonly resume: (handover: TmHandover) => void
  readonly newTab: (handover: TmHandover, surface: RenderSurface) => void
  readonly copy: (handover: TmHandover, surface: RenderSurface) => void
  readonly askClose: (ask: TmCloseAsk) => void
  readonly confirmClose: () => void
  readonly cancelClose: () => void
  readonly toggleClosed: () => void
  readonly refresh: () => void
  readonly strip: StripHandlers
  /** m: the sidebar over the pane. */
  readonly toggleSidebar: () => void
}

const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`

function healthOf(v: HandoversView): Health {
  const s = v.snapshot
  if (v.connecting === true && (s === null || !s.reachable)) return { kind: 'info', word: 'connecting' }
  if (s === null) return { kind: 'info', word: 'loading' }
  if (!s.reachable) return { kind: 'critical', word: 'offline' }
  return v.demo === true ? { kind: 'info', word: 'demo' } : { kind: 'success', word: 'tm ok' }
}

/** Line 2: `<id> · <branch> · N linked tasks` (`no linked task` when none). */
function refsSegs(k: Kit, ho: TmHandover): Seg[] {
  const ids = Array.isArray(ho.taskIds) ? ho.taskIds : []
  const branch = typeof ho.branch === 'string' ? ho.branch : ''
  return [{ text: [ho.id, branch, ids.length === 0 ? 'no linked task' : `${plural(ids.length, 'linked task')}`].filter(Boolean).join(' · '), color: k.t.fg.subtle }]
}

/** A linked task in the focused card: `task  ● IN REVIEW  <id>  <title>  <phase>`. */
function taskSegs(k: Kit, id: string, task: TmLinkedTask | undefined, phases: readonly TmPhase[]): Seg[] {
  const { t } = k
  const phase = task === undefined || task.phase === '' ? '' : phaseName(phases.find(p => p.id === task.phase) ?? { id: task.phase, status: '', order: 0, count: 0, name: '' })
  return [
    { text: 'task  ', color: t.signatureText },
    badgeSeg(k, statusBadgeWord(task?.status ?? '')),
    { text: '  ', color: t.fg.default },
    { text: id, color: t.fg.bold, bold: true },
    { text: `  ${task === undefined ? 'not read' : oneLine(task.title)}`, color: task === undefined ? t.fg.subtle : t.fg.default },
    { text: phase === '' ? '' : `  ${phase}`, color: t.fg.subtle },
  ]
}

function summaryRows(k: Kit, summary: TmHandoverSummary | undefined, width: number): RenderNode[] {
  const { t } = k
  if (summary === undefined || summary.unavailable === true) return [itemLine(k, [{ text: summary === undefined ? 'loading summary…' : 'summary unavailable', color: t.fg.subtle }], width)]
  const listOf = (items: readonly string[] | undefined): readonly string[] => (Array.isArray(items) ? items : [])
  return (
    [
      ['decisions', listOf(summary.decisions)],
      ['blockers', listOf(summary.blockers)],
    ] as const
  )
    .filter(([, items]) => items.length > 0)
    .flatMap(([name, items]) => [itemLine(k, [{ text: name, color: t.signatureText }], width), ...items.map(entry => itemLine(k, [{ text: `· ${oneLine(entry)}`, color: t.fg.default }], width))])
}

/** One handover: a collapsed item, or (focused) the boxed card with its tasks, summary and keys, or its y/n. */
function handoverEntry(k: Kit, v: HandoversView, on: HandoversHandlers, ho: TmHandover, focused: boolean, tasks: Readonly<Record<string, TmLinkedTask>>, width: number): ListEntry {
  const closed = isClosed(ho)
  const selected = v.marks.selected.includes(ho.id)
  const age = ageLabel(ho.created, v.now)
  const head: HeadSpec = {
    mark: { key: `ho-sel:${ho.id}`, selected, onPress: () => on.select(ho.id), ...(closed ? { dim: 'A closed handover is not selected for closing' } : {}) },
    badge: closed ? 'CLOSED' : 'OPEN',
    title: oneLine(ho.tldr) || ho.id,
    age: age === '' ? dateOf(ho.created).slice(5) : age,
    dim: closed,
    pick: { key: `ho:${ho.id}`, onPress: () => on.pick(ho.id) },
  }
  const lines: Seg[][] = [refsSegs(k, ho), fieldSegs(k, 'next', oneLine(ho.nextAction) || '—')]
  if (!focused) return { focused: false, nodes: item(k, head, lines, width) }
  const room = width - 4
  const ids = Array.isArray(ho.taskIds) ? ho.taskIds : []
  const open = v.summaryOpen === ho.id
  const extra: RenderNode[] = [...ids.map(id => itemLine(k, taskSegs(k, id, tasks[id], v.phaseView.phases), room)), ...(open ? summaryRows(k, v.summaries[ho.id], room) : [])]
  const ask = v.marks.ask
  const actions =
    ask !== null && ask.kind === 'one' && ask.ids[0] === ho.id
      ? askRow(k, { id: 'close', question: `close ${ho.id}?`, onYes: on.confirmClose, onNo: on.cancelClose, width: room - 1 })
      : actionRows(
          k,
          [
            actionKey(k, { id: 'resume', key: 'r', label: 'resume handover here', rung: 'primary', onPress: () => on.resume(ho) }),
            actionKey(k, { id: 'new-tab', key: 'w', label: 'resume in new tab', onPress: press => on.newTab(ho, press.surface) }),
            actionKey(k, { id: 'copy', key: 'c', label: 'copy handover', onPress: press => on.copy(ho, press.surface) }),
            actionKey(k, { id: 'summary', key: 'i', label: `decisions·blockers ${open ? '▾' : '▸'}`, onPress: () => on.toggleSummary(ho, !open) }),
            actionKey(k, { id: 'close', key: 'x', label: 'close handover', rung: 'destructive', onPress: () => on.askClose({ kind: 'one', ids: [ho.id] }), ...(closed ? { dim: 'Already closed' } : {}) }),
          ],
          room - 1,
        )
  return { focused: true, nodes: [focusedItem(k, { id: ho.id, head, lines, extra, actions }, width)] }
}

/** The handovers pane (spec §9, §15.7), one frame. */
export function handoversPaneTree(k: Kit, v: HandoversView, on: HandoversHandlers, width: number): RenderNode {
  const { Text } = k.ui
  const { t } = k
  const inner = Math.max(30, width - 4)
  const frame = (children: readonly RenderNode[]): RenderNode => paneFrame(k, { name: 'handovers', health: healthOf(v), width }, children)
  const tail: FooterItem[] = [
    { keys: 'm', word: 'sidebar', press: [{ key: 'm', onPress: on.toggleSidebar }] },
    { keys: 'esc', word: 'close' },
  ]
  const s = v.snapshot
  if (s === null || !s.reachable) {
    if (v.connecting === true) return frame([<Text color={t.fg.subtle}>{CONNECTING}</Text>, <Text> </Text>, footer(k, tail, inner)])
    if (s === null) return frame([<Text color={t.fg.subtle}>Loading…</Text>, <Text> </Text>, footer(k, tail, inner)])
    const retry = actionKey(k, { id: 'refresh', key: 'u', label: 'retry tm connection', onPress: on.refresh })
    return frame([offlineRow(k, s.reason, retry, inner), <Text> </Text>, footer(k, [{ keys: 'u', word: 'retry' }, ...tail], inner)])
  }
  const showClosed = v.marks.showClosed
  const tasks = v.list?.tasks ?? {}
  const listed = listedHandovers(v.list, s, showClosed)
  const shown = phaseHandovers(listed, v.phaseView.choice, tasks)
  const pg = handoverPage(shown, v.page, v.pick)
  const picked = pg.picked
  const strip: StripView = {
    pane: 'handovers',
    view: showClosed ? { ...v.phaseView, phases: withClosed(v.phaseView.phases) } : v.phaseView,
    allCount: allCount(v.list, s, showClosed),
    focused: v.focused,
    digits: true,
    empty: showClosed ? 'No handovers' : 'No open handovers',
  }
  const top = paneToolbar(k, {
    left: phasePicker(k, strip, on.strip),
    right: [
      togglePiece(k, { id: 'show-closed', label: '◐ show closed', on: showClosed, onPress: on.toggleClosed }),
      pagerPiece(k, { page: pg.page, pages: pg.pages, onTurn: n => on.turnPage(n, shown[n * LIST_PAGE_SIZE]?.id ?? '') }),
      refreshPiece(k, { label: 'refresh', onPress: on.refresh }),
    ],
    width: inner,
  })
  const ask = v.marks.ask
  const asking = ask !== null
  const down = picked === undefined ? null : moveTarget(shown, pg.page, picked.id, 1)
  const up = picked === undefined ? null : moveTarget(shown, pg.page, picked.id, -1)
  const keys: FooterItem[] = [
    {
      keys: 'j/k',
      word: 'move',
      press: [
        { id: 'move-down', key: 'j', onPress: () => (down === null ? k.say('Already on the last handover') : on.move(down.page, down.id)) },
        { id: 'move-up', key: 'k', onPress: () => (up === null ? k.say('Already on the first handover') : on.move(up.page, up.id)) },
      ],
    },
    {
      keys: 's',
      word: 'select',
      press: [
        {
          key: 's',
          onPress: () => {
            if (picked === undefined) return
            if (isClosed(picked)) k.say('A closed handover is not selected for closing')
            else on.select(picked.id)
          },
        },
      ],
    },
    { keys: 'b', word: 'close selected' },
    ...phaseFooter(k, strip, on.strip),
    ...(asking
      ? []
      : [
          {
            keys: 'p/n',
            word: 'page',
            press: [
              { key: 'p', onPress: () => (pg.page === 0 ? k.say('Already on the first page') : on.turnPage(pg.page - 1, shown[(pg.page - 1) * LIST_PAGE_SIZE]?.id ?? '')) },
              { key: 'n', onPress: () => (pg.page >= pg.pages - 1 ? k.say('Already on the last page') : on.turnPage(pg.page + 1, shown[(pg.page + 1) * LIST_PAGE_SIZE]?.id ?? '')) },
            ],
          },
        ]),
    { keys: 'u', word: 'refresh', press: [{ key: 'u', onPress: on.refresh }] },
    ...tail,
  ]
  if (picked === undefined) {
    const tab = orderPhases(strip.view.phases, strip.allCount, strip.view.choice).find(x => x.id === strip.view.choice)
    const way = strip.view.choice === ALL ? null : actionKey(k, { id: 'phase-all', key: '0', label: 'show all phases', onPress: () => on.strip.choose(ALL) })
    return frame([top, ...withPhaseMenu(k, strip, on.strip, [<Text> </Text>, emptyRow(k, emptyLine(strip.empty, tab), way, inner)]), <Text> </Text>, footer(k, keys, inner)])
  }
  const closable = closableSelection(v.marks.selected, shown)
  const openIds = pg.entries.filter(x => !isClosed(x)).map(x => x.id)
  const n = closable.length
  const bulk: RenderNode[] =
    ask !== null && ask.kind === 'bulk'
      ? askRow(k, { id: 'bulk-close', question: `close ${plural(ask.ids.length, 'handover')}?`, onYes: on.confirmClose, onNo: on.cancelClose, width: inner })
      : [
          bulkRow(k, {
            keys: [actionKey(k, { id: 'close-selected', key: 'b', label: `close ${plural(n, 'handover')}`, onPress: () => on.askClose({ kind: 'bulk', ids: closable }), ...(n === 0 ? { dim: 'Nothing selected to close' } : {}) })],
            scopes: [
              { id: 'select-all', label: 'all', onPress: () => on.selectOnly(openIds) },
              { id: 'select-none', label: 'none', onPress: () => on.selectOnly([]) },
            ],
            width: inner,
          }),
        ]
  const entries = pg.entries.map(ho => handoverEntry(k, v, on, ho, ho.id === picked.id, tasks, inner))
  return frame([top, ...withPhaseMenu(k, strip, on.strip, [summaryRow(k, tallyLine(shown, v.now), [], inner), ...bulk, <Text> </Text>, ...listing(k, entries, inner)]), <Text> </Text>, footer(k, keys, inner)])
}
```

In `mods/taskmaster-mods/hooks/register.tsx`, in the `tm-handovers` render: add `demo: mod.source === 'demo',` to `view`; add `toggleSidebar: () => act(f => f.toggleSidebar()),` to the handlers; and replace the return (from Task C2):

```tsx
    return withSidebar(
      k,
      <Box {...ground} flexGrow={1}>
        {tree}
      </Box>,
      await sidebarFor($, 'handovers'),
      pane => act(f => f.goTo(pane)),
    )
```

with:

```tsx
    return withSidebar(k, tree, await sidebarFor($, 'handovers'), pane => act(f => f.goTo(pane)))
```

and delete that render's now-unused `const ground = await $.rr.surfaceProps({ level: 'page' })` (and `Box` from its `$.ui.resolve(e)` destructuring if unused).

- [ ] **Step 4: Run the checks**

Expected: `tests/handovers-look.test.ts` (+5) and the double-count test (+1) pass; every migrated file passes; validate ✔; tsc clean.

- [ ] **Step 5: Commit**

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
git -C $WT add mods/taskmaster-mods/hooks mods/taskmaster-mods/tests
git -C $WT commit -m "feat(taskmaster-mods): the handovers pane on the rework vocabulary; a re-confirmed close no longer double-counts"
```

---

### Task D4: Remove what the rework replaced

**Files:**
- Modify: `mods/taskmaster-mods/hooks/kit.tsx` (delete the replaced primitives)
- Modify: `mods/taskmaster-mods/hooks/phases.ts` (delete the strip-only helpers)
- Modify: `mods/taskmaster-mods/hooks/model.ts` (delete `PAGE_SIZE` once unused)
- Delete: `mods/taskmaster-mods/tests/strip.test.ts`
- Modify: `mods/taskmaster-mods/tests/kit.test.ts`, `mods/taskmaster-mods/tests/phases.test.ts` (drop the tests of deleted functions)

**Interfaces:**
- Consumes: nothing new.
- Produces: `kit.tsx` exports exactly `Ui`, `Piece`, `BadgeTable`, `Kit`, `KitRr`, `KEYCAP_FACE`, `PANEL_TITLE`, `TM_TAG`, `HOTKEY`, `kitOf`, `stepUp`, `fit`, `row`, `label`, `signal`, `badge`, `rule`, `StripView`, `StripHandlers`. `phases.ts` keeps `ALL`, `NO_TASK`, `DONE_HIDE_OVER`, `PHASE_PANES`, `FRESH_PHASE_VIEW(S)`, `PhaseTabKind`, `PhaseTab`, `orderPhases`, `isDoneShown`, `shownTabs`, `step`, `atEnd`, `fallback`, `emptyLine`, `withCounts`, `withClosed`, `pickerName`, the store helpers. Stage E's quoted tasks (old plan 5.2–5.5, 5.7) import only `kitOf`, `KitRr`, `Ui`, `NO_TASK`, `withCounts`, `FRESH_PHASE_VIEWS`, `PhaseStore`: all kept.

- [ ] **Step 1: Delete**

From `hooks/kit.tsx` delete: `KeycapSpec`, `keycap`, `checkbox`, `confirmRow`, `PanelSpec`, `panelTitle`, `panel`, `CardSpec`, `card`, `toolbar`, `pager`, `BulkAction`, `Ghost`, `selectionBar`, `helpFooter`, `OVERLAY_PLACEMENT`, `OVERLAY_LIST`, `SELECT_ALL`, `moreLabel`, `phaseStrip`, `phaseOverlay`, `phaseEmpty`, `phaseSection`, and the imports only they used (`truncate`; `ALL`, `atEnd`, `emptyLine`, `isDoneShown`, `orderPhases`, `overlayLine`, `shownTabs`, `step`, `tabCells`, `tabLabel`, `visibleTabs`; `UiPressArgument`; `TmPhasePane` stays for `StripView`). Keep `rung` (it backs `stepUp`) and `RUNG`.

From `hooks/phases.ts` delete: `MAX_VISIBLE`, `StripFit`, `tabLabel`, `tabCells`, `visibleTabs`, `jump`, `KIND_WORD` (the phases one), `overlayLine`.

From `hooks/model.ts` delete `export const PAGE_SIZE = 5` (and fix any import that still names it to `LIST_PAGE_SIZE`).

Delete `tests/strip.test.ts`. In `tests/kit.test.ts` delete the `describe` blocks and tests for the deleted functions (keycap, checkbox, confirm row, panel, card, toolbar, pager, selection bar, help footer) and their imports; keep the tests of `badge`, `label`, `signal`, `rule`, `fit`, `row`, `stepUp`, `HOTKEY`. In `tests/phases.test.ts` delete the tests of `tabLabel`, `tabCells`, `visibleTabs`, `jump`, `overlayLine`.

- [ ] **Step 2: Prove nothing uses them**

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
grep -rnwE "keycap|checkbox|confirmRow|panelTitle|panel|selectionBar|helpFooter|phaseStrip|phaseOverlay|phaseEmpty|phaseSection|OVERLAY_PLACEMENT|OVERLAY_LIST|visibleTabs|tabCells|tabLabel|overlayLine|MAX_VISIBLE|PAGE_SIZE" $WT/mods/taskmaster-mods/hooks $WT/mods/taskmaster-mods/tests | grep -v "LIST_PAGE_SIZE\|RESTORE_PAGE_SIZE"
```

Expected: no output. (`card(` and `toolbar(` are common words in comments: grep them as `\bcard\(k` and `\btoolbar\(k` and expect no output.) The rr-tui noun's own `keycap` recipe is in `mods/rr-tui`, outside this grep.

- [ ] **Step 3: Run the checks**

Expected: all green (the deleted tests are gone, nothing else changes); validate ✔; tsc clean. A `tsc` error naming a deleted export means a caller survived: switch it to the vocabulary element this plan uses for the same thing.

- [ ] **Step 4: Commit**

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
git -C $WT add -A mods/taskmaster-mods/hooks mods/taskmaster-mods/tests
git -C $WT commit -m "refactor(taskmaster-mods): drop the kit and strip primitives the rework replaced"
```

---

### Task D5: Live check with the user (Stage D)

Orchestrator task; no subagent.

- [ ] **Step 1: Smoke** — as Task A6 Step 1; then the full suite once more on a quiet machine.
- [ ] **Step 2: Ask the user** (demo, then CodeMaestro at 120+ columns): compare Session, Review and Handovers with their artboards — the frame, row 1, the sentence and tallies, the bulk row, 3 items a page, the boxed focused item with one filled key, the footer; press `f` (the dropdown floats, digits pick), `m` (sidebar), Esc with each open; press `d` on a card and check `n` answers no (not page next); confirm the Session tab no longer clips at the right edge at the user's real width.
- [ ] **Step 3: Record** — ledger line `Task D5: Stage D live check — <words>`; this closes the old plan's Task 4.6 (Stage 4 live check), note that in the ledger. Stop until the user signs off.

---

## Stage E: Restore, built in the new style

**Goal.** The old plan's Stage 5 (spec §10): `/tm-restore [days]` and the sidebar's Restore entry show which of this repo's sessions closed together and reopen them, grouped UNFINISHED / HANDED OFF / IDLE / RUNNING NOW as `Restore.dc.html` draws them. The scanner, the tm resolution, the model and the flows are the old plan's, quoted by line range; the drawing and the wiring are rewritten here.

**How the old text is used.** `OLD` below is `docs/superpowers/plans/2026-10-11-mods-ux.md`. "Apply OLD Task 5.N (lines a–b) verbatim" means: execute every step of that task exactly as written there, with only the deltas this plan lists. Read OLD's Stage 5 preamble (lines 14244–14330) first for its rulings and file map; its "What Stage 5 consumes" list names Stage 1 kit primitives that Task D4 removed: only OLD Task 5.6 used them, and Task E6 replaces 5.6. Ignore OLD's absolute test counts ("401 + N", "468 plugin tests"): the suite is larger now; "all green, +N" is what counts. Where OLD says "the band's restore keycap" or "band `4`", read "the sidebar's Restore entry" (spec §15.5: the band has no counts); the data (`restorePending`, `restoreHidden`) is unchanged.

Checks from Task E1 on add the scanner's pytest:

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
claude plugin test $WT/mods/taskmaster-mods
claude plugin validate --strict $WT/mods/taskmaster-mods
npx -y -p typescript@5.6.3 tsc -p $WT/mods/taskmaster-mods
python -m pytest $WT/mods/taskmaster-mods/scanner -q
```

### Task E1: The scanner (`scanner/scan.py`) and its pytest

Apply OLD Task 5.1 (lines 14331–15038) verbatim. It is presentation-free and unchanged. Expected at its end: pytest `11 passed`; the plugin suite unchanged.

Commit as OLD 5.1 Step 5 writes it.

---

### Task E2: Status from tm (scan types, one `backlog_query`, classification)

Apply OLD Task 5.2 (lines 15039–15748) verbatim. Anchor note: `types/index.d.ts` now holds `TmNav` and `TmSessionInfo` before `TmRestorePending`; OLD 5.2 inserts after `TmHandoverGuard`, which is unchanged, so its anchor still matches.

---

### Task E3: Bursts, paging, ticks, resume modes — three a page, and the page's sections

Apply OLD Task 5.3 (lines 15749–16278) with these deltas.

**Delta 1 — three a page (spec §15.6).** In the code OLD 5.3 writes to `hooks/restore/model.ts`, write

```ts
export const RESTORE_PAGE_SIZE = 3
```

instead of `= 5`, and in OLD's preamble line `export const DEFAULT_DAYS = 4, SIGNAL_DAYS = 3, BURST_GAP_MIN = 10, RESTORE_PAGE_SIZE = 5` read `3`. The burst page still keeps the whole burst (spec §10).

**Delta 2 — its test.** In OLD 5.3's `tests/restore-paging.test.ts`, replace the test `page 1 is the closing burst plus what closed after; older sessions page newest first, five a page` with:

```ts
  test('page 1 is the closing burst plus what closed after; older sessions page newest first, three a page', () => {
    // Twenty minutes apart: each older session is a burst of its own.
    const older = [5, 4, 3, 2, 1, 0, -1].map(n => row(`o${n}`, n % 2 ? 'done' : 'wip', -10_000 + n * 1200))
    const rows = [...ROWS, ...older]
    const pages = pagesOf(rows, burstsOf(rows), '')
    expect(pages.map(p => p.isBurst)).toEqual([true, false, false, false])
    expect(pages[0]).toMatchObject({ burstSize: 3, since: 1, from: 980, to: 2000 })
    expect(pageRows(rows, pages[0]).map(r => r.s.id)).toEqual(['b', 'solo', 'c', 'a'])
    expect(pageRows(rows, pages[1]).map(r => r.s.id)).toEqual(['o5', 'o4', 'o3'])
    expect(pageRows(rows, pages[2]).map(r => r.s.id)).toEqual(['o2', 'o1', 'o0'])
    expect(pageRows(rows, pages[3]).map(r => r.s.id)).toEqual(['o-1'])
    expect(pagesOf([], [], '')).toEqual([])
    expect(pageRows(rows, undefined)).toEqual([])
  })
```

**Delta 3 — the page's sections.** Append to `hooks/restore/model.ts` (after the paging section OLD 5.3 appends):

```ts

// ── The page's sections (spec §15.6, Restore.dc.html): unfinished, handed off, idle ───────────────────────────────

export type RestoreSection = 'unfinished' | 'handed-off' | 'idle'
export const SECTION_ORDER: readonly RestoreSection[] = ['unfinished', 'handed-off', 'idle']
export const SECTION_OF: Readonly<Record<RestoreStatus, RestoreSection>> = {
  interrupted: 'unfinished',
  wip: 'unfinished',
  unknown: 'unfinished',
  'handed-off': 'handed-off',
  done: 'idle',
}
export const SECTION_LABEL: Readonly<Record<RestoreSection, string>> = { unfinished: 'UNFINISHED', 'handed-off': 'HANDED OFF', idle: 'IDLE' }
export const SECTION_NOTE: Readonly<Record<RestoreSection, string>> = {
  unfinished: 'interrupted or mid-work, no handover',
  'handed-off': 'an open handover carries the next step',
  idle: 'done or ended cleanly',
}

/** A page's rows by section, in section order, each keeping the page's order; empty sections left out. */
export function sectionsOf(rows: readonly RestoreRow[]): { readonly section: RestoreSection; readonly rows: readonly RestoreRow[] }[] {
  return SECTION_ORDER.map(section => ({ section, rows: rows.filter(r => SECTION_OF[r.status] === section) })).filter(s => s.rows.length > 0)
}
```

and append to `tests/restore-paging.test.ts`:

```ts
describe('sections', () => {
  test('a page groups as unfinished (interrupted, wip, ?), handed off, idle; empty ones left out; order kept', () => {
    const rows = [row('i', 'interrupted', 5), row('h', 'handed-off', 4), row('u', 'unknown', 3), row('w', 'wip', 2)]
    expect(sectionsOf(rows).map(s => [s.section, s.rows.map(r => r.s.id)])).toEqual([
      ['unfinished', ['i', 'u', 'w']],
      ['handed-off', ['h']],
    ])
    expect(sectionsOf([])).toEqual([])
  })
})
```

adding `sectionsOf` to that file's import from `../hooks/restore/model`.

Commit as OLD 5.3 Step 5 writes it, with the message `feat(taskmaster-mods): restore bursts, burst-first paging three a page, sections, default ticks, resume modes`.

---

### Task E4: The restore flows

Apply OLD Task 5.4 (lines 16279–16910) verbatim (it bumps `DEMO_SEED` to 8; no stage of this plan changed demo data, so its `toBe(7)` anchor holds).

---

### Task E5: Resume (fresh or --resume), resumed-from records, copy, Brief

Apply OLD Task 5.5 (lines 16911–17443) verbatim.

---

### Task E6: Drawing the restore pane in the new style (replaces OLD Task 5.6)

**Files:**
- Create: `mods/taskmaster-mods/hooks/restore/draw.tsx`
- Create: `mods/taskmaster-mods/tests/restore-draw.test.ts`

**Interfaces:**
- Consumes: Tasks E2–E5's model (`RestoreRow`, `RestoreStatus`, `burstsOf`, `pagesOf`, `pageRows`, `pageHeader`, `runningRows`, `blockedWhy`, `isTicked`, `resumeModeOf`, `detailOf`, `formatTokens`, `ctxTone`, `hhmm`, `when`, `sectionsOf`, `SECTION_LABEL`, `SECTION_NOTE`, `STATUS_ORDER`), the types `TmRestoreData`, `TmRestoreView`; the vocabulary; `demoRestore`, `demoPhases` in tests.
- Produces (OLD 5.7's wiring and this plan's Task E7 call these; names kept from OLD 5.6): `restorePaneTree(k: Kit, v: RestorePaneView, on: RestoreHandlers, width: number): RenderNode`; `RestorePaneView = { data: TmRestoreData; view: TmRestoreView; rows: readonly RestoreRow[]; strip: StripView; now: number /* epoch seconds */; source: 'tm' | 'demo' }`; `RestoreHandlers = { strip; turnPage(page); rescan(); toggleCompact(); focus(id); toggle(id); select(changes); toggleMode(id); resume(ids); toggleBrief(id); brief(ids); copy(id); toggleSidebar() }`. Element ids: `sel:<id>` (mark), `resume:<id>` r (primary), `mode:<id>` v, `brief:<id>` i, `copy:<id>` c, `restore-resume-all` b, `restore-brief-all` g, `restore-all`, `restore-unfinished`, `restore-none`, `restore-compact` (toolbar toggle, label `◐ compact` / `○ compact`), `refresh` (`u rescan`), footer `restore-down` j, `restore-up` k, `key:s`, `key:z`, `key:h`, `key:l`, `key:f`, `key:p`, `key:n`, `key:u`, `key:m`.

- [ ] **Step 1: Write the failing tests**

`mods/taskmaster-mods/tests/restore-draw.test.ts`:

```ts
// User intent: pin the restore pane as Restore.dc.html draws it (spec §10, §15.6–15.7) — the burst page grouped UNFINISHED /
// HANDED OFF / IDLE then RUNNING NOW, 3-line items with project · branch · cwd · ctx and the bound task, ticks marked ■, the
// focused one boxed with r resume session filled, the fresh / --resume field and v, the bulk row with counts, one dim footer.
import { describe, expect, test } from 'claude-code/testing'

import type { TmRestoreData, TmRestoreView } from '../types'
import { demoPhases, demoRestore } from '../hooks/demo'
import type { StripView } from '../hooks/kit'
import { FRESH_PHASE_VIEW, withCounts } from '../hooks/phases'
import { type RestoreHandlers, restorePaneTree } from '../hooks/restore/draw'
import { FRESH_RESTORE_DATA, FRESH_RESTORE_VIEW, paneRows, phaseCounts } from '../hooks/restore/model'
import { byKey, testKit, textsOf, walk } from './fixtures/kit-ui'
import { elementsOf, widthOf } from './fixtures/measure'
import { STUB_TOKENS as T } from './fixtures/tokens'

const NOW_MS = Date.UTC(2026, 9, 10, 16, 43)
const noop = (): void => undefined
const ON: RestoreHandlers = {
  strip: { choose: noop, toggleDone: noop, overlay: noop },
  turnPage: noop,
  rescan: noop,
  toggleCompact: noop,
  focus: noop,
  toggle: noop,
  select: noop,
  toggleMode: noop,
  resume: noop,
  toggleBrief: noop,
  brief: noop,
  copy: noop,
  toggleSidebar: noop,
}

function draw(view: Partial<TmRestoreView> = {}, data: Partial<TmRestoreData> = {}, width = 116) {
  const full: TmRestoreData = { ...FRESH_RESTORE_DATA, ...demoRestore(NOW_MS), ...data }
  const rows = paneRows(full, 'this-session')
  const strip: StripView = {
    pane: 'restore',
    view: { ...FRESH_PHASE_VIEW, phases: withCounts(demoPhases('restore'), phaseCounts(rows)), loaded: true },
    allCount: rows.filter(r => !r.s.running).length,
    focused: true,
    digits: true,
    empty: 'No sessions',
  }
  return restorePaneTree(testKit(), { data: full, view: { ...FRESH_RESTORE_VIEW, ...view }, rows, strip, now: NOW_MS / 1000, source: 'demo' }, ON, width)
}
const keysOf = (tree: unknown): Record<string, string[]> => {
  const keys: Record<string, string[]> = {}
  for (const b of walk(tree).filter(e => e.type === 'Button')) if (typeof b.props?.hotkey === 'string') (keys[b.props.hotkey] ??= []).push(String(b.props.key))
  return keys
}

describe('the restore pane, as drawn', () => {
  test('the frame names RESTORE; row 1 has the picker, ◐ compact, the pager and u rescan; row 2 the burst sentence', () => {
    const tree = draw()
    expect(walk(tree)[0]?.props).toMatchObject({ borderStyle: 'round', borderColor: T.border.default })
    const texts = textsOf(tree)
    for (const text of ['RESTORE', 'demo', '○ compact', 'u', ' rescan']) expect(texts, text).toContain(text)
    expect(texts.some(text => /closed together/.test(text))).toBe(true)
    expect(byKey(tree, 'restore-compact')?.props?.hotkey).toBeUndefined()
    expect(byKey(tree, 'refresh')?.props?.label).toBe('u rescan')
  })

  test('sections in order, each under its section row; RUNNING NOW last', () => {
    const texts = textsOf(draw())
    const at = (word: string) => texts.indexOf(word)
    for (const word of ['UNFINISHED', 'RUNNING NOW']) expect(at(word), word).toBeGreaterThan(-1)
    expect(at('UNFINISHED')).toBeLessThan(at('RUNNING NOW'))
    if (at('HANDED OFF') > -1) expect(at('UNFINISHED')).toBeLessThan(at('HANDED OFF'))
    if (at('IDLE') > -1) expect(at('HANDED OFF') === -1 || at('HANDED OFF') < at('IDLE')).toBe(true)
  })

  test('the focused session is boxed with r resume session filled, v, i and c; collapsed ones carry no keys', () => {
    const tree = draw()
    const boxes = walk(tree).filter(e => e.type === 'Box' && e.props?.borderStyle === 'round' && String(e.props?.key ?? '').startsWith('card:'))
    expect(boxes).toHaveLength(1)
    const id = String(boxes[0]?.props?.key).slice(5)
    expect(byKey(tree, `resume:${id}`)?.props).toMatchObject({ hotkey: 'r', label: 'resume session' })
    expect(byKey(tree, `resume:${id}-box`)?.props?.backgroundColor).toBe(T.keycap.signature.bg)
    expect(byKey(tree, `mode:${id}`)?.props).toMatchObject({ hotkey: 'v', label: 'switch resume mode' })
    expect(byKey(tree, `brief:${id}`)?.props).toMatchObject({ hotkey: 'i', label: 'brief session' })
    expect(byKey(tree, `copy:${id}`)?.props).toMatchObject({ hotkey: 'c', label: 'copy resume command' })
    expect(walk(tree).filter(e => e.type === 'Button' && String(e.props?.key ?? '').startsWith('resume:'))).toHaveLength(1)
  })

  test('the bulk row counts the ticked sessions; compact renames resume; scopes all · unfinished · none', () => {
    const tree = draw()
    const resumeAll = byKey(tree, 'restore-resume-all')?.props
    expect(resumeAll).toMatchObject({ hotkey: 'b' })
    expect(String(resumeAll?.label)).toMatch(/^resume \d+ sessions?$/)
    expect(String(byKey(tree, 'restore-brief-all')?.props?.label)).toMatch(/^brief \d+ sessions?$/)
    for (const key of ['restore-all', 'restore-unfinished', 'restore-none']) expect(byKey(tree, key)?.props?.hotkey, key).toBeUndefined()
    expect(String(byKey(draw({ compact: true }), 'restore-resume-all')?.props?.label)).toMatch(/^resume \+ compact \d+ sessions?$/)
    expect(textsOf(draw({ compact: true }))).toContain('◐ compact')
  })

  test('the footer owns j k s z h l f p n u m; no hotkey on two Buttons; no digit outside the open list', () => {
    const keys = keysOf(draw())
    for (const [hotkey, id] of [['j', 'restore-down'], ['k', 'restore-up'], ['s', 'key:s'], ['z', 'key:z'], ['u', 'key:u'], ['m', 'key:m']] as const) expect(keys[hotkey], hotkey).toEqual([id])
    expect(Object.entries(keys).filter(([, ids]) => ids.length > 1)).toEqual([])
    expect(Object.keys(keys).filter(key => /^\d$/.test(key))).toEqual([])
  })

  test('a failed scan, no scan yet, and tm offline each say so in one row', () => {
    expect(textsOf(draw({}, { error: 'Scan failed: OSError: no projects folder', scan: null }))).toEqual(expect.arrayContaining(['▲ scan failed', ' · OSError: no projects folder']))
    expect(textsOf(draw({}, { scan: null, refs: null }))).toContain('No scan yet: u scans this repo’s sessions')
  })

  test('every row fits at 60 and 100 columns', () => {
    for (const columns of [60, 100]) {
      const rows = elementsOf(draw({}, {}, columns)).filter(e => e.type === 'Box' && e.props?.flexDirection === 'row' && e.props?.position !== 'absolute')
      for (const row of rows) expect(widthOf(row), `${columns}`).toBeLessThanOrEqual(columns - 4)
    }
  })
})
```

- [ ] **Step 2: Run the suite to see the new tests fail**

Expected: `tests/restore-draw.test.ts` fails to load (`../hooks/restore/draw` missing).

- [ ] **Step 3: Implement**

`mods/taskmaster-mods/hooks/restore/draw.tsx`:

```tsx
// User intent: the restore pane drawn in the rework's vocabulary (spec §10, §15.6–15.7, Restore.dc.html) — the burst page
// grouped UNFINISHED / HANDED OFF / IDLE then RUNNING NOW, 3-line items, the focused one boxed with r resume session filled and
// its fresh / --resume field, the bulk row with counts, one dim footer — so a burst of sessions reopens in one press.
// JSX compiles to h(...): never name a variable `h` in this file (it would shadow the factory).
import type { RenderNode } from 'claude-code'

import type { TmRestoreData, TmRestoreView } from '../../types'
import type { Kit, StripHandlers, StripView } from '../kit'
import { truncate } from '../model'
import {
  actionKey,
  actionRows,
  badgeSeg,
  bulkRow,
  CONTENT_COL,
  emptyRow,
  focusedItem,
  footer,
  type FooterItem,
  type HeadSpec,
  type Health,
  item,
  itemLine,
  line,
  type ListEntry,
  listing,
  pagerPiece,
  paneFrame,
  paneToolbar,
  phaseFooter,
  phasePicker,
  refreshPiece,
  sectionRow,
  type Seg,
  statusBadgeWord,
  summaryRow,
  togglePiece,
  withPhaseMenu,
} from '../vocab'
import {
  blockedWhy,
  burstsOf,
  ctxTone,
  detailOf,
  formatTokens,
  hhmm,
  isTicked,
  pageHeader,
  pageRows,
  pagesOf,
  type RestoreRow,
  type RestoreStatus,
  resumeModeOf,
  runningRows,
  SECTION_LABEL,
  SECTION_NOTE,
  sectionsOf,
  STATUS_ORDER,
  when,
} from './model'

export type RestorePaneView = {
  readonly data: TmRestoreData
  readonly view: TmRestoreView
  /** The rows the pane lists (paneRows: this session left out), computed once with the strip's counts. */
  readonly rows: readonly RestoreRow[]
  readonly strip: StripView
  /** Epoch seconds. */
  readonly now: number
  readonly source: 'tm' | 'demo'
}

export type RestoreHandlers = {
  readonly strip: StripHandlers
  readonly turnPage: (page: number) => void
  readonly rescan: () => void
  readonly toggleCompact: () => void
  readonly focus: (id: string) => void
  readonly toggle: (id: string) => void
  readonly select: (changes: Readonly<Record<string, boolean>>) => void
  readonly toggleMode: (id: string) => void
  readonly resume: (ids: readonly string[]) => void
  readonly toggleBrief: (id: string) => void
  readonly brief: (ids: readonly string[]) => void
  readonly copy: (id: string) => void
  readonly toggleSidebar: () => void
}

const BADGE_OF: Readonly<Record<RestoreStatus, string>> = {
  interrupted: 'INTERRUPTED',
  wip: 'WIP',
  unknown: 'UNKNOWN',
  'handed-off': 'HANDED OFF',
  done: 'DONE',
}
const plural = (n: number, word: string): string => `${n} ${word}${n === 1 ? '' : 's'}`
const baseName = (path: string): string => path.replace(/[\\/]+$/, '').split(/[\\/]/).pop() ?? path

function healthOf(v: RestorePaneView): Health {
  if (v.source === 'demo') return { kind: 'info', word: 'demo' }
  if (v.data.error !== '') return { kind: 'critical', word: 'scan failed' }
  if (v.data.scan !== null && v.data.refs === null && v.view.busy === '') return { kind: 'critical', word: 'tm offline' }
  return { kind: 'success', word: 'tm ok' }
}

/** Line 2: `taskmaster · feat/x · .worktrees/x · 512k ctx` (ctx warning past 400k, critical past 700k). */
function whereSegs(k: Kit, r: RestoreRow, root: string, dim: boolean): Seg[] {
  const { t } = k
  const cwd = r.s.cwd === root ? 'repo root' : r.s.cwd.startsWith(root) ? r.s.cwd.slice(root.length).replace(/^[\\/]+/, '').replace(/\\/g, '/') : r.s.cwd
  const tone = ctxTone(r.s.ctx)
  return [
    { text: `${baseName(root)} · ${r.s.branch || 'no branch'} · ${cwd}`, color: dim ? t.border.strong : t.fg.subtle },
    { text: r.s.ctx > 0 ? ` · ${formatTokens(r.s.ctx)} ctx` : '', color: dim ? t.border.strong : tone === null ? t.fg.subtle : t.tone[tone] },
  ]
}

/** Line 3: the bound task (`● IN PROGRESS  tm-audit-033 · <detail>`), else the detail as a field. */
function taskSegs(k: Kit, r: RestoreRow, dim: boolean): Seg[] {
  const { t } = k
  const detail = detailOf(r)
  const words = `${detail.label.toLowerCase()} "${detail.text}"`
  if (r.task !== null) {
    return [
      dim ? { text: `· ${statusBadgeWord(r.task.status)}`, color: t.border.strong } : badgeSeg(k, statusBadgeWord(r.task.status)),
      { text: `  ${r.task.id}`, color: dim ? t.border.strong : t.fg.bold, bold: !dim },
      { text: ` · ${words}`, color: dim ? t.border.strong : t.fg.subtle },
    ]
  }
  return [
    { text: r.boundId === '' ? 'no task touched' : r.boundId, color: dim ? t.border.strong : t.fg.disabled },
    { text: ` · ${words}`, color: dim ? t.border.strong : t.fg.subtle },
  ]
}

function headOf(r: RestoreRow, v: RestorePaneView, on: RestoreHandlers): HeadSpec {
  const why = blockedWhy(r)
  return {
    mark: r.s.running ? null : { key: `sel:${r.s.id}`, selected: isTicked(r, v.view.selected, v.view.opened), onPress: () => on.toggle(r.s.id), ...(why === '' ? {} : { dim: why }) },
    badge: r.s.running ? 'RUNNING' : BADGE_OF[r.status],
    title: r.s.title,
    age: r.s.running ? when(r.s.closedEpoch, v.now) : hhmm(r.s.closedEpoch),
    dim: r.status === 'done' && !r.s.running,
  }
}

/** The focused session: where, task, handover, detail, the fresh / --resume field, its brief and marks, then r / i / c. */
function focusedCard(k: Kit, r: RestoreRow, v: RestorePaneView, on: RestoreHandlers, root: string, width: number): RenderNode {
  const { Box, Text } = k.ui
  const { t } = k
  const id = r.s.id
  const room = width - 4
  const lines: Seg[][] = [whereSegs(k, r, root, false)]
  if (r.task !== null) {
    lines.push([{ text: 'task  ', color: t.signatureText }, badgeSeg(k, statusBadgeWord(r.task.status)), { text: `  ${r.task.id}`, color: t.fg.bold, bold: true }, { text: r.phase === '' ? '' : ` · ${r.phase}`, color: t.fg.subtle }])
  }
  if (r.handover !== null) {
    lines.push([{ text: 'handover  ', color: t.signatureText }, { text: r.handover.id, color: t.fg.bold, bold: true }, { text: ` · next: ${r.handover.next || r.handover.tldr}`, color: t.fg.default }])
  }
  const detail = detailOf(r)
  lines.push([{ text: `${detail.label.toLowerCase()}  `, color: t.signatureText }, { text: detail.text, color: t.fg.default }])
  const mode = resumeModeOf(r, v.view.modes)
  const fresh = mode === 'fresh' && r.handover !== null
  const chosen = fresh ? ` fresh from ${truncate(r.handover?.id ?? '', 28)} ` : ' --resume '
  const other = fresh ? '  --resume' : r.handover === null ? '' : `  fresh from ${truncate(r.handover.id, 20)}`
  const switchKey = actionKey(k, {
    id: `mode:${id}`,
    key: 'v',
    label: 'switch resume mode',
    onPress: () => on.toggleMode(id),
    ...(r.handover === null ? { dim: `${r.s.title} wrote no handover: it reopens with --resume` } : {}),
  })
  // `v switch resume mode` sits on the via row when it fits there, else it joins the action row after `r`.
  const chosenText = truncate(chosen, Math.max(6, room - CONTENT_COL - 5))
  const inline = CONTENT_COL + 5 + chosenText.length + 3 + switchKey.width <= room
  const otherRoom = room - CONTENT_COL - 5 - chosenText.length - (inline ? 3 + switchKey.width : 0)
  const extra: RenderNode[] = [
    <Box paddingLeft={CONTENT_COL} flexDirection="row">
      <Text color={t.signatureText}>via  </Text>
      <Text backgroundColor={t.surface.overlay} color={t.fg.bold}>
        {chosenText}
      </Text>
      <Text color={t.fg.disabled}>{truncate(other, Math.max(0, otherRoom))}</Text>
      {inline ? <Text>{'   '}</Text> : null}
      {inline ? switchKey.el : null}
    </Box>,
  ]
  if (v.view.briefing.includes(id)) extra.push(itemLine(k, [{ text: 'Briefing…', color: t.tone.info }], room))
  const brief = v.view.briefs[id]
  if (brief !== undefined) {
    for (const entry of brief.split('\n')) {
      const [head, ...rest] = entry.split(': ')
      extra.push(itemLine(k, rest.length === 0 ? [{ text: entry, color: t.fg.default }] : [{ text: `${(head ?? '').toLowerCase()}  `, color: t.signatureText }, { text: rest.join(': '), color: t.fg.default }], room))
    }
  }
  const openedAt = v.view.opened[id]
  const marks = [
    ...(openedAt === undefined ? [] : [`opened ${when(openedAt, v.now)}`]),
    ...(r.s.resumedAfterClose && !r.s.running ? ['resumed since'] : []),
    ...(r.s.cwdExists ? [] : ['folder missing']),
  ]
  if (marks.length > 0) extra.push(itemLine(k, [{ text: marks.join(' · '), color: r.s.cwdExists ? t.fg.subtle : t.tone.critical }], room))
  const why = blockedWhy(r)
  const launching = v.view.launching.includes(id)
  const actions = actionRows(
    k,
    [
      actionKey(k, { id: `resume:${id}`, key: 'r', label: 'resume session', rung: 'primary', onPress: () => on.resume([id]), ...(why !== '' ? { dim: why } : launching ? { dim: `${r.s.title} is opening` } : {}) }),
      ...(inline ? [] : [switchKey]),
      actionKey(k, { id: `brief:${id}`, key: 'i', label: brief === undefined ? 'brief session' : 'hide brief', onPress: () => on.toggleBrief(id), ...(v.view.briefing.includes(id) ? { dim: `Briefing ${r.s.title}…` } : {}) }),
      actionKey(k, { id: `copy:${id}`, key: 'c', label: 'copy resume command', onPress: () => on.copy(id) }),
    ],
    room - 1,
  )
  return focusedItem(k, { id, head: headOf(r, v, on), lines, extra, actions }, width)
}

/** The whole pane in its frame (`◆ TASKMASTER · RESTORE`), `width` cells across. */
export function restorePaneTree(k: Kit, v: RestorePaneView, on: RestoreHandlers, width: number): RenderNode {
  const { Text } = k.ui
  const { t } = k
  const inner = Math.max(30, width - 4)
  const rows = v.rows
  const pages = pagesOf(rows, burstsOf(rows), v.strip.view.choice)
  const at = Math.min(Math.max(0, v.view.page), Math.max(0, pages.length - 1))
  const page = pages[at]
  const list = pageRows(rows, page)
  const focusId = list.some(r => r.s.id === v.view.focus) ? v.view.focus : (list[0]?.s.id ?? '')
  const ticked = list.filter(r => isTicked(r, v.view.selected, v.view.opened)).map(r => r.s.id)
  const running = runningRows(rows)
  const root = v.data.scan?.root ?? ''
  const index = list.findIndex(r => r.s.id === focusId)

  const top = paneToolbar(k, {
    left: phasePicker(k, v.strip, on.strip),
    right: [
      togglePiece(k, { id: 'restore-compact', label: `${v.view.compact ? '◐' : '○'} compact`, on: v.view.compact, onPress: on.toggleCompact }),
      pagerPiece(k, { page: at, pages: pages.length, onTurn: on.turnPage }),
      refreshPiece(k, { label: 'rescan', onPress: on.rescan }),
    ],
    width: inner,
  })
  const keys: FooterItem[] = [
    {
      keys: 'j/k',
      word: 'move',
      press: [
        { id: 'restore-down', key: 'j', onPress: () => (index < 0 || index >= list.length - 1 ? k.say('Already on the last session') : on.focus(list[index + 1]?.s.id ?? focusId)) },
        { id: 'restore-up', key: 'k', onPress: () => (index <= 0 ? k.say('Already on the first session') : on.focus(list[index - 1]?.s.id ?? focusId)) },
      ],
    },
    { keys: 's', word: 'tick', press: [{ key: 's', onPress: () => (focusId === '' ? undefined : on.toggle(focusId)) }] },
    { keys: 'v', word: 'resume mode' },
    ...phaseFooter(k, v.strip, on.strip),
    {
      keys: 'p/n',
      word: 'page',
      press: [
        { key: 'p', onPress: () => (at === 0 ? k.say('Already on the first page') : on.turnPage(at - 1)) },
        { key: 'n', onPress: () => (at >= pages.length - 1 ? k.say('Already on the last page') : on.turnPage(at + 1)) },
      ],
    },
    { keys: 'z', word: 'compact', press: [{ key: 'z', onPress: on.toggleCompact }] },
    { keys: 'u', word: 'rescan', press: [{ key: 'u', onPress: on.rescan }] },
    { keys: 'm', word: 'sidebar', press: [{ key: 'm', onPress: on.toggleSidebar }] },
    { keys: 'esc', word: 'close' },
  ]

  const body: RenderNode[] = []
  if (v.data.error !== '') {
    body.push(line(k, [{ text: '▲ scan failed', color: t.tone.critical, bold: true }, { text: ` · ${v.data.error.replace(/^Scan failed: /, '')}`, color: t.fg.subtle }], inner).el)
  } else if (v.data.scan === null) {
    body.push(emptyRow(k, v.view.busy !== '' ? v.view.busy : 'No scan yet: u scans this repo’s sessions', null, inner))
  } else {
    if (v.source === 'tm' && v.data.refs === null && v.view.busy === '') {
      body.push(line(k, [{ text: '▲ tm offline', color: t.tone.critical, bold: true }, { text: ' · statuses read ? · resume still works', color: t.fg.subtle }], inner).el)
    }
    const tallies = STATUS_ORDER.map(status => ({ word: BADGE_OF[status as RestoreStatus], count: list.filter(r => r.status === status).length })).filter(x => x.count > 0)
    body.push(summaryRow(k, page === undefined ? 'No sessions in this window' : pageHeader(page, v.now), tallies, inner))
    const n = ticked.length
    body.push(
      bulkRow(k, {
        keys: [
          actionKey(k, { id: 'restore-resume-all', key: 'b', label: `${v.view.compact ? 'resume + compact' : 'resume'} ${plural(n, 'session')}`, onPress: () => on.resume(ticked), ...(n === 0 ? { dim: 'Nothing ticked to resume' } : {}) }),
          actionKey(k, { id: 'restore-brief-all', key: 'g', label: `brief ${plural(n, 'session')}`, onPress: () => on.brief(ticked), ...(n === 0 ? { dim: 'Nothing ticked to brief' } : {}) }),
        ],
        scopes: [
          { id: 'restore-all', label: 'all', onPress: () => on.select(Object.fromEntries(list.map(r => [r.s.id, blockedWhy(r) === '']))) },
          { id: 'restore-unfinished', label: 'unfinished', onPress: () => on.select(Object.fromEntries(list.map(r => [r.s.id, blockedWhy(r) === '' && r.status !== 'done']))) },
          { id: 'restore-none', label: 'none', onPress: () => on.select(Object.fromEntries(list.map(r => [r.s.id, false]))) },
        ],
        width: inner,
      }),
    )
    if (v.view.busy !== '') body.push(line(k, [{ text: v.view.busy, color: t.tone.info }], inner).el)
    body.push(<Text> </Text>)
    if (list.length === 0) body.push(emptyRow(k, 'No sessions under this phase', actionKey(k, { id: 'phase-all', key: '0', label: 'show all phases', onPress: () => on.strip.choose('') }), inner))
    for (const group of sectionsOf(list)) {
      body.push(sectionRow(k, SECTION_LABEL[group.section], `${group.rows.length} · ${SECTION_NOTE[group.section]}`, inner))
      const entries: ListEntry[] = group.rows.map(r =>
        r.s.id === focusId
          ? { focused: true, nodes: [focusedCard(k, r, v, on, root, inner)] }
          : { focused: false, nodes: item(k, headOf(r, v, on), [whereSegs(k, r, root, r.status === 'done'), taskSegs(k, r, r.status === 'done')], inner) },
      )
      body.push(...listing(k, entries, inner), <Text> </Text>)
    }
    if (running.length > 0) {
      body.push(sectionRow(k, 'RUNNING NOW', `${running.length} · open in other terminals, not resumable here`, inner))
      body.push(...listing(k, running.map(r => ({ focused: false, nodes: item(k, headOf(r, v, on), [whereSegs(k, r, root, false)], inner) })), inner))
    }
  }
  return paneFrame(k, { name: 'restore', health: healthOf(v), width }, [top, ...withPhaseMenu(k, v.strip, on.strip, body), <Text> </Text>, footer(k, keys, inner)])
}
```

`when` and `hhmm` take epoch **seconds** (OLD 5.3); `v.now` is seconds too. Running rows have no mark (not restorable), as OLD 5.6 drew them.

- [ ] **Step 4: Run every check**

Expected: `tests/restore-draw.test.ts` passes (+7); validate ✔; tsc clean; pytest `11 passed` (plus the E3 sections test in the plugin suite).

- [ ] **Step 5: Commit**

```bash
WT=C:/Users/gruku/Files/Claude/taskmaster/.worktrees/mods-ux
git -C $WT add mods/taskmaster-mods/hooks/restore/draw.tsx mods/taskmaster-mods/tests/restore-draw.test.ts
git -C $WT commit -m "feat(taskmaster-mods): restore pane drawing in the rework style — sections, 3-line items, r resume session, the resume-mode field"
```

---

### Task E7: Wiring the restore pane (OLD Task 5.7 with deltas)

Apply OLD Task 5.7 (lines 17962–18602) with these deltas. Everything not listed is verbatim (world.ts `answer`, `onRestore`'s commands and session-start scan, `refreshRestore`, flows `refresh('restore')`, register's three lines, `briefModel`, the PluginState keys).

**Delta 1 — imports in `hooks/restore/hooks.tsx`.** Replace OLD's

```tsx
import type { TmPhasePane, TmPhaseView, TmRestorePending, TmSessionBurst } from '../../types'
```

with

```tsx
import type { TmNav, TmPhasePane, TmPhaseView, TmRestorePending, TmSessionBurst, TmSnapshot } from '../../types'
```

and OLD's

```tsx
import { kitOf, type KitRr, type Ui } from '../kit'
import { RESTORE } from '../model'
```

with

```tsx
import { kitOf, type KitRr, type Ui } from '../kit'
import { RESTORE } from '../model'
import { FRESH_NAV, sidebarView } from '../nav'
import { withSidebar } from '../vocab'
```

and after OLD's `const RR_POLARITY = { plugin: 'rr-tui', key: 'polarity' } as const`, add (second atoms on keys register.tsx also reads, as OLD's `PENDING` and `BURST` are; the validator accepts them):

```tsx
const NAV = atom({ plugin: 'taskmaster-mods', key: 'nav' } as const, FRESH_NAV as TmNav)
const SNAPSHOT = atom({ plugin: 'taskmaster-mods', key: 'snapshot' } as const, null as TmSnapshot | null)
```

**Delta 2 — the restore pane opens through the one-pane rule (spec §15.8 S2).** In OLD's `depsOf`, replace

```tsx
    openPane: async () => (await $.ui.open({ id: RESTORE, title: 'Restore', focus: true, closeOnEscape: true })).isPlaced,
```

with

```tsx
    // Through the main flows when they exist: the other Taskmaster panes close first and Restore becomes the band's last pane.
    openPane: async () => {
      const main = rs.link?.flows() ?? null
      if (main === null) return (await $.ui.open({ id: RESTORE, title: 'Restore', focus: true, closeOnEscape: true })).isPlaced
      await main.openPane('restore')
      return true
    },
```

**Delta 3 — the pane render.** In OLD's `ui.render` hook for `{ component: 'Pane', requestId: RESTORE }`, replace from `const k = await kitOf(` to the end of that hook's `return (…)` with:

```tsx
    const k = await kitOf($.ui.resolve(e) as unknown as Ui, kitRrOf($), e.surface, sayOf($))
    const tree = restorePaneTree(
      k,
      { data, view: await read($, VIEW), rows, strip, now: now / 1000, source: rs.source },
      {
        strip: {
          // A new phase starts at its page 1, as review and handovers do.
          choose: id => {
            actMain(f => f.choosePhase('restore', id))
            act(f => f.turnPage(0))
          },
          toggleDone: () => actMain(f => f.toggleDone('restore')),
          overlay: open => actMain(f => f.setOverlay('restore', open)),
        },
        turnPage: page => act(f => f.turnPage(page)),
        rescan: () => actMain(f => f.refresh('restore')),
        toggleCompact: () => act(f => f.toggleCompact()),
        focus: id => act(f => f.focus(id)),
        toggle: id => act(f => f.toggle(id)),
        select: changes => act(f => f.select(changes)),
        toggleMode: id => act(f => f.toggleMode(id)),
        resume: ids => act(f => f.resume(ids)),
        toggleBrief: id => act(f => f.toggleBrief(id)),
        brief: ids => act(f => f.brief(ids)),
        copy: id => act(f => f.copy(id)),
        toggleSidebar: () => actMain(f => f.toggleSidebar()),
      },
      e.props.bodyColumns,
    )
    const nav = await read($, NAV)
    const sidebar = nav.sidebar ? sidebarView({ current: 'restore', snapshot: await read($, SNAPSHOT), restore: await read($, PENDING), now }) : null
    return withSidebar(k, tree, sidebar, pane => actMain(f => f.goTo(pane)))
```

(`Box` is then unused in that hook's `$.ui.resolve(e)` destructuring: keep only `Text`.)

**Delta 4 — Restore is built.** In `mods/taskmaster-mods/hooks/nav.ts`, replace

```ts
export const RESTORE_BUILT = false
```

with

```ts
export const RESTORE_BUILT = true
```

and in `tests/sidebar.test.ts`, replace

```ts
    expect((await ui.find({ type: 'Button', key: 'nav:restore' }))?.props?.dimColor).toBe(true)
```

with

```ts
    expect((await ui.find({ type: 'Button', key: 'nav:restore' }))?.props?.dimColor).toBeUndefined()
```

**Delta 5 — OLD's `tests/restore-pane.test.ts`, migrated.** Write OLD's file with these replacements:

| OLD | New |
|-----|-----|
| `` `the session-start scan feeds the band's restore keycap` `` (header comment) and the test name `… writes the band keycap and the burst line` | `… feeds the sidebar's Restore entry`; `… writes the Restore entry's burst and the burst line` (assertions unchanged) |
| `restore-resume-all` `{ hotkey: 'b', label: 'Resume 4' }` | `{ hotkey: 'b', label: 'resume 4 sessions' }` |
| label `'Resume 2'`, `'Resume 1'` | `'resume 2 sessions'`, `'resume 1 session'` |
| Text `'Running now · 1 · open in other terminals'` | Text `'RUNNING NOW'` |
| Text `' 1 INTERRUPTED '`, `' 1 HANDED OFF '` | Texts `'▲ INTERRUPTED'` and `'● HANDED OFF'` (each followed by Text `' 1'`) |
| `label('mode:u5')` `'○ --resume'`, then after the press `'◐ fresh from h-1'` | `label('mode:u5')` is `'switch resume mode'` both times; the current mode is the Text `' --resume '`, then `' fresh from h-1 '` |
| `label('restore-compact')` `'◐ compact'` | unchanged (`'◐ compact'` after the press) |
| press `{ key: 'restore-rescan' }` | press `{ key: 'refresh' }` |
| `phase-tab:p1` label `'Patch 1 · active 2'`; press `{ key: 'phase-tab:p1' }` | press `{ key: 'phase-field' }`, then expect `phase-pick:p1`'s label to match `/^Patch 1 · active +2$/`, then press `{ key: 'phase-pick:p1' }` |
| Text `'  Finish the port.'` | Text `'Finish the port.'` |
| Text `'  OSError: no projects folder'` | Text `' · OSError: no projects folder'` |

Add one test to the migrated file, in `describe('/tm-restore over tm', …)`:

```ts
  test('/tm-restore closes another open Taskmaster pane first, and the sidebar lists Restore live', TM, async ($, on) => {
    const clock = mock.clock(on, { now: 1_791_700_000_000 })
    const world = worldOf(on, clock)
    world.env = ENV
    tmWorld(world, 1_791_700_000)
    await $.session.start(SESSION)
    await $.command.run(command('tm-session'))
    await $.command.run(command('tm-restore'))
    expect(world.closed).toEqual(['tm-session'])
    expect(world.opened.slice(-1)).toEqual(['tm-restore'])
    const ui = await $.ui.mount({ plugin: PLUGIN, ...RESTORE_PANE })
    await ui.press({ key: 'key:m' })
    await clock.settle()
    await ui.redraw()
    expect((await ui.find({ type: 'Button', key: 'nav:restore' }))?.props?.autoFocus).toBe(true)
  })
```

- [ ] **Steps.** Follow OLD 5.7's steps (write the failing tests with the deltas above, see them fail, implement with Deltas 1–4, run every check, commit with OLD's message plus ` in the rework style`). Expected at the end: all green, validate ✔, tsc clean, pytest `11 passed`.

---

### Task E8: Live check with the user (Stage E), then the final review

Orchestrator task; no subagent.

- [ ] **Step 1: Smoke** — as Task A6 Step 1, plus pytest.
- [ ] **Step 2: Ask the user** (the conditions of OLD Task 5.8, lines 18603 onward: a real burst of closed sessions in claude-tools or CodeMaestro, 120+ columns): `/tm-restore` and the sidebar's Restore entry (count and burst time); the sections; `v` flipping fresh / --resume; `r` reopening in a new tab with the right session; `b` resuming the ticked ones; `z`; compare with `Restore.dc.html`.
- [ ] **Step 3: Record** — ledger line `Task E8: Stage E live check — <words>`. Record OLD Stage 5's "spec points this stage settles" (OLD lines 14310–14329) in the spec as OLD 5.8 says, reading `m fresh / --resume` there as `v` and `z ◐ compact` as the toolbar toggle plus footer `z`.
- [ ] **Step 4: Final whole-branch review** — dispatch the most capable model over `f9937ea..HEAD` (the old plan's base), pointing it at the SDD ledger's deferred minors and at this plan's Review Focus. Do not push; the user decides.

---

## Self-review notes (for the executor)

- Spec §15 coverage: §15.2 vocabulary → A1–A5; §15.3 ladder and labels → A1, every pane task; §15.4 picker → A3, D2, D3, E6; §15.5 band → B1–B2; §15.6 lists → A2, D2, D3, E3, E6; §15.7 screens → D1–D3, E6; §15.8 sidebar and S1–S7 → A0, A4, C1–C2, E7; §15.9 keys → each pane task's footer and tests; §15.10 constraints → Global Constraints.
- The Task 4.4 "fix before merge" minor (close double-count) is fixed in D3, where the close flow is already touched.
- Interfaces that cross tasks: `vocab.tsx` exports (A1–A4, `badgeSeg` in D1); `nav.ts` (A4, `RESTORE_BUILT` flipped in E7); `TmNav`/`nav` (A4/B1); `TmFlows.openSidebar`/`retry` (B1), `goTo`/`toggleSidebar` (C1); `TmHost.panes?` (C1); `sidebarFor` (C2, register.tsx only); `LIST_PAGE_SIZE` (A2, used D2/D3); `RESTORE_PAGE_SIZE = 3`, `sectionsOf` (E3, used E6); `restorePaneTree`/`RestoreHandlers.toggleSidebar` (E6, used E7).
