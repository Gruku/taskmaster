// User intent: in a terminal too narrow for panes the plugin opens on its own (about 75 columns), every tab switch and the
// band's m open their pane as the person's own open, the way /tm-review does, one pane at a time; and an open the engine
// still refuses puts the pane it replaced back instead of leaving no pane at all.
import { describe, expect, mock, test } from 'claude-code/testing'

import { holdOpen, whileAsked } from '../hooks/asked'
import { BAND, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const WIDEN = 'taskmaster-mods: widen the terminal to see the pane'

describe('the person asked for it', () => {
  test('the press hook returns only after the pane open its handler started has landed', async () => {
    const order: string[] = []
    let land: () => void = () => undefined
    const opening = new Promise<void>(resolve => (land = resolve)).then(() => void order.push('opened'))
    const press = whileAsked(async () => {
      holdOpen(opening)
      order.push('handler returned')
    }).then(() => void order.push('press done'))
    await Promise.resolve()
    expect(order).toEqual(['handler returned'])
    land()
    await press
    expect(order).toEqual(['handler returned', 'opened', 'press done'])
  })

})

describe('a refused open', () => {
  test('puts the pane it replaced back, says nothing, and leaves one pane', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    world.place = id => id !== 'tm-handovers'
    await $.session.start(SESSION)
    const review = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
    await review.press({ key: 'tab:review' })
    await review.press({ key: 'tab:handovers' })
    expect(world.opened).toEqual(['tm-review', 'tm-handovers', 'tm-review'])
    expect(world.closed).toEqual(['tm-review', 'tm-handovers'])
    expect(world.toasts).not.toContain(WIDEN)
    expect(world.openNow()).toEqual(['tm-review'])
  })

  test('with nothing to put back, says to widen the terminal and leaves no undrawn pane behind', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    world.place = () => false
    await $.session.start(SESSION)
    const band = await $.ui.mount({ plugin: PLUGIN, ...BAND })
    await band.press({ key: 'band-sidebar' })
    expect(world.toasts).toContain(WIDEN)
    expect(world.openNow()).toEqual([])
  })
})

describe('one pane at a time', () => {
  test('each tab switch leaves exactly its own pane open, with no toast', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const first = await $.ui.mount({ plugin: PLUGIN, ...pane('tm-review') })
    await first.press({ key: 'tab:review' })
    await first.unmount()
    for (const [from, to] of [
      ['tm-review', 'handovers'],
      ['tm-handovers', 'session'],
      ['tm-session', 'restore'],
      ['tm-restore', 'review'],
    ] as const) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...pane(from) })
      await ui.press({ key: `tab:${to}` })
      await ui.unmount()
      expect(world.openNow(), to).toEqual([`tm-${to}`])
    }
    expect(world.toasts).not.toContain(WIDEN)
  })
})
