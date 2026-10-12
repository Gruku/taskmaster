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
    for (const text of [' ◆ TASKMASTER ', '● IN REVIEW', 'unified-chat-022', '↳ resumed from ', AUDIT, ' · 20h']) {
      expect(await ui.find({ type: 'Text', text }), text).toBeDefined()
    }
    const tree = JSON.stringify(await ui.drawn())
    expect(tree).toContain('Audit fixes landed')
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
    await ui.redraw()
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
