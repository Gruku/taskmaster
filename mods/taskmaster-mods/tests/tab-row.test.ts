// User intent: the tab row atop every Taskmaster pane (spec §15.x, user request 2026-10-12) — ☰ opens the sidebar and one tab
// per pane switches panes without the band, no hotkeys on any of it, and it never grows wider than its room.
import { describe, expect, mock, test } from 'claude-code/testing'

import { demoSnapshot } from '../hooks/demo'
import { sidebarView } from '../hooks/nav'
import { tabRow } from '../hooks/vocab'
import { pane, PLUGIN, SESSION } from './fixtures/inputs'
import { byKey, testKit, textsOf, walk } from './fixtures/kit-ui'
import { widthOf } from './fixtures/measure'
import { RR_STUB } from './fixtures/rr-stub'
import { STUB_TOKENS as T } from './fixtures/tokens'
import { stateOf, worldOf } from './fixtures/world'

const NOW = Date.UTC(2026, 9, 12, 12, 0)
const view = (current: 'review' | 'handovers' | 'session' = 'review') => sidebarView({ current, snapshot: demoSnapshot(NOW), restore: null, now: NOW })
const KEYS = ['tab:review', 'tab:handovers', 'tab:session', 'tab:restore']

describe('tabRow', () => {
  test('☰ then Review, Handovers, Session, Restore in sidebar order, dim │ between', () => {
    const tree = tabRow(testKit(), view(), { go: () => undefined, toggleSidebar: () => undefined }, 100)
    const keys = walk(tree).filter(e => e.type === 'Button').map(e => e.props?.key)
    expect(keys).toEqual(['tabs:sidebar', ...KEYS])
    expect(textsOf(tree).filter(s => s === '│').length).toBe(3)
  })

  test('no Button on the row carries a hotkey', () => {
    const tree = tabRow(testKit(), view(), { go: () => undefined, toggleSidebar: () => undefined }, 100)
    for (const b of walk(tree).filter(e => e.type === 'Button')) expect(b.props?.hotkey, String(b.props?.key)).toBeUndefined()
  })

  test('the current tab is bold signature text on the tint24 signature ground; counts subtle; Restore live', () => {
    const v = view('handovers')
    const tree = tabRow(testKit(), v, { go: () => undefined, toggleSidebar: () => undefined }, 100)
    expect(byKey(tree, 'tab:handovers-box')?.props?.backgroundColor).toBe(T.tint24.page.signature)
    expect(byKey(tree, 'tab:review-box')?.props?.backgroundColor).toBeUndefined()
    const names = walk(byKey(tree, 'tab:handovers')).filter(e => e.type === 'Text' && e.props?.bold === true)
    expect(names[0]?.props?.color).toBe(T.signatureText)
    const count = v.entries[0]?.count ?? ''
    expect(count).not.toBe('')
    const reviewCount = walk(byKey(tree, 'tab:review')).find(e => e.type === 'Text' && textsOf(e).join('') === ` ${count} `)
    expect(reviewCount?.props?.color).toBe(T.fg.subtle)
    expect(byKey(tree, 'tab:restore')?.props?.dimColor).toBeUndefined()
  })

  test('widths: full at 100 and 60; counts dropped then names shortened, then the m hint dropped — never wider than its room', () => {
    const draw = (w: number) => tabRow(testKit(), view(), { go: () => undefined, toggleSidebar: () => undefined }, w)
    for (const w of [100, 60, 36, 28]) expect(widthOf(draw(w)), String(w)).toBeLessThanOrEqual(w)
    const labels = (w: number): string[] => walk(draw(w)).filter(e => e.type === 'Button').map(e => String(e.props?.label).trim())
    expect(labels(100)[1]).toMatch(/^Review \d/)
    expect(labels(60)[0]).toBe('☰ m')
    expect(labels(36).slice(1)).toEqual(['Rev', 'Hand', 'Sess', 'Rest'])
    expect(labels(28)[0]).toBe('☰')
    expect(labels(28).slice(1)).toEqual(['Rev', 'Hand', 'Sess', 'Rest'])
  })
})

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }

describe('the tab row in each pane', () => {
  for (const [paneName, id, target, targetId] of [
    ['session', 'tm-session', 'handovers', 'tm-handovers'],
    ['review', 'tm-review', 'session', 'tm-session'],
    ['handovers', 'tm-handovers', 'review', 'tm-review'],
    ['restore', 'tm-restore', 'session', 'tm-session'],
  ] as const) {
    test(`${paneName}: the row is there; ☰ opens the sidebar; a tab closes this pane and opens ${target}`, DEMO, async ($, on) => {
      const world = worldOf(on, mock.clock(on))
      const state = stateOf(on)
      await $.session.start(SESSION)
      const ui = await $.ui.mount({ plugin: PLUGIN, ...pane(id) })
      for (const key of ['tabs:sidebar', ...KEYS]) expect(await ui.find({ type: 'Button', key }), key).toBeDefined()
      await ui.press({ key: 'tabs:sidebar' })
      expect((state.get(`${PLUGIN}.nav`) as { sidebar: boolean }).sidebar).toBe(true)
      await ui.press({ key: 'tabs:sidebar' })
      expect(world.opened).toEqual([])
      // Its own tab opens this pane (it was only mounted); the target's tab then swaps it out.
      await ui.press({ key: `tab:${paneName}` })
      expect(world.opened).toEqual([id])
      await ui.press({ key: `tab:${target}` })
      expect(world.closed).toContain(id)
      expect(world.opened).toContain(targetId)
    })
  }
})
