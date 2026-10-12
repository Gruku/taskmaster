// User intent: the navigation flows behind the band's `m` and `r` (spec §15.5, §15.8) — opening any Taskmaster pane remembers
// it, `m` reopens the last one with the sidebar open, `r` asks tm again — driven directly over a recording host.
import { describe, expect, test } from 'claude-code/testing'

import type { TmNav } from '../types'
import { createFlows, type TmWriter } from '../hooks/flows'
import type { TmHost } from '../hooks/host'
import { FRESH_NAV } from '../hooks/nav'

function rig(a: { refresh?: () => void; onOpen?: (setNav: (n: TmNav) => void, get: () => TmNav) => void } = {}) {
  const seen = { opened: [] as string[], closed: [] as string[], toasts: [] as string[], nav: FRESH_NAV as TmNav }
  const host = new Proxy(
    {
      openPane: async (id: string) => {
        seen.opened.push(id)
        a.onOpen?.(n => void (seen.nav = n), () => seen.nav)
        return true
      },
      closePane: async (id: string) => void seen.closed.push(id),
      toast: (text: string) => void seen.toasts.push(text),
      after: () => undefined,
    },
    { get: (target, key) => (key in target ? target[key as keyof typeof target] : async () => undefined) },
  ) as unknown as TmHost
  const write = new Proxy(
    { nav: async (change: (n: TmNav) => TmNav) => void (seen.nav = change(seen.nav)) },
    { get: (target, key) => (key in target ? target[key as keyof typeof target] : async () => undefined) },
  ) as unknown as TmWriter
  const flows = createFlows({
    host,
    write,
    actions: { done: async () => ({ ok: true }), backToAgent: async () => ({ ok: true }) },
    afterWrite: () => undefined,
    boundId: async () => null,
    ...(a.refresh === undefined ? { source: 'demo' as const } : { refresh: a.refresh }),
  })
  return { flows, seen }
}

describe('navigation flows', () => {
  test('opening a pane remembers it as the last one', async () => {
    const { flows, seen } = rig()
    await flows.openPane('handovers')
    expect(seen.nav).toEqual({ sidebar: false, last: 'handovers' })
    await flows.openPane('review')
    expect(seen.nav.last).toBe('review')
  })

  test('m on the band opens the last pane (Session at first) with the sidebar open', async () => {
    const { flows, seen } = rig()
    await flows.openSidebar()
    expect(seen.opened).toEqual(['tm-session'])
    expect(seen.nav).toEqual({ sidebar: true, last: 'session' })
    await flows.openPane('review')
    seen.nav = { ...seen.nav, sidebar: false }
    await flows.openSidebar()
    expect(seen.opened).toEqual(['tm-session', 'tm-review', 'tm-review'])
    expect(seen.nav).toEqual({ sidebar: true, last: 'review' })
  })

  test('the sidebar is written after the pane opens, so a close the open triggers cannot clear it', async () => {
    // Stand-in for Task C1: opening a pane closes the other panes, and each close clears nav.sidebar.
    const { flows, seen } = rig({ onOpen: (setNav, get) => setNav({ ...get(), sidebar: false }) })
    await flows.openSidebar()
    expect(seen.opened).toEqual(['tm-session'])
    expect(seen.nav.sidebar).toBe(true)
  })

  test('r asks tm again; demo says it has nothing newer', async () => {
    let asked = 0
    const live = rig({ refresh: () => void (asked += 1) })
    await live.flows.retry()
    expect(asked).toBe(1)
    const demo = rig()
    await demo.flows.retry()
    expect(demo.seen.toasts).toEqual(['taskmaster-mods: demo data has nothing newer to read'])
  })
})
