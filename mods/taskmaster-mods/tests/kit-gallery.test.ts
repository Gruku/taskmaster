// User intent: /tm-kit shows the rework's component sheet in demo mode so the vocabulary is judged by eye — it opens, draws
// every element once on the terminal and the desktop, its keys press, a disabled key says why, its picker really moves.
import { describe, expect, mock, test } from 'claude-code/testing'

import { command, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const KIT_PANE = { ...pane('tm-kit'), props: { ...pane('tm-kit').props, bodyColumns: 120 } }

describe('/tm-kit, the component sheet', () => {
  test('demo: /tm-kit opens the sheet; every element draws on the terminal and the desktop', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect(await $.command.run(command('tm-kit'))).toEqual({ text: 'Kit gallery opened.' })
    expect(world.opened).toEqual(['tm-kit'])
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...KIT_PANE, surface })
      for (const text of ['FRAME', 'BAND', 'SIDEBAR', 'TOOLBAR', 'SUMMARY', 'BULK', 'SECTION', 'BADGE', 'ITEM', 'CARD', 'FIELD', 'SELECTION', 'KEY 1', 'KEY 2', 'KEY 3', 'KEY 4', 'KEY 5', 'FOOTER', 'EMPTY', 'OFFLINE']) {
        expect(await ui.find({ type: 'Text', text: text.padEnd(12) }), `${surface} ${text}`).toBeDefined()
      }
      for (const key of ['g-primary', 'g-secondary', 'g-disabled', 'g-destructive', 'phase-field', 'nav:review', 'sel:docs-012', 'page-next']) {
        expect(await ui.find({ type: 'Button', key }), `${surface} ${key}`).toBeDefined()
      }
      await ui.unmount()
    }
  })

  test('keys toast that they were pressed; a disabled key says why', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...KIT_PANE })
    await ui.press({ key: 'g-disabled' })
    await ui.press({ key: 'g-primary' })
    await ui.press({ key: 'page-prev' })
    expect(world.toasts).toEqual(['Nothing selected to close', 'pressed g-primary', 'Already on the first page'])
  })

  test('the picker on the sheet moves the review phase, in demo only (no store, no tm)', DEMO, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...KIT_PANE })
    expect((await ui.find({ type: 'Button', key: 'phase-field' }))?.props?.label).toBe(' All  5 ▾ ')
    await ui.press({ key: 'phase-next' })
    await clock.settle()
    await ui.redraw()
    expect((await ui.find({ type: 'Button', key: 'phase-field' }))?.props?.label).toBe(' Patch 1.9 · active  3 ▾ ')
    expect(world.calls).toEqual([])
  })

  test('tm mode: /tm-kit only says the gallery is a demo surface', { plugins: [RR_STUB] }, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect((await $.command.run(command('tm-kit'))).text).toMatch(/demo mode/)
    expect(world.opened).toEqual([])
  })
})
