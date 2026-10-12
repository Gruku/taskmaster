// User intent: pin the Taskmaster sidebar (spec §15.8) — four entries with their counts and notes, the current pane marked,
// 1–4 jumping, Restore dim until it is built — and the navigation rules behind it: which pane an id is, which layer Esc closes.
import type { RenderElement } from 'claude-code'
import { describe, expect, test } from 'claude-code/testing'

import { demoSnapshot } from '../hooks/demo'
import { escLayer, FRESH_NAV, hhmm, paneOf, RESTORE_BUILT, shownRestore, shownSnapshot, sidebarView, TM_PANE_IDS } from '../hooks/nav'
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

  test('Restore draws dim while unbuilt and the others live (handlers are not in the drawn tree); withSidebar floats it at the top-left, absent when closed', () => {
    const k = testKit('terminal', [])
    const v = sidebarView({ current: 'session', snapshot: demoSnapshot(NOW), restore: null, now: NOW })
    const tree = sidebar(k, v, () => undefined)
    expect(byKey(tree, 'nav:review')?.props?.dimColor).toBeUndefined()
    expect(byKey(tree, 'nav:restore')?.props?.dimColor).toBe(RESTORE_BUILT ? undefined : true)
    const body = h('Text', {}, 'body') as RenderElement
    expect(withSidebar(k, body, null, () => undefined)).toBe(body)
    const open = withSidebar(k, body, v, () => undefined)
    expect(walk(open).some(e => e.props?.position === 'absolute' && e.props?.top === 0 && e.props?.left === 0)).toBe(true)
  })
})

describe('what every pane shows of the snapshot and the restore burst', () => {
  const live = { ...demoSnapshot(NOW), reason: '', queueTotal: 7 }
  test('shownSnapshot: demo keeps a current seed and rebuilds a stale or missing one; tm drops any demo seed', () => {
    const current = demoSnapshot(NOW)
    expect(shownSnapshot(current, 'demo', NOW + 1)).toBe(current)
    expect(shownSnapshot({ ...current, reason: 'demo:old' }, 'demo', NOW)).toEqual(demoSnapshot(NOW))
    expect(shownSnapshot(null, 'demo', NOW)).toEqual(demoSnapshot(NOW))
    expect(shownSnapshot(live, 'demo', NOW)).toEqual(demoSnapshot(NOW))
    expect(shownSnapshot(current, 'tm', NOW)).toBeNull()
    expect(shownSnapshot({ ...current, reason: 'demo:old' }, 'tm', NOW)).toBeNull()
    expect(shownSnapshot(live, 'tm', NOW)).toBe(live)
    expect(shownSnapshot(null, 'tm', NOW)).toBeNull()
  })
  test('shownRestore: a burst hidden here or in the store is not offered', () => {
    const pending = { epoch: 1_000, unfinished: 2 }
    expect(shownRestore(pending, null, false)).toBe(pending)
    expect(shownRestore(pending, 999, false)).toBe(pending)
    expect(shownRestore(pending, 1_000, false)).toBeNull()
    expect(shownRestore(pending, null, true)).toBeNull()
    expect(shownRestore(null, null, false)).toBeNull()
  })
})
