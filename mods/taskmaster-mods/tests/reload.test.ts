// User intent: a module instance that never saw session.start — a userConfig change (e.g. Data source → demo) or any reload of
// an unchanged module re-runs register() alone — still draws and acts; "Loading…" only ever means the data is absent.
import { describe, expect, mock, test } from 'claude-code/testing'

import { demoSnapshot } from '../hooks/demo'
import { handoverCopyText } from '../hooks/model'
import { BAND, command, pane, PLUGIN } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

// No `$.session.start` in any test below: the plugins load at the first call on `$`, as a reload re-runs register().
const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const TM = { plugins: [RR_STUB] }

describe('reload without session.start', () => {
  test('demo: the open review pane draws its card, d asks and y signs off', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
    expect(await ui.find({ type: 'Text', text: 'Loading…' })).toBeUndefined()
    expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeDefined()
    await ui.press({ key: 'done' })
    expect((await ui.find({ type: 'Button', key: 'confirm-no' }))?.props.autoFocus).toBe(true)
    await ui.press({ key: 'confirm-yes' })
    expect(await ui.find({ type: 'Text', text: /2 of 5 · 1 done this pass/ })).toBeDefined()
  })

  test('demo: the open handovers pane lists, copies and resumes', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    const first = demoSnapshot(0).handovers[0]!
    const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-handovers') })
    expect(await ui.find({ type: 'Button', key: `ho:${first.id}` })).toBeDefined()
    await ui.press({ key: 'copy' })
    expect(world.copies).toEqual([handoverCopyText(first)])
    await ui.press({ key: 'resume' })
    expect(world.fills).toEqual([`Resume from handover ${first.id} (${first.path})`])
  })

  test('demo: the band draws, d asks and y signs the bound task off', DEMO, async ($, on) => {
    worldOf(on, mock.clock(on))
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeDefined()
    await ui.press({ key: 'band-done' })
    expect(await ui.find({ type: 'Button', key: 'band-yes' })).toBeDefined()
    await ui.press({ key: 'band-yes' })
    expect(await ui.find({ type: 'Text', text: /4 waiting on you/ })).toBeDefined()
  })

  test('demo: the commands open their panes', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    expect(await $.command.run(command('tm-review'))).toEqual({ text: 'Review queue opened.' })
    expect(await $.command.run(command('tm-handovers'))).toEqual({ text: 'Handovers opened.' })
    expect(world.opened).toEqual(['tm-review', 'tm-handovers'])
  })

  test('tm: with no data yet the panes say Loading… and the band stays out of the way', TM, async ($, on) => {
    worldOf(on, mock.clock(on))
    for (const id of ['tm-review', 'tm-handovers']) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...pane(id) })
      expect(await ui.find({ type: 'Text', text: 'Loading…' }), id).toBeDefined()
      await ui.unmount()
    }
    expect(await $.ui.render(BAND)).toEqual({ type: 'Text', children: ['BELOW'] })
  })

  test('switching demo → tm: demo data left in $.state is not shown as real', TM, async ($, on) => {
    worldOf(on, mock.clock(on))
    stateOf(on, { [`${PLUGIN}.snapshot`]: demoSnapshot(0) })
    const ui = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
    expect(await ui.find({ type: 'Text', text: 'Loading…' })).toBeDefined()
    expect(await ui.find({ type: 'Text', text: 'unified-chat-022' })).toBeUndefined()
  })
})
