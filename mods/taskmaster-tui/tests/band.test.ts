// User intent: the band as a person sees it — composed with other mods' bands, out of a survey's way, sized to the room,
// its digit hotkeys opening the panes and its review form asking before it signs anything off.
import { describe, expect, mock, test } from 'claude-code/testing'

import { DEMO_DETAILS } from '../hooks/demo'
import type { TmSnapshot } from '../types'
import { BAND, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { type Drawn, isEl, widthOf } from './fixtures/measure'
import { stateOf, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const SNAP = `${PLUGIN}.snapshot`

const FAR: TmSnapshot = {
  reachable: true,
  reason: '',
  fetchedAt: 0,
  queue: [{ kind: 'task', id: 'docs-012', title: 'README', priority: 'low', humanAction: 'Read it', timestamp: '' }],
  queueTotal: 355,
  handovers: [],
  handoversTotal: 0,
  bound: {
    taskId: 'far-001',
    inferred: false,
    detail: {
      id: 'far-001',
      title: 'Bound task beyond the loaded window',
      status: 'in-review',
      priority: 'medium',
      lane: 'full',
      gateState: 'review-gate:pass',
      branch: '',
      humanAction: 'Check the far task',
    },
    pipeline: null,
  },
}

describe('band', () => {
  test('draws the bound task and needs-you above whatever the other mods drew', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const tree = JSON.stringify(await $.ui.render(BAND))
    expect(tree).toContain('unified-chat-022')
    expect(tree).toContain('5 waiting on you')
    expect(tree.indexOf('5 waiting on you')).toBeLessThan(tree.indexOf('BELOW'))
  })

  test('yields entirely to a survey', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const tree = JSON.stringify(await $.ui.render({ ...BAND, props: { ...BAND.props, hasSurvey: true } }))
    expect(tree).not.toContain('waiting on you')
    expect(tree).toContain('BELOW')
  })

  test('hidden when nothing is bound and nothing waits', { plugins: [RR_STUB] }, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect(await $.ui.render(BAND)).toEqual({ type: 'Text', children: ['BELOW'] })
  })

  test('a 30-column, 2-row band fits every row in 30 cells, keeps the check to do and the two most important rows', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND, surface, props: { ...BAND.props, bodyColumns: 30, maxRows: 2 } })
      const texts = (await ui.findAll({ type: 'Text' })).map(t => t.text)
      for (const text of texts) {
        expect(text.includes('\n')).toBe(false)
        expect(text.length).toBeLessThanOrEqual(30)
      }
      const drawn = (await ui.drawn()) as unknown as Drawn
      const ours = drawn.children?.[0]
      expect(isEl(ours) ? ours.children?.length : 0).toBe(2)
      for (const row of isEl(ours) ? (ours.children ?? []) : []) expect(widthOf(row), JSON.stringify(row)).toBeLessThanOrEqual(30)
      expect(widthOf(drawn)).toBeLessThanOrEqual(30)
      expect(texts.some(t => /^waiting on you: \S/.test(t)), JSON.stringify(texts)).toBe(true)
      expect(await ui.find({ type: 'Button', key: 'band-review' })).toBeDefined()
      expect(await ui.find({ type: 'Button', key: 'band-done' })).toBeUndefined()
      await ui.unmount()
    }
  })

  test('the wide band shows id, check, and every action as one whole-chip button', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^waiting on you: Live check on dev/ })).toBeDefined()
    for (const [key, hotkey, label] of [
      ['band-done', 'd', 'done'],
      ['band-back', 'a', 'back to agent'],
      ['band-review', '1', 'review'],
      ['band-handovers', '2', 'handovers'],
    ] as const) {
      const wrapper = await ui.find({ type: 'Box', key: `${key}-box` })
      expect(wrapper?.children).toHaveLength(1)
      expect(wrapper?.children[0]).toMatchObject({ type: 'Button', props: { key, hotkey, label, plain: true }, hover: { bold: true } })
    }
  })

  test('1 opens the review queue and 2 the handovers', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await ui.press({ key: 'band-review' })
    await ui.press({ key: 'band-handovers' })
    expect(world.opened).toEqual(['tm-review', 'tm-handovers'])
  })

  test('d asks first, n cancels, y signs the bound task off and the band moves on', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await ui.press({ key: 'band-done' })
    expect(await ui.find({ type: 'Text', text: /done unified-chat-022\?/ })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'band-no' }))?.props.autoFocus).toBe(true)
    await ui.press({ key: 'band-no' })
    expect(await ui.find({ type: 'Button', key: 'band-done' })).toBeDefined()
    await ui.press({ key: 'band-done' })
    await ui.press({ key: 'band-yes' })
    expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /4 waiting on you/ })).toBeDefined()
  })

  test('a sends back the bound task even when the loaded queue window does not hold it', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    state.set(SNAP, FAR)
    const band = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await band.press({ key: 'band-back' })
    expect(world.opened).toEqual(['tm-review'])
    const review = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review'), surface: 'terminal' })
    expect(await review.find({ type: 'Text', text: 'far-001' })).toBeDefined()
    expect(await review.find({ type: 'Text', text: 'Check the far task' })).toBeDefined()
    await review.input({ key: 'note', text: 'not yet' })
    expect(world.fills).toEqual(['Back to far-001: not yet'])
  })

  test('a narrow confirm row never shows y without n, and its question names the task', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const columns of [44, 30, 24]) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND, props: { ...BAND.props, bodyColumns: columns, maxRows: 3 } })
      await ui.press({ key: 'band-done' })
      expect(await ui.find({ type: 'Button', key: 'band-yes' }), `${columns}`).toBeDefined()
      expect((await ui.find({ type: 'Button', key: 'band-no' }))?.props.autoFocus, `${columns}`).toBe(true)
      expect(await ui.find({ type: 'Text', text: /(done )?unified[-a-z0-9]*…?\?/ }), `${columns}`).toBeDefined()
      const drawn = JSON.stringify(await ui.drawn())
      if (columns >= 30) {
        const ours = ((await ui.drawn()) as unknown as Drawn).children?.[0]
        for (const row of isEl(ours) ? (ours.children ?? []) : []) expect(widthOf(row), `${columns}: ${JSON.stringify(row)}`).toBeLessThanOrEqual(columns)
      } else {
        // Too narrow for both: the pair is drawn first, so the edge clips the question, never `n`.
        expect(drawn.indexOf('band-no')).toBeLessThan(drawn.indexOf('unified'))
      }
      await ui.press({ key: 'band-no' })
      await ui.unmount()
    }
  })

  test('a band refusal shows only on the task it was given for', DEMO, async ($, on) => {
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
    expect(await ui.find({ type: 'Text', text: / ◆ refused / })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /outstanding gates/ })).toBeDefined()
    bind('docs-012')
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: / ◆ refused / })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /outstanding gates/ })).toBeUndefined()
  })

  test('a refresh between d and y never retargets y (Review Focus 5)', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    const demo = state.get(SNAP) as TmSnapshot
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    // Reordered: the asked task is no longer first; y still signs off the task d was pressed on.
    await ui.press({ key: 'band-done' })
    await ui.redraw()
    state.set(SNAP, { ...demo, queue: [...demo.queue].reverse() })
    await ui.redraw()
    await ui.press({ key: 'band-yes' })
    const after = state.get(SNAP) as TmSnapshot
    expect(after.queue.map(i => i.id)).toEqual(['DEC-4', 'ISS-7', 'docs-012', 'tm-audit-031'])
    expect(after.bound).toBeNull()
    // Replaced: the session is now bound to another task; the pending confirm does not carry over to it.
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
})
