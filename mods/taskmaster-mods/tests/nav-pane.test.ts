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

  // The test `$` cannot raise ui.close itself (a person's Esc is a live check): the plugin's own close, made by opening
  // another pane, goes through the same hook.
  test("the plugin's own close passes, and a closed pane leaves the sidebar shut", DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    const state = stateOf(on)
    await $.session.start(SESSION)
    await $.command.run(command('tm-session'))
    state.set(`${PLUGIN}.nav`, { sidebar: true, last: 'session' })
    await $.command.run(command('tm-review'))
    expect(world.closed).toEqual(['tm-session'])
    expect(state.get(`${PLUGIN}.nav`)).toEqual({ sidebar: false, last: 'review' })
  })
})
