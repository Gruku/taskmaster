// User intent: when rr-tui is too old (no badgeProps/tint) the band and every pane must say so in plain text, not draw blank.
import { describe, expect, mock, type Plugin, test } from 'claude-code/testing'

import { RR_OUTDATED } from '../hooks/kit'
import { BAND, PLUGIN, pane, SESSION } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { worldOf } from './fixtures/world'

// The stub as rr-tui 0.1.0 shipped it: a plugin after RR_STUB that leaves $.rr without badgeProps and tint.
const OLD_RR: Plugin = {
  name: 'rr-tui-old',
  register(on) {
    on('engine.create', async ($, e, next) => {
      const built = await next(e)
      return { ...built, rr: { tokens: async () => ({ polarity: 'dark' }) } } as never
    })
  },
}
const OLD = { options: { source: 'demo' }, plugins: [RR_STUB, OLD_RR] }

describe('outdated rr-tui', () => {
  test('the band says rr-tui is outdated instead of drawing blank', OLD, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    expect(await ui.find({ type: 'Text', text: RR_OUTDATED })).toBeDefined()
  })

  test('every pane says so too', OLD, async ($, on) => {
    worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    for (const id of ['tm-review', 'tm-handovers', 'tm-session', 'tm-restore']) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...pane(id) })
      expect(await ui.find({ type: 'Text', text: RR_OUTDATED }), id).toBeDefined()
    }
  })
})
