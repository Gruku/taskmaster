// User intent: a pane's drawn tree must never exceed scroll.bodyRows, or the engine scrolls the whole body and the pinned header
// and footer move. Renders review, handovers and restore at tiny/medium/large heights with the focus at top, middle and bottom,
// a bulk ask and the phase menu open, and checks the row count and that the footer hints survive.
import { describe, expect, mock, test } from 'claude-code/testing'

import { FRESH_PHASE_VIEW, FRESH_PHASE_VIEWS } from '../hooks/phases'
import { DEMO_PHASES, demoSnapshot } from '../hooks/demo'
import type { TmQueueItem } from '../types'
import { heightOf } from './fixtures/measure'
import { command, pane, PLUGIN, SESSION } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const TM = { plugins: [RR_STUB] }
// Below ~20 rows the pinned header and footer alone fill the pane; 22 is the tiny case that can still show one item.
const HEIGHTS = [22, 30, 60] as const
const TWELVE: TmQueueItem[] = Array.from({ length: 12 }, (_, i) => ({
  kind: 'task',
  id: `t-${String(i + 1).padStart(3, '0')}`,
  title: `Task ${i + 1}`,
  priority: 'high',
  humanAction: `Check ${i + 1}`,
  timestamp: new Date(Date.UTC(2026, 9, 10) - i * 3_600_000).toISOString(),
}))
const SNAP = `${PLUGIN}.snapshot`


const sized = (id: string, rows: number) => ({ ...pane(id, rows), props: { ...pane(id, rows).props, bodyColumns: 120 } })

async function fits(ui: any, rows: number, what: string, hint: string): Promise<void> {
  await ui.redraw()
  const tree = await ui.drawn()
  expect(heightOf(tree), `${what} at ${rows} rows`).toBeLessThanOrEqual(rows)
  expect(JSON.stringify(tree), `${what}: footer hint`).toContain(hint)
}

describe('drawn height never exceeds bodyRows', () => {
  for (const rows of HEIGHTS) {
    test(`review at ${rows} rows: top, middle, bottom, bulk ask, phase menu`, TM, async ($, on) => {
      worldOf(on, mock.clock(on))
      stateOf(on, {
        [SNAP]: { ...demoSnapshot(0), reason: '', queue: TWELVE, queueTotal: 12 },
        [`${PLUGIN}.phaseViews`]: { ...FRESH_PHASE_VIEWS, review: { ...FRESH_PHASE_VIEW, phases: DEMO_PHASES, loaded: true } },
      })
      await $.session.start(SESSION)
      const ui = await $.ui.mount({ plugin: PLUGIN, ...sized('tm-review', rows) })
      await fits(ui, rows, 'top', 'move')
      for (let i = 0; i < 6; i += 1) await ui.press({ key: 'focus-next' })
      await fits(ui, rows, 'middle', 'move')
      for (let i = 0; i < 8; i += 1) await ui.press({ key: 'focus-next' })
      await fits(ui, rows, 'bottom', 'move')
      await ui.press({ key: 'sel-all' })
      await fits(ui, rows, 'selected', 'move')
      await ui.press({ key: 'bulk-done' })
      await fits(ui, rows, 'bulk ask', 'move')
      await ui.press({ key: 'bulk-no' })
      await ui.press({ key: 'phase-field' })
      await fits(ui, rows, 'phase menu', 'move')
    })

    test(`handovers at ${rows} rows: top, middle, bottom, bulk ask, phase menu`, DEMO, async ($, on) => {
      worldOf(on, mock.clock(on))
      await $.session.start(SESSION)
      const ui = await $.ui.mount({ plugin: PLUGIN, ...sized('tm-handovers', rows) })
      await fits(ui, rows, 'top', 'move')
      await ui.press({ key: 'move-down' })
      await ui.press({ key: 'move-down' })
      await fits(ui, rows, 'middle', 'move')
      for (let i = 0; i < 6; i += 1) await ui.press({ key: 'move-down' })
      await fits(ui, rows, 'bottom', 'move')
      await ui.press({ key: 'select-all' })
      await ui.press({ key: 'close-selected' })
      await fits(ui, rows, 'bulk ask', 'move')
      await ui.press({ key: 'bulk-close-no' })
      await ui.press({ key: 'phase-field' })
      await fits(ui, rows, 'phase menu', 'move')
    })

    test(`restore at ${rows} rows: top, middle, bottom, phase menu`, DEMO, async ($, on) => {
      worldOf(on, mock.clock(on))
      await $.session.start(SESSION)
      await $.command.run(command('tm-restore'))
      const ui = await $.ui.mount({ plugin: PLUGIN, ...sized('tm-restore', rows) })
      await fits(ui, rows, 'top', 'move')
      for (let i = 0; i < 3; i += 1) await ui.press({ key: 'restore-down' })
      await fits(ui, rows, 'middle', 'move')
      for (let i = 0; i < 8; i += 1) await ui.press({ key: 'restore-down' })
      await fits(ui, rows, 'bottom', 'move')
      await ui.press({ key: 'phase-field' })
      await fits(ui, rows, 'phase menu', 'move')
    })
  }
})
