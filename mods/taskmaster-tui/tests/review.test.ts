// User intent: the review queue as a person walks it — most urgent card first with the whole check to do, skip/open keep the
// pane, done asks first and shows a refusal on the card, back-to-agent takes a note and closes the pane.
import { describe, expect, mock, test } from 'claude-code/testing'

import { DEMO_DETAILS, demoSnapshot } from '../hooks/demo'
import type { TmSnapshot } from '../types'
import { command, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const REVIEW_PANE = pane('tm-review')
const SNAP = `${PLUGIN}.snapshot`

describe('review queue', () => {
  test('/tm-review opens the review pane', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect(await $.command.run(command('tm-review'))).toEqual({ text: 'Review queue opened.' })
    expect(world.opened).toEqual(['tm-review'])
  })

  test('the first card is the most urgent in-review task with its whole human_action', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /second message builds with the full toolset/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /1 of 5 · 0 done this pass/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /◆ Critical/ })).toBeDefined()
  })

  test('every action is one whole-chip button with its key; done is the one outlined primary; only Esc gets a legend', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    for (const [key, hotkey, label] of [
      ['done', 'd', 'done'],
      ['back', 'a', 'back to agent'],
      ['skip', 's', 'skip'],
      ['open', 'o', 'open in prompt'],
    ] as const) {
      const wrapper = await ui.find({ type: 'Box', key: `${key}-box` })
      expect(wrapper?.children).toHaveLength(1)
      expect(wrapper?.children[0]).toMatchObject({ type: 'Button', props: { key, hotkey, label, plain: true }, hover: { bold: true } })
      expect(typeof wrapper?.props.borderStyle === 'string').toBe(key === 'done')
    }
    expect(await ui.find({ type: 'Text', text: 'Esc' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /^close$/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /back to agent|skip/ })).toBeUndefined()
  })

  test('s moves to the next card; o fills the prompt and keeps the pane open', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: 'skip' })
    expect(await ui.find({ type: 'Text', text: 'tm-audit-031' })).toBeDefined()
    await ui.press({ key: 'open' })
    expect(world.fills).toEqual(['Look at tm-audit-031'])
    expect(world.closed).toEqual([])
  })

  test('d asks first and the focused n cancels; y signs off and the next card shows', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: 'done' })
    expect(await ui.find({ type: 'Button', key: 'done' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: /confirm done\?/ })).toBeDefined()
    expect((await ui.find({ type: 'Button', key: 'confirm-no' }))?.props.autoFocus).toBe(true)
    await ui.press({ key: 'confirm-no' })
    expect(await ui.find({ type: 'Button', key: 'done' })).toBeDefined()
    await ui.press({ key: 'done' })
    await ui.press({ key: 'confirm-yes' })
    expect(await ui.find({ type: 'Text', text: /2 of 5 · 1 done this pass/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'tm-audit-031' })).toBeDefined()
  })

  test('a refusal is shown on the card and the card stays', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: 'skip' })
    await ui.press({ key: 'done' })
    await ui.press({ key: 'confirm-yes' })
    expect(await ui.find({ type: 'Text', text: /outstanding gates for lane `full`: review-gate/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: / ◆ refused / })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'tm-audit-031' })).toBeDefined()
  })

  test('a takes a note, then the pane closes and the prompt holds "Back to <id>: <note>"', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE, surface: 'terminal' })
    await ui.press({ key: 'back' })
    expect(await ui.find({ type: 'Input', key: 'note' })).toBeDefined()
    await ui.input({ key: 'note', text: 'tighten the copy' })
    expect(world.closed).toEqual(['tm-review'])
    expect(world.fills).toEqual(['Back to unified-chat-022: tighten the copy'])
  })

  test('after the last card: Queue clear with the pass tally', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    for (let i = 0; i < 5; i += 1) await ui.press({ key: 'skip' })
    expect(await ui.find({ type: 'Text', text: /Queue clear {2}0 done · 5 skipped this pass/ })).toBeDefined()
  })

  test('every mode validates on the terminal and the desktop', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE, surface })
      await ui.press({ key: 'done' })
      await ui.press({ key: 'confirm-no' })
      await ui.press({ key: 'back' })
      expect(await ui.find({ type: 'Input', key: 'note' })).toBeDefined()
      await ui.press({ key: 'note-cancel' })
      await ui.unmount()
    }
  })
  test('a narrow pane never shows y without n, and n keeps the focus', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const columns of [72, 34, 28, 20]) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE, props: { ...REVIEW_PANE.props, bodyColumns: columns } })
      await ui.press({ key: 'done' })
      expect(await ui.find({ type: 'Button', key: 'confirm-yes' }), `${columns}`).toBeDefined()
      expect((await ui.find({ type: 'Button', key: 'confirm-no' }))?.props.autoFocus, `${columns}`).toBe(true)
      expect(await ui.find({ type: 'Text', text: /confirm done\?/ }), `${columns}`).toBeDefined()
      await ui.press({ key: 'confirm-no' })
      await ui.unmount()
    }
  })

  test('tm mode: y refuses until the writes are wired, and the card and tally stay', { plugins: [RR_STUB] }, async ($, on) => {
    worldOf(on, mock.clock(on))
    stateOf(on, { [SNAP]: demoSnapshot(0), [`${PLUGIN}.details`]: DEMO_DETAILS })
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: 'done' })
    await ui.redraw()
    await ui.press({ key: 'confirm-yes' })
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: /writes not wired yet/ })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: /1 of 5 · 0 done this pass/ })).toBeDefined()
  })

  test('a refresh between d and y never retargets y (Review Focus 5)', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    const demo = state.get(SNAP) as TmSnapshot
    const ui = await $.ui.mount({ plugin: PLUGIN, ...REVIEW_PANE })
    await ui.press({ key: 'done' })
    await ui.redraw()
    // The refusing task moves to the head of the queue: a retargeted y would sign it off (and be refused).
    const [first, second, ...rest] = demo.queue
    state.set(SNAP, { ...demo, queue: [second!, first!, ...rest] })
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeDefined()
    await ui.press({ key: 'confirm-yes' })
    await ui.redraw()
    expect(await ui.find({ type: 'Text', text: /refused|outstanding gates/ })).toBeUndefined()
    expect((state.get(SNAP) as TmSnapshot).queue.map(i => i.id)).toEqual(['tm-audit-031', 'docs-012', 'ISS-7', 'DEC-4'])
    expect(await ui.find({ type: 'Text', text: /2 of 5 · 1 done this pass/ })).toBeDefined()
  })
})
