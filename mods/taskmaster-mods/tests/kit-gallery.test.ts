// User intent: /tm-kit shows every kit primitive in demo mode so the kit can be judged by eye — it opens, draws on the
// terminal and the desktop, a dim keycap says why instead of acting, and the strip on it really moves; tm mode never opens it.
import { describe, expect, mock, test } from 'claude-code/testing'

import { command, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const KIT_PANE = { ...pane('tm-kit'), props: { ...pane('tm-kit').props, bodyColumns: 120 } }

describe('/tm-kit', () => {
  test('demo: /tm-kit opens the gallery, which draws on the terminal and the desktop', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect(await $.command.run(command('tm-kit'))).toEqual({ text: 'Kit gallery opened.' })
    expect(world.opened).toEqual(['tm-kit'])
    for (const surface of ['terminal', 'desktop'] as const) {
      const ui = await $.ui.mount({ plugin: PLUGIN, ...KIT_PANE, surface })
      for (const key of ['page-prev', 'g-refresh', 'g-success', 'g-dim', 'phase-next', 'sel:tm-audit-031', 'g-bulk-done', 'g-confirm-no']) {
        expect(await ui.find({ type: 'Button', key }), `${surface} ${key}`).toBeDefined()
      }
      await ui.unmount()
    }
  })

  test('a dim keycap toasts its reason; a live one toasts that it was pressed', DEMO, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...KIT_PANE })
    await ui.press({ key: 'g-dim' })
    await ui.press({ key: 'g-success' })
    await ui.press({ key: 'page-prev' })
    expect(world.toasts).toEqual(['Nothing to do here', 'pressed success', 'Already on the first page'])
  })

  test('the strip on the gallery moves the review phase, in demo only (no store, no tm)', DEMO, async ($, on) => {
    const clock = mock.clock(on)
    const world = worldOf(on, clock)
    await $.session.start(SESSION)
    const ui = await $.ui.mount({ plugin: PLUGIN, ...KIT_PANE })
    expect((await ui.find({ type: 'Button', key: 'phase-tab:patch-1-9' }))?.props).toMatchObject({ hotkey: '1', label: 'Patch 1.9 · active 3' })
    await ui.press({ key: 'phase-next' })
    await clock.settle()
    await ui.redraw()
    expect((await ui.find({ type: 'Box', key: 'phase-tab:patch-1-9-box' }))?.props.backgroundColor).toBe('#4a5590')
    expect(world.calls).toEqual([])
  })

  test('tm mode: /tm-kit only says the gallery is a demo surface', { plugins: [RR_STUB] }, async ($, on) => {
    const world = worldOf(on, mock.clock(on))
    await $.session.start(SESSION)
    expect((await $.command.run(command('tm-kit'))).text).toMatch(/demo mode/)
    expect(world.opened).toEqual([])
  })
})
