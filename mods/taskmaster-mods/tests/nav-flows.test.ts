// User intent: the navigation flows behind the band's `m` and `r` (spec §15.5, §15.8) — opening any Taskmaster pane remembers
// it, `m` reopens the last one with the sidebar open, `r` asks tm again — driven directly over a recording host.
import { describe, expect, test } from 'claude-code/testing'

import type { TmNav, TmReviewList } from '../types'
import { createFlows, type TmWriter } from '../hooks/flows'
import type { TmHost } from '../hooks/host'
import { FRESH_NAV } from '../hooks/nav'
import { FRESH_REVIEW_LIST } from '../hooks/review-list'

function rig(
  a: { refresh?: () => void; onOpen?: (setNav: (n: TmNav) => void, get: () => TmNav) => void; placed?: boolean; closeClearsSidebar?: boolean } = {},
) {
  const seen = { opened: [] as string[], closed: [] as string[], toasts: [] as string[], nav: FRESH_NAV as TmNav, openNow: [] as string[], list: FRESH_REVIEW_LIST as TmReviewList }
  const host = new Proxy(
    {
      openPane: async (id: string) => {
        seen.opened.push(id)
        a.onOpen?.(n => void (seen.nav = n), () => seen.nav)
        return a.placed ?? true
      },
      closePane: async (id: string) => {
        seen.closed.push(id)
        // Stand-in for the ui.close hook: every close lets the sidebar go.
        if (a.closeClearsSidebar === true) seen.nav = { ...seen.nav, sidebar: false }
      },
      panes: async () => seen.openNow,
      toast: (text: string) => void seen.toasts.push(text),
      after: () => undefined,
    },
    { get: (target, key) => (key in target ? target[key as keyof typeof target] : async () => undefined) },
  ) as unknown as TmHost
  const write = new Proxy(
    {
      nav: async (change: (n: TmNav) => TmNav) => void (seen.nav = change(seen.nav)),
      reviewList: async (change: (l: TmReviewList) => TmReviewList) => void (seen.list = change(seen.list)),
    },
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

describe('one pane at a time', () => {
  test('opening a Taskmaster pane closes the other open Taskmaster panes first, never another plugin pane or itself', async () => {
    const { flows, seen } = rig()
    seen.openNow = ['tm-review', 'tm-kit', 'tm-handovers']
    await flows.openPane('handovers')
    expect(seen.closed).toEqual(['tm-review'])
    expect(seen.opened).toEqual(['tm-handovers'])
  })

  test('goTo closes the sidebar and opens the pane; the pane already open only loses its sidebar', async () => {
    const { flows, seen } = rig()
    seen.nav = { sidebar: true, last: 'session' }
    seen.openNow = ['tm-session']
    await flows.goTo('session')
    expect(seen.opened).toEqual([])
    expect(seen.nav).toEqual({ sidebar: false, last: 'session' })
    seen.nav = { sidebar: true, last: 'session' }
    await flows.goTo('review')
    expect(seen.closed).toEqual(['tm-session'])
    expect(seen.opened).toEqual(['tm-review'])
    expect(seen.nav).toEqual({ sidebar: false, last: 'review' })
  })

  test('m in a pane flips the sidebar', async () => {
    const { flows, seen } = rig()
    await flows.toggleSidebar()
    expect(seen.nav.sidebar).toBe(true)
    await flows.toggleSidebar()
    expect(seen.nav.sidebar).toBe(false)
  })

  test('opening the sidebar lets review ticking go', async () => {
    const { flows, seen } = rig()
    seen.list = { ...seen.list, ticking: true }
    await flows.toggleSidebar()
    expect(seen.list.ticking).toBe(false)
  })

  test('arming review ticking closes the sidebar, so digits 1-4 are never on two Buttons', async () => {
    const { flows, seen } = rig()
    seen.nav = { sidebar: true, last: 'review' }
    await flows.armTick()
    expect(seen.list.ticking).toBe(true)
    expect(seen.nav.sidebar).toBe(false)
  })

  test('a host without panes (older rigs) closes nothing', async () => {
    const { flows, seen } = rig()
    seen.openNow = undefined as unknown as string[]
    await flows.openPane('review')
    expect(seen.closed).toEqual([])
  })

  test('m on the band with two other Taskmaster panes open: both close, then the sidebar opens and stays open', async () => {
    const { flows, seen } = rig({ closeClearsSidebar: true })
    seen.openNow = ['tm-review', 'tm-handovers']
    await flows.openSidebar()
    expect(seen.closed).toEqual(['tm-review', 'tm-handovers'])
    expect(seen.opened).toEqual(['tm-session'])
    expect(seen.nav).toEqual({ sidebar: true, last: 'session' })
  })

  test('m on the band when the terminal is too narrow for the pane leaves the sidebar shut', async () => {
    const { flows, seen } = rig({ placed: false })
    await flows.openSidebar()
    expect(seen.opened).toEqual(['tm-session'])
    expect(seen.nav.sidebar).toBe(false)
  })
})
