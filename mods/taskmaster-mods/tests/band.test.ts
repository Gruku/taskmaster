// User intent: the band as a person sees it (spec §6) — two lines at most on their own raised ground under `◆ TASKMASTER`,
// never mistaken for transcript text: the task with `d` and `3 session`, the session's handover with `c`, keys `1` `2` `4`
// to the panes, sized to the room on both surfaces, and its `d` asking before it signs anything off.
import { describe, expect, mock, test } from 'claude-code/testing'

import { DEMO_DETAILS, DEMO_REASON } from '../hooks/demo'
import type { TmSnapshot } from '../types'
import { BAND, PLUGIN, SESSION } from './fixtures/inputs'
import { type Drawn, isEl, widthOf } from './fixtures/measure'
import * as R from './fixtures/replies'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const TM = { plugins: [RR_STUB] }
const SNAP = `${PLUGIN}.snapshot`
const AUDIT = '2026-10-04-audit-fixes-landed'
const AUDIT_BLOCK = `Audit fixes landed on the native branch\nC:\\Users\\demo\\project\\.taskmaster\\handovers\\${AUDIT}.md\nResume: tm-audit — Record the merge for tm-audit-030`
const BURST = { epoch: 1_760_000_000, unfinished: 3 }

/** Our band in a mounted AbovePrompt: its blank-row wrapper, the raised ground inside it, and that ground's lines. */
async function bandOf(ui: { drawn: () => Promise<unknown> }): Promise<{ outer: Drawn; ground: Drawn; lines: Drawn[] }> {
  const drawn = (await ui.drawn()) as Drawn
  const outer = drawn.children?.[0]
  const ground = isEl(outer) ? outer.children?.[0] : undefined
  if (!isEl(outer) || !isEl(ground)) throw new Error(`no band drawn: ${JSON.stringify(drawn)}`)
  return { outer, ground, lines: (ground.children ?? []).filter(isEl) }
}

describe('band', () => {
  test('draws on its own raised ground, one blank row above, above whatever the other mods drew', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    const { outer, ground, lines } = await bandOf(ui)
    expect(outer.props?.paddingTop).toBe(1)
    expect(ground.props).toMatchObject({ backgroundColor: '#1d1d1b', paddingX: 1 })
    expect(lines).toHaveLength(2)
    expect(await ui.find({ type: 'Text', text: ' ◆ TASKMASTER ' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: ' IN REVIEW ' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeDefined()
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

  test('line 1: the task with d done and 3 session; line 2: the handover it was resumed from, c copy and the counts', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    for (const [key, hotkey, label] of [
      ['band-done', 'd', 'done'],
      ['band-session', '3', 'session ›'],
      ['band-copy', 'c', 'copy'],
      ['band-review', '1', '5 waiting on you'],
      ['band-handovers', '2', '6 handovers'],
    ] as const) {
      expect((await ui.find({ type: 'Button', key }))?.props, key).toMatchObject({ hotkey, label, plain: true })
    }
    expect(await ui.find({ type: 'Text', text: '↳ HANDOVER' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'resumed from ·' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'Audit fixes landed on the native branch' })).toBeDefined()
    expect(await ui.find({ type: 'Button', key: 'band-restore' })).toBeUndefined()
  })

  test('1 2 3 open review, handovers and session; c copies the handover block', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    for (const key of ['band-review', 'band-handovers', 'band-session', 'band-copy']) await ui.press({ key })
    expect(world.opened).toEqual(['tm-review', 'tm-handovers', 'tm-session'])
    expect(world.copies).toEqual([AUDIT_BLOCK])
    expect(world.toasts).toEqual([`Copied handover ${AUDIT}`])
  })

  test('no handover: the counts move up and the band is one line; no task: the chip and the counts', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    state.set(`${PLUGIN}.sessionHandover`, null)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    expect((await bandOf(ui)).lines).toHaveLength(1)
    expect(await ui.find({ type: 'Button', key: 'band-review' })).toBeDefined()
    expect(await ui.find({ type: 'Button', key: 'band-done' })).toBeDefined()
    state.set(SNAP, { ...(state.get(SNAP) as TmSnapshot), bound: null })
    await ui.redraw()
    expect((await bandOf(ui)).lines).toHaveLength(1)
    expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeUndefined()
    expect((await ui.find({ type: 'Button', key: 'band-review' }))?.props.label).toBe('5 waiting on you')
  })

  test('every row fits from 30 to 120 columns on both surfaces; words shrink to key and count before any key goes', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const surface of ['terminal', 'desktop'] as const) {
      for (const columns of [30, 44, 60, 90, 120]) {
        const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND, surface, props: { ...BAND.props, bodyColumns: columns } })
        const { ground, lines } = await bandOf(ui)
        // Cells are the terminal's measure; the desktop lays out its own native buttons, so there only the keys are checked.
        if (surface === 'terminal') {
          expect(widthOf(ground), `${columns}`).toBeLessThanOrEqual(columns)
          for (const line of lines) expect(widthOf(line), `${columns}: ${JSON.stringify(line)}`).toBeLessThanOrEqual(columns - 2)
        }
        for (const key of ['band-done', 'band-session', 'band-copy', 'band-review', 'band-handovers']) {
          expect(await ui.find({ type: 'Button', key }), `${surface} ${columns} ${key}`).toBeDefined()
        }
        await ui.unmount()
      }
    }
    const narrow = await $.ui.mount({ plugin: PLUGIN, ...BAND, props: { ...BAND.props, bodyColumns: 30 } })
    expect((await narrow.find({ type: 'Button', key: 'band-review' }))?.props.label).toBe('5')
    expect(await narrow.find({ type: 'Text', text: ' ◆ TM ' })).toBeDefined()
  })

  test('two rows: no blank row above; one row: line 1 alone, or the y/n while a confirm is up', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const two = await $.ui.mount({ plugin: PLUGIN, ...BAND, props: { ...BAND.props, maxRows: 2 } })
    expect((await bandOf(two)).outer.props?.paddingTop).toBe(0)
    expect((await bandOf(two)).lines).toHaveLength(2)
    await two.unmount()
    const one = await $.ui.mount({ plugin: PLUGIN, ...BAND, props: { ...BAND.props, maxRows: 1 } })
    expect((await bandOf(one)).lines).toHaveLength(1)
    expect(await one.find({ type: 'Button', key: 'band-done' })).toBeDefined()
    await one.press({ key: 'band-done' })
    expect(await one.find({ type: 'Button', key: 'band-yes' })).toBeDefined()
    expect(await one.find({ type: 'Button', key: 'band-no' })).toBeDefined()
  })

  test('d asks first, n cancels, y signs the bound task off and the band moves on', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await ui.press({ key: 'band-done' })
    expect(await ui.find({ type: 'Text', text: ' done unified-chat-022? ' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'band-no' }))?.props.autoFocus).toBe(true)
    await ui.press({ key: 'band-no' })
    expect(await ui.find({ type: 'Button', key: 'band-yes' })).toBeUndefined()
    await ui.press({ key: 'band-done' })
    await ui.press({ key: 'band-yes' })
    expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeUndefined()
    expect((await ui.find({ type: 'Button', key: 'band-review' }))?.props.label).toBe('4 waiting on you')
  })

  test('a narrow confirm never shows y without n, and its question names the task', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const columns of [44, 30, 24]) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND, props: { ...BAND.props, bodyColumns: columns, maxRows: 4 } })
      await ui.press({ key: 'band-done' })
      expect(await ui.find({ type: 'Button', key: 'band-yes' }), `${columns}`).toBeDefined()
      expect((await ui.find({ type: 'Button', key: 'band-no' }))?.props.autoFocus, `${columns}`).toBe(true)
      expect(await ui.find({ type: 'Text', text: / (done )?unified-[-a-z0-9]*…?\? / }), `${columns}`).toBeDefined()
      for (const line of (await bandOf(ui)).lines) expect(widthOf(line), `${columns}: ${JSON.stringify(line)}`).toBeLessThanOrEqual(columns - 2)
      await ui.press({ key: 'band-no' })
      await ui.unmount()
    }
  })

  test('a band refusal shows on line 2, only on the task it was given for', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    const demo = state.get(SNAP) as TmSnapshot
    const bind = (id: string) => state.set(SNAP, { ...demo, bound: { taskId: id, inferred: false, detail: DEMO_DETAILS[id] ?? null, pipeline: null } })
    bind('tm-audit-031')
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await ui.press({ key: 'band-done' })
    await ui.redraw()
    await ui.press({ key: 'band-yes' })
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: ' REFUSED ' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /outstanding gates/ })).toBeDefined()
    bind('docs-012')
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: ' REFUSED ' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /outstanding gates/ })).toBeUndefined()
  })

  test('a refresh between d and y never retargets y (Review Focus 5)', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    const demo = state.get(SNAP) as TmSnapshot
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await ui.press({ key: 'band-done' })
    await ui.redraw()
    state.set(SNAP, { ...demo, queue: [...demo.queue].reverse() })
    await ui.redraw()
    await ui.press({ key: 'band-yes' })
    const after = state.get(SNAP) as TmSnapshot
    expect(after.queue.map(i => i.id)).toEqual(['DEC-4', 'ISS-7', 'docs-012', 'tm-audit-031'])
    expect(after.bound).toBeNull()
    state.set(SNAP, { ...demo, bound: { taskId: 'tm-audit-031', inferred: false, detail: DEMO_DETAILS['tm-audit-031'] ?? null, pipeline: null } })
    await ui.redraw()
    await ui.press({ key: 'band-done' })
    await ui.redraw()
    state.set(SNAP, { ...demo })
    await ui.redraw()
    expect(await ui.find({ type: 'Button', key: 'band-yes' })).toBeUndefined()
    expect(await ui.find({ type: 'Button', key: 'band-done' })).toBeDefined()
    expect((state.get(SNAP) as TmSnapshot).queue).toHaveLength(5)
  })

  test('d shows only for a bound task that is ready: not for an in-progress one with gates outstanding', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    const demo = state.get(SNAP) as TmSnapshot
    const detail = { ...DEMO_DETAILS['tm-audit-031']!, status: 'in-progress' }
    state.set(SNAP, { ...demo, reason: DEMO_REASON, bound: { taskId: 'tm-audit-031', inferred: false, detail, pipeline: { laneless: false, lane: 'full', gateState: '', outstanding: ['review-gate'] } } })
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    expect(await ui.find({ type: 'Text', text: ' IN PROGRESS ' })).toBeDefined()
    expect(await ui.find({ type: 'Button', key: 'band-done' })).toBeUndefined()
  })

  test('4 restore shows while a burst is pending; x hides it for that burst, here and in every later session', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock, { 'binding:sess-A': { taskId: 'tm-audit-030', at: 0 } })
    world.mcp = R.backlog
    const state = stateOf(on, { [`${PLUGIN}.restorePending`]: BURST })
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    expect((await ui.find({ type: 'Button', key: 'band-restore' }))?.props).toMatchObject({ hotkey: '4', label: '3 restore' })
    expect((await ui.find({ type: 'Button', key: 'band-restore-hide' }))?.props).toMatchObject({ hotkey: 'x', label: 'hide' })
    await ui.press({ key: 'band-restore' })
    expect(world.opened).toEqual(['tm-restore'])
    await ui.press({ key: 'band-restore-hide' })
    await clock.settle()
    await ui.redraw()
    expect(world.store.get(`restore-hidden:${BURST.epoch}`)).toBe(true)
    expect(await ui.find({ type: 'Button', key: 'band-restore' })).toBeUndefined()
    // Another session: nothing hidden in its own state, the store still hides that burst; a newer burst shows again.
    state.set(`${PLUGIN}.restoreHidden`, null)
    await ui.redraw()
    expect(await ui.find({ type: 'Button', key: 'band-restore' })).toBeUndefined()
    state.set(`${PLUGIN}.restorePending`, { epoch: BURST.epoch + 600, unfinished: 2 })
    await ui.redraw()
    expect((await ui.find({ type: 'Button', key: 'band-restore' }))?.props.label).toBe('2 restore')
  })

  test('offline: the offline signal on line 1 and the counts faded, still pressable', TM, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock, { 'binding:sess-A': { taskId: 'tm-audit-030', at: 0 } })
    await $.session.start(SESSION)
    await clock.settle()
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    expect(await ui.find({ type: 'Text', text: ' offline' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'tm-audit-030' })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'band-review' }))?.props).toMatchObject({ label: '– waiting on you', dimColor: true })
    await ui.press({ key: 'band-review' })
    expect(world.opened).toEqual(['tm-review'])
  })
})
