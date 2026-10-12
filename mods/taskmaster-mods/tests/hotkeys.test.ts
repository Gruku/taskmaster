// User intent: no two buttons in a drawn pane answer the same key — each of the four panes, plain, with the sidebar open and
// with its phase list open, so a digit never means two things at once.
import { describe, expect, mock, test } from 'claude-code/testing'

import { pane, PLUGIN, SESSION } from './fixtures/inputs'
import { RR_STUB } from './fixtures/rr-stub'
import { stateOf, worldOf } from './fixtures/world'

const DEMO = { options: { source: 'demo' }, plugins: [RR_STUB] }
const PANES = [
  ['tm-session', 'session'],
  ['tm-review', 'review'],
  ['tm-handovers', 'handovers'],
  ['tm-restore', 'restore'],
] as const

type Ui = { findAll: (q: { type: string }) => Promise<{ props: Record<string, unknown> }[]>; find: (q: object) => Promise<unknown>; press: (q: object) => Promise<unknown>; redraw: () => Promise<unknown> }

/** Every hotkey drawn on a Button, with the keys of the Buttons that carry it. */
async function hotkeys(ui: Ui): Promise<Map<string, string[]>> {
  const out = new Map<string, string[]>()
  for (const b of await ui.findAll({ type: 'Button' })) {
    const k = b.props.hotkey
    if (typeof k !== 'string' || b.props.disabled === true) continue
    out.set(k, [...(out.get(k) ?? []), String(b.props.key)])
  }
  return out
}
const clashes = (m: Map<string, string[]>): string[] => [...m].filter(([, keys]) => keys.length > 1).map(([k, keys]) => `${k}: ${keys.join(', ')}`)

describe('hotkeys are unique in every drawn pane state', () => {
  for (const [request, name] of PANES) {
    test(`${name}: plain`, DEMO, async ($, on) => {
      worldOf(on, mock.clock(on))
      stateOf(on)
      await $.session.start(SESSION)
      const ui = await $.ui.mount({ plugin: PLUGIN, ...pane(request) })
      expect(clashes(await hotkeys(ui as unknown as Ui))).toEqual([])
    })

    test(`${name}: sidebar open`, DEMO, async ($, on) => {
      worldOf(on, mock.clock(on))
      const state = stateOf(on)
      await $.session.start(SESSION)
      state.set(`${PLUGIN}.nav`, { sidebar: true, last: name })
      const ui = (await $.ui.mount({ plugin: PLUGIN, ...pane(request) })) as unknown as Ui
      const keys = await hotkeys(ui)
      expect(keys.get('1')).toEqual(['nav:review'])
      expect(clashes(keys)).toEqual([])
    })

    test(`${name}: phase list open`, DEMO, async ($, on) => {
      worldOf(on, mock.clock(on))
      stateOf(on)
      await $.session.start(SESSION)
      const ui = (await $.ui.mount({ plugin: PLUGIN, ...pane(request) })) as unknown as Ui
      if ((await ui.find({ type: 'Button', key: 'phase-field' })) === undefined) {
        expect(name, 'only a pane without a phase field may skip').toBe('session')
        return
      }
      await ui.press({ key: 'phase-field' })
      await ui.redraw()
      expect(clashes(await hotkeys(ui))).toEqual([])
    })
  }
})
