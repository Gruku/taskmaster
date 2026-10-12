// User intent: pin the restore pane as Restore.dc.html draws it (spec §10, §15.6–15.7) — the burst page grouped UNFINISHED /
// HANDED OFF / IDLE then RUNNING NOW, 3-line items with project · branch · cwd · ctx and the bound task, ticks marked ■, the
// focused one boxed with r resume session filled, the fresh / --resume field and v, the bulk row with counts, one dim footer.
import { describe, expect, test } from 'claude-code/testing'

import type { TmRestoreData, TmRestoreView } from '../types'
import { demoPhases, demoRestore, demoSnapshot } from '../hooks/demo'
import type { StripView } from '../hooks/kit'
import { sidebarView } from '../hooks/nav'
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
  goTo: noop,
}
const TABS = sidebarView({ current: 'restore', snapshot: demoSnapshot(NOW_MS), restore: null, now: NOW_MS })

function draw(view: Partial<TmRestoreView> = {}, data: Partial<TmRestoreData> = {}, width = 116, source: 'tm' | 'demo' = 'demo') {
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
  return restorePaneTree(testKit(), { data: full, view: { ...FRESH_RESTORE_VIEW, ...view }, rows, strip, now: NOW_MS / 1000, source, tabs: TABS }, ON, width)
}
const keysOf = (tree: unknown): Record<string, string[]> => {
  const keys: Record<string, string[]> = {}
  for (const b of walk(tree).filter(e => e.type === 'Button')) if (typeof b.props?.hotkey === 'string') (keys[b.props.hotkey] ??= []).push(String(b.props.key))
  return keys
}
const focusedId = (tree: unknown): string => String(walk(tree).find(e => e.type === 'Box' && String(e.props?.key ?? '').startsWith('card:'))?.props?.key).slice(5)

describe('the restore pane, as drawn', () => {
  test('the frame names RESTORE; the tab row first; row 1 has the picker, ○ compact, the pager and u rescan; then the burst sentence', () => {
    const tree = draw()
    expect(walk(tree)[0]?.props).toMatchObject({ borderStyle: 'round', borderColor: T.border.default })
    const texts = textsOf(tree)
    for (const text of ['RESTORE', 'demo', '○ compact', 'u', ' rescan']) expect(texts, text).toContain(text)
    expect(texts.some(text => /closed together/.test(text))).toBe(true)
    expect(byKey(tree, 'tabs:sidebar')).toBeDefined()
    expect(byKey(tree, 'tab:restore')).toBeDefined()
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
    const id = focusedId(tree)
    expect(byKey(tree, `resume:${id}`)?.props).toMatchObject({ hotkey: 'r', label: 'resume session' })
    expect(byKey(tree, `resume:${id}-box`)?.props?.backgroundColor).toBe(T.keycap.signature.bg)
    expect(byKey(tree, `mode:${id}`)?.props).toMatchObject({ hotkey: 'v', label: 'switch resume mode' })
    expect(byKey(tree, `brief:${id}`)?.props).toMatchObject({ hotkey: 'i', label: 'brief session' })
    expect(byKey(tree, `copy:${id}`)?.props).toMatchObject({ hotkey: 'c', label: 'copy resume command' })
    expect(walk(tree).filter(e => e.type === 'Button' && String(e.props?.key ?? '').startsWith('resume:'))).toHaveLength(1)
  })

  test('one filled key in the pane: only the focused r carries the signature keycap', () => {
    const tree = draw()
    const filled = walk(tree).filter(e => e.type === 'Box' && e.props?.backgroundColor === T.keycap.signature.bg)
    expect(filled.map(e => String(e.props?.key))).toEqual([`resume:${focusedId(tree)}-box`])
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

  test('the footer owns j k s z h l f p n u m; v resume mode is words only; no hotkey on two Buttons; no digit drawn', () => {
    const tree = draw()
    const keys = keysOf(tree)
    for (const [hotkey, id] of [['j', 'restore-down'], ['k', 'restore-up'], ['s', 'key:s'], ['z', 'key:z'], ['h', 'key:h'], ['l', 'key:l'], ['f', 'key:f'], ['p', 'key:p'], ['n', 'key:n'], ['u', 'key:u'], ['m', 'key:m']] as const) expect(keys[hotkey], hotkey).toEqual([id])
    expect(keys.v).toEqual([`mode:${focusedId(tree)}`])
    expect(textsOf(tree).join('')).toContain('v resume mode')
    expect(Object.entries(keys).filter(([, ids]) => ids.length > 1)).toEqual([])
    expect(Object.keys(keys).filter(key => /^\d$/.test(key))).toEqual([])
  })

  test('a failed scan and no scan yet each say so in one row', () => {
    expect(textsOf(draw({}, { error: 'Scan failed: OSError: no projects folder', scan: null }))).toEqual(expect.arrayContaining(['▲ scan failed', ' · OSError: no projects folder']))
    expect(textsOf(draw({}, { scan: null, refs: null }))).toContain('No scan yet: u scans this repo’s sessions')
  })

  test('tm offline: one ▲ row when the source is tm, refs null and nothing busy; none while busy, none in demo', () => {
    const offline = (view: Partial<TmRestoreView>, source: 'tm' | 'demo') => textsOf(draw(view, { refs: null }, 116, source)).includes('▲ tm offline')
    expect(offline({ busy: '' }, 'tm')).toBe(true)
    expect(textsOf(draw({ busy: '' }, { refs: null }, 116, 'tm'))).toContain(' · statuses read ? · resume still works')
    expect(offline({ busy: 'scanning' }, 'tm')).toBe(false)
    expect(offline({ busy: '' }, 'demo')).toBe(false)
  })

  test('too tight for a whole bulk label, the count stays and the trailing sessions goes', () => {
    expect(String(byKey(draw({ compact: true }, {}, 28), 'restore-resume-all')?.props?.label)).toMatch(/^resume \+ compact \d+$/)
    expect(String(byKey(draw({}, {}, 28), 'restore-brief-all')?.props?.label)).toMatch(/^brief \d+ sessions?$/)
  })

  test('every row fits at 100, 60, 36 and 28 columns', () => {
    for (const columns of [100, 60, 36, 28]) {
      for (const view of [{}, { compact: true }]) {
        const tree = draw(view, {}, columns)
        expect(widthOf(tree), `${columns}`).toBeLessThanOrEqual(columns)
        const rows = elementsOf(tree).filter(e => e.type === 'Box' && e.props?.flexDirection === 'row' && e.props?.position !== 'absolute')
        for (const row of rows) expect(widthOf(row), `${columns} ${JSON.stringify(textsOf(row))}`).toBeLessThanOrEqual(columns - 4)
      }
    }
  })
})
