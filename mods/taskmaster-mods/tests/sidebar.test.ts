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
