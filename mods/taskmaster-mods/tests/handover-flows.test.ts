// User intent: what the handovers pane's keys do (spec §9, §11), checked on the flows alone — close one or many with one y/n
// and one toast, refusals kept selected, never a second close in flight, the selection and close row reset by any move, the
// closed toggle read once, and a handover opened from the band landing on its phase and page.
import { describe, expect, test } from 'claude-code/testing'

import { demoActions, type TmCloseHandover } from '../hooks/actions'
import { demoHandoverList, demoPhases, demoSnapshot } from '../hooks/demo'
import { createFlows, type TmFlowDeps, type TmWriter } from '../hooks/flows'
import { FRESH_MARKS } from '../hooks/handover-list'
import type { TmHost } from '../hooks/host'
import { resumePrompt } from '../hooks/launch'
import { FRESH_PHASE_VIEWS, phaseKey } from '../hooks/phases'
import type { TmHandoverList, TmHandoverMarks, TmPhasePane, TmPhaseView, TmSnapshot } from '../types'

const HOUR = 3_600_000
const NOW = 200 * HOUR
const ROOT = 'C:\\work\\proj'
const CWD = 'C:\\work\\proj\\sub'
const [A, B, C] = demoSnapshot(NOW).handovers.map(x => x.id) as [string, string, string]

type Mem = {
  snapshot: TmSnapshot | null
  pick: string
  summaryOpen: string
  handoverPage: number
  handoverList: TmHandoverList | null
  handoverMarks: TmHandoverMarks
  phaseViews: Record<TmPhasePane, TmPhaseView>
}

/** Flows over memory: the writer keeps the handovers pane's state, the host records what was asked of the engine. */
function rig(over: Partial<TmFlowDeps> = {}, mem: Partial<Mem> = {}) {
  const state: Mem = {
    snapshot: demoSnapshot(NOW),
    pick: '',
    summaryOpen: '',
    handoverPage: 0,
    handoverList: demoHandoverList(NOW),
    handoverMarks: FRESH_MARKS,
    phaseViews: { ...FRESH_PHASE_VIEWS, handovers: { ...FRESH_PHASE_VIEWS.handovers, phases: demoPhases('handovers'), loaded: true } },
    ...mem,
  }
  const held: Partial<TmWriter> = {
    snapshot: async change => void (state.snapshot = change(state.snapshot)),
    pick: async id => void (state.pick = id),
    summaryOpen: async change => void (state.summaryOpen = change(state.summaryOpen)),
    handoverPage: async page => void (state.handoverPage = page),
    handoverList: async change => void (state.handoverList = change(state.handoverList)),
    handoverMarks: async change => void (state.handoverMarks = change(state.handoverMarks)),
    phaseView: async (pane, change) => void (state.phaseViews = { ...state.phaseViews, [pane]: change(state.phaseViews[pane]) }),
  }
  const write = new Proxy(held, { get: (target, key) => (key in target ? target[key as keyof TmWriter] : async () => undefined) }) as unknown as TmWriter
  const seen = { toasts: [] as string[], fills: [] as string[], focus: [] as string[], opened: [] as string[], later: [] as (() => void)[], store: new Map<string, unknown>() }
  const host = {
    toast: (text: string) => void seen.toasts.push(text),
    fill: async (text: string) => {
      seen.fills.push(text)
      return true
    },
    focus: async (_requestId: string, key: string) => void seen.focus.push(key),
    openPane: async (id: string) => {
      seen.opened.push(id)
      return true
    },
    after: (_ms: number, fn: () => void) => void seen.later.push(fn),
    repoRoot: async () => ROOT,
    cwd: async () => CWD,
    sessionId: async () => 'sess-A',
    storeGet: async (key: string) => seen.store.get(key),
    storeSet: async (key: string, value: unknown) => void seen.store.set(key, value),
    storeDelete: async (key: string) => void seen.store.delete(key),
  } as unknown as TmHost
  const flows = createFlows({ host, write, actions: demoActions(), afterWrite: () => undefined, boundId: async () => null, ...over })
  const settle = async () => {
    while (seen.later.length > 0) seen.later.shift()?.()
    for (let i = 0; i < 30; i += 1) await Promise.resolve()
  }
  return { flows, state, seen, settle }
}

const closer = (refuse: Record<string, string> = {}) => {
  const asked: string[] = []
  const close: TmCloseHandover = async id => {
    asked.push(id)
    return refuse[id] === undefined ? { ok: true } : { ok: false, refusal: refuse[id] }
  }
  return { asked, close }
}

describe('closing handovers', () => {
  test('one call a handover, in order, then one toast; closed ones leave the selection, list and snapshot, refused stay selected', async () => {
    const { asked, close } = closer({ [B]: 'Handover not found: x' })
    let refreshes = 0
    const { flows, state, seen } = rig({ closeHandover: close, refresh: () => void (refreshes += 1) }, { handoverMarks: { ...FRESH_MARKS, selected: [A, B, C] } })
    await flows.closeHandovers([A, B, C])
    expect(asked).toEqual([A, B, C])
    expect(seen.toasts).toEqual([`2 closed · 1 refused: ${B} (Handover not found: x)`])
    expect(state.handoverMarks.selected).toEqual([B])
    expect(state.handoverList?.handovers.filter(x => x.status === 'closed').map(x => x.id)).toEqual(expect.arrayContaining([A, C]))
    expect(state.snapshot?.handovers.map(x => x.id)).not.toContain(A)
    expect(state.snapshot?.handoversTotal).toBe(demoSnapshot(NOW).handoversTotal - 2)
    expect(refreshes).toBe(1)
  })

  test('the y/n row acts only while it is up: y closes what it asked about once; n takes it down; asking focuses n', async () => {
    const { asked, close } = closer()
    const { flows, state, seen } = rig({ closeHandover: close })
    await flows.askClose({ kind: 'one', ids: [A] })
    expect(state.handoverMarks.ask).toEqual({ kind: 'one', ids: [A] })
    expect(seen.focus).toEqual(['close-no'])
    await flows.confirmClose()
    await flows.confirmClose()
    expect(asked).toEqual([A])
    await flows.askClose({ kind: 'bulk', ids: [B, C] })
    expect(seen.focus.at(-1)).toBe('bulk-close-no')
    await flows.cancelClose()
    await flows.confirmClose()
    expect(asked).toEqual([A])
    await flows.askClose({ kind: 'bulk', ids: [] })
    expect(state.handoverMarks.ask).toBeNull()
  })

  test('a handover being closed is never closed a second time in flight', async () => {
    let release = (): void => undefined
    const asked: string[] = []
    const close: TmCloseHandover = async id => {
      asked.push(id)
      await new Promise<void>(resolve => {
        release = resolve
      })
      return { ok: true }
    }
    const { flows, settle } = rig({ closeHandover: close })
    const first = flows.closeHandovers([A])
    const second = flows.closeHandovers([A])
    await settle()
    release()
    await Promise.all([first, second])
    expect(asked).toEqual([A])
  })

  test('with no close dep a close only toasts why; tm reads phases and list again after a close, demo does not', async () => {
    const none = rig()
    await none.flows.closeHandovers([A])
    expect(none.seen.toasts).toEqual(['taskmaster-mods: handovers cannot be closed from here'])
    const reads: string[] = []
    const deps = {
      closeHandover: closer().close,
      readPhases: async (pane: TmPhasePane) => {
        reads.push(`phases:${pane}`)
        return demoPhases(pane)
      },
      readHandoverList: async (closed: boolean) => {
        reads.push(`list:${closed}`)
        return demoHandoverList(NOW)
      },
    }
    await rig({ ...deps, source: 'demo' }).flows.closeHandovers([A])
    expect(reads).toEqual([])
    await rig({ ...deps, source: 'tm' }).flows.closeHandovers([A])
    expect(reads).toEqual(['phases:handovers', 'list:false'])
  })
})

describe('selection, moves and the closed toggle', () => {
  test('s toggles one, all / none set the selection; each, a pick and a phase change take the close row down', async () => {
    const { flows, state } = rig()
    await flows.selectHandover(A)
    await flows.selectHandover(B)
    await flows.selectHandover(A)
    expect(state.handoverMarks.selected).toEqual([B])
    await flows.askClose({ kind: 'bulk', ids: [B] })
    await flows.selectHandovers([A, B, C])
    expect(state.handoverMarks).toMatchObject({ selected: [A, B, C], ask: null })
    await flows.askClose({ kind: 'one', ids: [A] })
    await flows.pick(B)
    expect(state.handoverMarks.ask).toBeNull()
    await flows.choosePhase('handovers', 'patch-1-9')
    expect(state.handoverMarks.selected).toEqual([])
  })

  test('j / k show the page, pick the handover and move the focus to its row', async () => {
    const { flows, state, seen } = rig()
    await flows.moveTo(1, C)
    expect([state.handoverPage, state.pick]).toEqual([1, C])
    expect(seen.focus).toEqual([`ho:${C}`])
  })

  test('◐ closed flips, starts over at the top, and reads the closed ones only when the list holds none', async () => {
    const reads: boolean[] = []
    const readHandoverList = async (closed: boolean) => {
      reads.push(closed)
      return { ...demoHandoverList(NOW), closed }
    }
    const { flows, state } = rig({ readHandoverList }, { handoverList: { ...demoHandoverList(NOW), closed: false }, handoverPage: 1, pick: C })
    await flows.toggleClosed()
    expect(state.handoverMarks.showClosed).toBe(true)
    expect([state.handoverPage, state.pick]).toEqual([0, ''])
    expect(reads).toEqual([true])
    await flows.toggleClosed()
    await flows.toggleClosed()
    expect(reads).toEqual([true])
    expect(state.handoverMarks.showClosed).toBe(true)
  })
})

describe('opening the pane', () => {
  test('/tm-handovers starts over and reads the phases, then the list, off the open', async () => {
    const reads: string[] = []
    const { flows, state, seen, settle } = rig(
      {
        readPhases: async pane => {
          reads.push(`phases:${pane}`)
          return demoPhases(pane)
        },
        readHandoverList: async closed => {
          reads.push(`list:${closed}`)
          return demoHandoverList(NOW)
        },
      },
      { pick: B, handoverPage: 1, handoverMarks: { ...FRESH_MARKS, selected: [A], ask: { kind: 'one', ids: [A] } } },
    )
    await flows.openHandovers()
    expect(seen.opened).toEqual(['tm-handovers'])
    expect([state.pick, state.handoverPage, state.handoverMarks.selected, state.handoverMarks.ask]).toEqual(['', 0, [], null])
    expect(reads).toEqual([])
    await settle()
    expect(reads).toEqual(['phases:handovers', 'list:false'])
  })

  test('band or Session h: the handover lands under its phase (kept for the project) on its page, focused', async () => {
    const hang = '2026-10-02-store-read-hang'
    const { flows, state, seen } = rig({ readPhases: async pane => demoPhases(pane), source: 'tm' })
    state.phaseViews = { ...state.phaseViews, handovers: { ...state.phaseViews.handovers, choice: 'patch-1-9' } }
    await flows.openHandover(hang)
    expect(state.phaseViews.handovers.choice).toBe('release-2')
    expect(seen.store.get(phaseKey('handovers', ROOT))).toEqual({ phase: 'release-2' })
    expect([state.pick, state.handoverPage]).toEqual([hang, 0])
    expect(seen.focus.at(-1)).toBe(`ho:${hang}`)
    await flows.openHandover('not-in-the-list')
    expect([state.pick, state.handoverPage]).toEqual(['', 0])
    expect(seen.toasts).toEqual(['taskmaster-mods: handover not-in-the-list is not in the open list'])
    expect(state.phaseViews.handovers.choice).toBe('release-2')
  })
})

describe('resume and new tab', () => {
  test('r fills the resume line and says whether the prompt took it; w is a fresh launch here, titled as the Session tab titles it', async () => {
    const { flows, seen } = rig()
    const ho = demoSnapshot(NOW).handovers[0]!
    expect(await flows.resumeHandover(ho)).toBe(true)
    expect(seen.fills).toEqual([resumePrompt(ho.id, ho.path)])
    expect(await flows.handoverLaunch(ho)).toEqual({ cwd: CWD, title: `handover ${ho.id}`, mode: 'fresh', prompt: resumePrompt(ho.id, ho.path) })
  })
})
