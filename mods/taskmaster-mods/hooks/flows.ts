// User intent: what every key in the band, the review queue and the handovers pane does, in one place, so the band and the
// pane can never disagree and every write goes through one explicit confirmation and at most one write per task at a time.
import type { RenderSurface } from 'claude-code'

import type {
  TmBandMode,
  TmCloseAsk,
  TmCursor,
  TmHandover,
  TmHandoverList,
  TmHandoverMarks,
  TmHandoverNotice,
  TmHandoverSummary,
  TmNav,
  TmPhase,
  TmPhasePane,
  TmPhaseView,
  TmReviewList,
  TmSnapshot,
  TmTaskDetail,
} from '../types'
import type { TmActions, TmCloseHandover } from './actions'
import { arrival, closeToast, FRESH_MARKS, listedHandovers, markClosed, withoutHandovers } from './handover-list'
import type { TmHost } from './host'
import { launchAll, type LaunchResult, type LaunchSpec, launchSummary, resumePrompt } from './launch'
import { PANE_IDS, paneOf, TM_PANE_IDS } from './nav'
import {
  afterDone,
  afterSendBack,
  FRESH_CURSOR,
  handoverCopyText,
  HANDOVERS,
  PAGE_SIZE,
  RESTORE,
  restoreHiddenKey,
  REVIEW,
  SESSION,
  TICKS_PREFIX,
  ticksOf,
  toggleTick,
} from './model'
import { ALL, fallback, FRESH_PHASE_VIEW, isDoneShown, orderPhases, phaseStoreOf, saveChoice } from './phases'
import { type BulkResult, bulkSummary, FRESH_REVIEW_LIST, toggled } from './review-list'
import { recordResumedFrom } from './resumed'

export type TmWriter = {
  snapshot: (change: (s: TmSnapshot | null) => TmSnapshot | null) => Promise<void>
  cursor: (change: (c: TmCursor) => TmCursor) => Promise<void>
  band: (change: (b: TmBandMode) => TmBandMode) => Promise<void>
  pick: (id: string) => Promise<void>
  details: (change: (d: Readonly<Record<string, TmTaskDetail>>) => Readonly<Record<string, TmTaskDetail>>) => Promise<void>
  ticks: (change: (t: Readonly<Record<string, readonly string[]>>) => Readonly<Record<string, readonly string[]>>) => Promise<void>
  detailsOpen: (change: (open: boolean) => boolean) => Promise<void>
  summaries: (
    change: (s: Readonly<Record<string, TmHandoverSummary>>) => Readonly<Record<string, TmHandoverSummary>>,
  ) => Promise<void>
  summaryOpen: (change: (id: string) => string) => Promise<void>
  notice: (change: (n: TmHandoverNotice | null) => TmHandoverNotice | null) => Promise<void>
  phaseView: (pane: TmPhasePane, change: (v: TmPhaseView) => TmPhaseView) => Promise<void>
  handoverPage: (page: number) => Promise<void>
  handoverList: (change: (l: TmHandoverList | null) => TmHandoverList | null) => Promise<void>
  handoverMarks: (change: (m: TmHandoverMarks) => TmHandoverMarks) => Promise<void>
  reviewList: (change: (l: TmReviewList) => TmReviewList) => Promise<void>
  resumedFrom: (handoverId: string) => Promise<void>
  restoreHidden: (epoch: number) => Promise<void>
  nav: (change: (n: TmNav) => TmNav) => Promise<void>
}

export type TmFlowDeps = {
  host: TmHost
  write: TmWriter
  actions: TmActions
  /** After a write: `failed` is a refused send-back, or a timed-out write that has now answered; each asks for a refresh. */
  afterWrite: (taskId: string, outcome: 'done' | 'sent-back' | 'failed') => void
  /**
   * The task this session is bound to (tm: the session binding; demo: the seeded bound task), or null. The band's d and a
   * act only on it (ruling F1): a task shown as inferred, or one the binding moved off, is refused, never looked up.
   */
  boundId: () => Promise<string | null>
  /** Which data the flows act on: demo never reaches the tm server (the viewer is only announced). Default tm. */
  source?: 'tm' | 'demo'
  /**
   * Reads a handover's decisions and blockers (tm: backlog_handover_get with sections decisions + blockers), or null when it
   * cannot. Absent in demo, whose summaries are seeded like the task details.
   */
  summary?: (handoverId: string) => Promise<TmHandoverSummary | null>
  /** Reads one pane's phases for its strip (null: could not; the strip stays as it was). Demo answers its own lists. */
  readPhases?: (pane: TmPhasePane) => Promise<readonly TmPhase[] | null>
  /** A pane's strip moved to this phase id ('' All): review's scope follows and a refresh is asked. */
  phaseChosen?: (pane: TmPhasePane, phaseId: string) => void
  /** The handovers pane's own read (tm: readHandoverList). Absent in demo, whose list is seeded with the snapshot. */
  readHandoverList?: (closed: boolean) => Promise<TmHandoverList | null>
  /** A handover the pane's own list holds (up to 100, closed ones too): copyHandover looks here after the snapshot window. */
  findHandover?: (handoverId: string) => Promise<TmHandover | null>
  /** Closes one handover (tm: backlog_handover_update_status; demo: always closes). Absent: a close only toasts why not. */
  closeHandover?: TmCloseHandover
  /** Resolves the session's handover again (D5) after something it depends on changed. */
  resolve?: () => Promise<void>
  /** Reads one handover by id (tm: backlog_handover_get), for a copy the open list and the notice cannot answer. */
  readHandover?: (handoverId: string) => Promise<TmHandover | null>
  /** Asks tm for a fresh snapshot (u, Retry); absent in demo, whose data does not change underneath. */
  refresh?: () => void
}

/** The surfaces a key can open: band and Session `1` `2` `3` `4`. */
export type TmPane = 'review' | 'handovers' | 'session' | 'restore'

export type TmFlows = ReturnType<typeof createFlows>

const PROMPT_REFUSED = 'taskmaster-mods: the prompt did not take the text; close the dialog and try again'
const notBound = (id: string, what: string): string => `${id} is not this session's task: ${what} it from the review queue (1)`
/** A write still running after its timeout, wrapped so an async function hands it back unawaited. */
type Hold = { readonly pending: Promise<unknown> } | undefined
const holdOf = (out: { readonly pending?: Promise<unknown> }): Hold => (out.pending === undefined ? undefined : { pending: out.pending })
const UNAVAILABLE: TmHandoverSummary = { decisions: [], blockers: [], unavailable: true }
/** A launch's commands may take a moment to hand the tab over (wt.exe returns at once; osascript waits for the app). */
const LAUNCH_TIMEOUT_MS = 15_000

export function createFlows(d: TmFlowDeps) {
  const busy = new Set<string>()
  // Summaries asked for since the last refresh; `generation` moves on at each refresh, so a read begun before it never
  // writes its (possibly stale) answer after it.
  const summaryAsked = new Set<string>()
  let generation = 0
  const askSummary = async (id: string): Promise<void> => {
    if (d.summary === undefined || summaryAsked.has(id)) return
    summaryAsked.add(id)
    const asked = generation
    const summary = await d.summary(id)
    if (asked !== generation) return
    if (summary !== null) {
      await d.write.summaries(all => ({ ...all, [id]: summary }))
      return
    }
    // A failed read keeps what is cached (stale but shown; asked again after the next refresh). Only with nothing cached
    // does the card say "summary unavailable", and then the next expand asks again.
    let cached = false
    await d.write.summaries(all => {
      const held = all[id]
      cached = held !== undefined && held.unavailable !== true
      return cached ? all : { ...all, [id]: UNAVAILABLE }
    })
    if (!cached) summaryAsked.delete(id)
  }
  // One write per task at a time. A write that timed out is still running (`work` hands back its pending call): the task
  // stays busy until that call answers, so a re-confirm never sends a second write beside it; then a refresh shows what it did.
  const held = new Set<string>()
  const claim = (id: string): boolean => {
    if (busy.has(id)) return false
    busy.add(id)
    return true
  }
  const release = (id: string, pending: Promise<unknown> | undefined): void => {
    if (pending === undefined) {
      busy.delete(id)
      return
    }
    held.add(id)
    const free = () => {
      held.delete(id)
      busy.delete(id)
      d.afterWrite(id, 'failed')
    }
    void pending.then(free, free)
  }
  const once = async (id: string, work: () => Promise<Hold>): Promise<void> => {
    if (!claim(id)) {
      if (held.has(id)) {
        // Not left armed either: once the earlier call answers, a y must not fire off this press.
        await disarm(id)
        d.host.toast(`taskmaster-mods: the earlier write on ${id} has not answered yet; try again once it has`)
      }
      return
    }
    let pending: Promise<unknown> | undefined
    try {
      pending = (await work())?.pending
    } finally {
      release(id, pending)
    }
  }
  // The bulk row acts once, on exactly the ids it was asked for: the check and the move to `busy` are one write, so a second
  // y, or a y on a row drawn before a cancel, finds nothing armed and sends nothing.
  const bulkArmed = async (kind: 'done' | 'agent', ids: readonly string[]): Promise<boolean> => {
    let armed = false
    await d.write.reviewList(l => {
      armed = l.bulk === kind && l.bulkIds.length === ids.length && l.bulkIds.every((id, i) => id === ids[i])
      return armed ? { ...l, bulk: 'busy' } : l
    })
    return armed
  }
  // A single card's y/n or note and the bulk row never stand together; a running bulk is left to finish.
  const quietList = (l: TmReviewList): TmReviewList => ({ ...l, ticking: false, ...(l.bulk === 'busy' ? {} : { bulk: 'none' as const, bulkIds: [] }) })
  // Any write's outcome on a task, from either surface, takes down every confirm or note still up for it on both: an arm
  // left behind (on a row hidden for now) would sign off a round the person never pressed d on.
  const disarm = async (id: string): Promise<void> => {
    await d.write.cursor(c => (c.currentId === id && c.mode !== 'card' ? { ...c, mode: 'card' } : c))
    await d.write.band(b => (b.confirmingId === id ? { ...b, confirmingId: '' } : b))
  }
  // A write acts only while the row that asked for it is still up for that task: a press after the write landed or was
  // refused, after a cancel, or on a row drawn before a refresh finds nothing armed and sends nothing (never twice).
  const paneArmed = async (id: string, mode: TmCursor['mode']): Promise<boolean> => {
    let armed = false
    await d.write.cursor(c => {
      armed = c.mode === mode && c.currentId === id
      return c
    })
    return armed
  }
  const bandArmed = async (id: string): Promise<boolean> => {
    let armed = false
    await d.write.band(b => {
      armed = b.confirmingId === id
      return b
    })
    return armed
  }
  // A signed-off task leaves both the pane and the band: no confirm row on it stays up anywhere to be pressed again.
  const signedOff = async (id: string): Promise<void> => {
    await d.write.snapshot(s => (s === null ? s : afterDone(s, id)))
    await d.write.cursor(c => (c.currentId === id ? { ...c, mode: 'card', refusal: '', currentId: '' } : c))
    await d.write.band(b => (b.confirmingId === id || b.refusalId === id ? { confirmingId: '', refusal: '' } : b))
    d.afterWrite(id, 'done')
  }
  const pick = async (id: string): Promise<void> => {
    await d.write.pick(id)
    await d.write.summaryOpen(open => (open === id ? open : ''))
    await d.write.handoverMarks(m => (m.ask === null ? m : { ...m, ask: null }))
  }
  const isBound = async (id: string): Promise<boolean> => (await d.boundId()) === id
  // One Taskmaster pane at a time (spec §15.8 S2): the plugin's other open Taskmaster panes close first, so the engine never
  // draws its tab row. A host without `panes` (or one answering something else) closes nothing.
  const closeOthers = async (keep: string): Promise<void> => {
    const open = await d.host.panes?.()
    if (!Array.isArray(open)) return
    for (const id of open) if (id !== keep && TM_PANE_IDS.includes(id)) await d.host.closePane(id)
  }
  // Whether the last pane opened was placed (false: the terminal is too narrow); openSidebar reads it.
  const placed = { last: true }
  // Every Taskmaster pane opens through here: it becomes the pane the band's `m` reopens (spec §15.8).
  const open = async (id: string, title: string): Promise<void> => {
    await closeOthers(id)
    const pane = paneOf(id)
    if (pane !== null) await d.write.nav(n => (n.last === pane ? n : { ...n, last: pane }))
    placed.last = await d.host.openPane(id, title)
    if (!placed.last) d.host.toast('taskmaster-mods: widen the terminal to see the pane')
  }
  const fill = async (text: string): Promise<void> => {
    if (!(await d.host.fill(text))) d.host.toast(PROMPT_REFUSED)
  }
  // A handover's Telegram-ready block by id: this session's notice (its block is the receipt's own), else the open list,
  // else (tm) one read by id. Read through the writers, which hand back the current state unchanged.
  const handoverText = async (id: string): Promise<{ readonly text: string; readonly path: string } | null> => {
    const seen: { notice: TmHandoverNotice | null; listed: TmHandover | undefined } = { notice: null, listed: undefined }
    await d.write.notice(n => {
      seen.notice = n
      return n
    })
    if (seen.notice !== null && seen.notice.id === id) return { text: seen.notice.text, path: seen.notice.path }
    await d.write.snapshot(s => {
      seen.listed = s?.handovers.find(h => h.id === id)
      return s
    })
    const found = seen.listed ?? (await d.findHandover?.(id)) ?? (await d.readHandover?.(id)) ?? null
    return found === null ? null : { text: handoverCopyText(found), path: found.path }
  }
  // /tm-handovers, band and Session `2`: the pane from its top, nothing selected; it draws at once (the snapshot's window),
  // then its phases and its own list land, read off the open.
  const openHandovers = async (): Promise<void> => {
    await d.write.pick('')
    await d.write.summaryOpen(() => '')
    await d.write.handoverPage(0)
    await unmark()
    await open(HANDOVERS, 'Handovers')
    d.host.after(0, () => {
      void reloadHandovers().catch(() => undefined)
    })
  }
  const keepChoice = async (pane: TmPhasePane, phaseId: string): Promise<void> => {
    if (d.source !== 'demo') await saveChoice(phaseStoreOf(d.host), pane, await d.host.repoRoot(), phaseId)
  }
  // A pane's phases, read fresh: a remembered choice the list no longer holds falls back to All, with a toast (spec §5).
  const loadPhases = async (pane: TmPhasePane): Promise<readonly TmPhase[] | null> => {
    const phases = (await d.readPhases?.(pane)) ?? null
    if (phases === null) return null
    let lost = ''
    await d.write.phaseView(pane, v => {
      const kept = fallback({ ...v, phases, loaded: true })
      lost = kept.lost
      return { ...v, phases, loaded: true, choice: kept.choice }
    })
    if (lost !== '') {
      await keepChoice(pane, ALL)
      d.host.toast(`taskmaster-mods: phase ${lost} no longer exists; showing All`)
      d.phaseChosen?.(pane, ALL)
    }
    return phases
  }
  // A tab, ‹ ›, a digit, an overlay pick or 0 All: kept per pane and project (not in demo), the overlay closes, and the pane
  // starts over at its top (review: page 1, its first card focused, nothing selected that the new phase does not show).
  const choosePhase = async (pane: TmPhasePane, phaseId: string): Promise<void> => {
    let changed = false
    await d.write.phaseView(pane, v => {
      changed = v.choice !== phaseId
      return { ...v, choice: phaseId, overlay: false }
    })
    if (!changed) return
    await keepChoice(pane, phaseId)
    if (pane === 'review') {
      await d.write.cursor(c => ({ ...c, mode: 'card', refusal: '', currentId: '' }))
      await d.write.reviewList(() => FRESH_REVIEW_LIST)
    }
    if (pane === 'handovers') {
      await d.write.handoverPage(0)
      await pick('')
      await d.write.handoverMarks(m => ({ ...m, selected: [], ask: null }))
    }
    d.phaseChosen?.(pane, phaseId)
  }
  // The handovers pane's own list (spec §9), read with `◐ closed`'s current setting; a failed read keeps what is drawn.
  const loadHandovers = async (): Promise<void> => {
    if (d.readHandoverList === undefined) return
    const held = { closed: false }
    await d.write.handoverMarks(m => {
      held.closed = m.showClosed
      return m
    })
    const list = await d.readHandoverList(held.closed)
    if (list !== null) await d.write.handoverList(() => list)
  }
  // The pane's phases, then its list: one call after another (the server answers one at a time).
  const reloadHandovers = async (): Promise<void> => {
    await loadPhases('handovers')
    await loadHandovers()
  }
  const unmark = async (): Promise<void> => {
    await d.write.handoverMarks(m => ({ ...m, selected: [], ask: null }))
  }
  // Ids being closed now: a second y (or a bulk row drawn before the first answered) never closes one twice.
  const closing = new Set<string>()
  /**
   * Closes handovers one at a time (backlog_handover_update_status), then one toast for all of them (spec §11): closed ones
   * leave the selection, turn closed in the list and leave the snapshot's window; refused ones stay selected.
   */
  const closeHandovers = async (ids: readonly string[]): Promise<void> => {
    const close = d.closeHandover
    if (close === undefined) {
      d.host.toast('taskmaster-mods: handovers cannot be closed from here')
      return
    }
    const todo = ids.filter(id => !closing.has(id))
    for (const id of todo) closing.add(id)
    const closed: string[] = []
    const refused: { id: string; refusal: string }[] = []
    try {
      for (const id of todo) {
        const out = await close(id)
        if (out.ok) closed.push(id)
        else refused.push({ id, refusal: out.refusal })
      }
    } finally {
      for (const id of todo) closing.delete(id)
    }
    if (todo.length === 0) return
    if (closed.length > 0) {
      await d.write.handoverMarks(m => ({ ...m, selected: m.selected.filter(id => !closed.includes(id)) }))
      await d.write.handoverList(l => (l === null ? l : markClosed(l, closed)))
      await d.write.snapshot(s => (s === null ? s : withoutHandovers(s, closed)))
    }
    d.host.toast(closeToast(closed, refused))
    if (closed.length === 0) return
    d.refresh?.()
    if (d.source !== 'demo') await reloadHandovers()
  }
  const moveTo = async (page: number, id: string): Promise<void> => {
    await d.write.handoverPage(page)
    await pick(id)
    await d.host.focus(HANDOVERS, `ho:${id}`)
  }
  // What the handovers pane draws from, read through the writers (each change hands its value back unchanged).
  const handoverState = async () => {
    const at: { list: TmHandoverList | null; snapshot: TmSnapshot | null; marks: TmHandoverMarks; view: TmPhaseView } = {
      list: null,
      snapshot: null,
      marks: FRESH_MARKS,
      view: FRESH_PHASE_VIEW,
    }
    await d.write.handoverList(l => {
      at.list = l
      return l
    })
    await d.write.snapshot(s => {
      at.snapshot = s
      return s
    })
    await d.write.handoverMarks(m => {
      at.marks = m
      return m
    })
    await d.write.phaseView('handovers', v => {
      at.view = v
      return v
    })
    return at
  }
  const openReview = async (pin = '', mode: TmCursor['mode'] = 'card'): Promise<void> => {
    await d.write.cursor(() => ({ ...FRESH_CURSOR, currentId: pin, mode }))
    await d.write.reviewList(() => FRESH_REVIEW_LIST)
    await open(REVIEW, 'Review')
    // The phase list is read when the pane opens, off the open: the pane draws at once, the filter label follows.
    if (pin === '') {
      d.host.after(0, () => {
        void loadPhases('review').catch(() => undefined)
      })
    }
    if (mode === 'note') await d.host.focus(REVIEW, 'note')
  }

  /** A pane, opened as its own command opens it. */
  const openPane = async (pane: TmPane): Promise<void> => {
    if (pane === 'review') return openReview()
    if (pane === 'handovers') return openHandovers()
    return pane === 'session' ? open(SESSION, 'Session') : open(RESTORE, 'Restore')
  }

  return {
    openReview,
    openHandovers,
    openPane,
    /**
     * The band's `m`: the last Taskmaster pane (Session at first), opened with the sidebar open over it. The sidebar is
     * written after the open: opening a pane closes the other panes, and each close clears nav.sidebar (Task C1).
     */
    openSidebar: async (): Promise<void> => {
      const last: { pane: TmPane } = { pane: 'session' }
      await d.write.nav(n => {
        last.pane = n.last
        return n
      })
      placed.last = true
      await openPane(last.pane)
      // A pane the terminal could not place has no sidebar to show: nav.sidebar stays shut.
      if (placed.last) await d.write.nav(n => (n.sidebar ? n : { ...n, sidebar: true }))
    },
    /** A sidebar entry: the sidebar closes and that pane opens; the pane already open only loses its sidebar. */
    goTo: async (pane: TmPane): Promise<void> => {
      await d.write.nav(n => (n.sidebar ? { ...n, sidebar: false } : n))
      const open = await d.host.panes?.()
      if (Array.isArray(open) && open.includes(PANE_IDS[pane])) return
      await openPane(pane)
    },
    /** `m` in a pane: the sidebar opens or closes; opening it closes every phase list and lets review's ticking go. */
    toggleSidebar: async (): Promise<void> => {
      const was = { open: false }
      await d.write.nav(n => {
        was.open = n.sidebar
        return { ...n, sidebar: !n.sidebar }
      })
      if (was.open) return
      for (const pane of ['review', 'handovers', 'restore'] as const) await d.write.phaseView(pane, v => (v.overlay ? { ...v, overlay: false } : v))
      await d.write.reviewList(l => (l.ticking ? { ...l, ticking: false } : l))
    },
    /** The band's `r` (tm offline) and Session's `u`: a fresh snapshot from tm; demo says it has nothing newer. */
    retry: async (): Promise<void> => {
      if (d.refresh === undefined) d.host.toast('taskmaster-mods: demo data has nothing newer to read')
      else d.refresh()
    },
    /**
     * Session `h` (spec §9): the pane opens on that handover at once, then, once its phases and list are read, shows it under
     * its phase (the chosen one when that holds it) on its page, focused. One the list does not hold leaves page 1 unpicked, and says so.
     */
    openHandover: async (handoverId: string): Promise<void> => {
      await d.write.pick(handoverId)
      await d.write.summaryOpen(() => '')
      await d.write.handoverPage(0)
      await unmark()
      await open(HANDOVERS, 'Handovers')
      await reloadHandovers()
      const at = await handoverState()
      const listed = listedHandovers(at.list, at.snapshot, at.marks.showClosed)
      const found = arrival(listed, handoverId, at.list?.tasks ?? {}, at.view.phases, at.view.choice)
      if (found === null) {
        await d.write.pick('')
        d.host.toast(`taskmaster-mods: handover ${handoverId} is not in the open list`)
        return
      }
      if (found.choice !== at.view.choice) await choosePhase('handovers', found.choice)
      await d.write.handoverPage(found.page)
      await pick(handoverId)
      await d.host.focus(HANDOVERS, `ho:${handoverId}`)
    },
    /** Band `x`: the restore burst's `4` goes for good (kept per burst, not in demo). */
    hideRestore: async (epoch: number): Promise<void> => {
      if (d.source !== 'demo') await d.host.storeSet(restoreHiddenKey(epoch), true)
      await d.write.restoreHidden(epoch)
    },
    /** New terminal tabs, 700 ms apart (launch.ts), then one toast; demo opens nothing and says what it would open. */
    launch: async (specs: readonly LaunchSpec[], surface?: RenderSurface): Promise<LaunchResult> => {
      if (d.source === 'demo') {
        d.host.toast(`Demo: would open ${specs.map(s => s.title).join(', ')}`)
        return { opened: 0, copied: null, failed: [] }
      }
      const r = await launchAll(
        {
          env: () => d.host.launchEnv(),
          run: argv => d.host.run(argv, LAUNCH_TIMEOUT_MS),
          copy: async text => (await d.host.copy(text, surface)).isCopied,
          sleep: ms => d.host.sleep(ms),
        },
        specs,
      )
      d.host.toast(launchSummary(r))
      return r
    },
    loadPhases,
    choosePhase,
    /** `◐ done`: flips from what the strip shows now (the default rule until the person first presses it). */
    toggleDone: async (pane: TmPhasePane): Promise<void> => {
      await d.write.phaseView(pane, v => ({ ...v, showDone: !isDoneShown(orderPhases(v.phases, 0), v.showDone) }))
    },
    /** `+N ▾` / `f`: opens the overlay list (reading the phases fresh) or closes it. */
    setOverlay: async (pane: TmPhasePane, open: boolean): Promise<void> => {
      await d.write.phaseView(pane, v => ({ ...v, overlay: open }))
      // The phase list and the sidebar never stand open together: their digits would clash.
      if (open) await d.write.nav(n => (n.sidebar ? { ...n, sidebar: false } : n))
      // `f` takes the digits for the overlay's picks: review's tick mode lets go of them.
      if (pane === 'review') await d.write.reviewList(l => ({ ...l, ticking: false }))
      if (open) await loadPhases(pane)
    },
    askDone: async (id: string): Promise<void> => {
      await d.write.cursor(c => ({ ...c, mode: 'confirm', refusal: '', currentId: id }))
      await d.write.reviewList(quietList)
      await d.host.focus(REVIEW, 'confirm-no')
    },
    cancel: async (): Promise<void> => {
      await d.write.cursor(c => ({ ...c, mode: 'card' }))
    },
    confirmDone: (id: string): Promise<void> =>
      once(id, async () => {
        if (!(await paneArmed(id, 'confirm'))) return undefined
        const out = await d.actions.done(id)
        if (out.ok) {
          await d.write.cursor(c => ({ ...c, done: c.done.includes(id) ? c.done : [...c.done, id] }))
          await signedOff(id)
          return undefined
        }
        await disarm(id)
        await d.write.cursor(c => ({ ...c, mode: 'card', refusal: out.refusal, currentId: id }))
        return holdOf(out)
      }),
    askNote: async (id: string): Promise<void> => {
      await d.write.cursor(c => ({ ...c, mode: 'note', refusal: '', currentId: id }))
      await d.write.reviewList(quietList)
      await d.host.focus(REVIEW, 'note')
    },
    /** `humanAction`: the check the card shows ('' when known empty), so the clear knows what "(not persisted)" means. */
    sendBack: (id: string, note: string, humanAction: string): Promise<void> =>
      once(id, async () => {
        if (!(await paneArmed(id, 'note'))) return undefined
        const out = await d.actions.backToAgent(id, note, humanAction)
        await disarm(id)
        if (!out.ok) {
          if (out.mayHaveMoved === true) {
            // Past the status move (or unknown): the task is no longer waiting, so its card goes (d must not sign off a
            // task just sent back); the refusal goes to a toast and the refresh shows where it stands.
            await d.write.snapshot(s => (s === null ? s : afterSendBack(s, id)))
            await d.write.cursor(c => ({ ...c, mode: 'card', refusal: '', currentId: c.currentId === id ? '' : c.currentId }))
            d.host.toast(`taskmaster-mods: ${id} went back to the agent only in part — ${out.refusal}`)
          } else {
            await d.write.cursor(c => ({ ...c, mode: 'card', refusal: out.refusal, currentId: id }))
          }
          // The Input is gone with the refusal: the prompt keeps the note so it is never lost (ruling F14).
          if (note.trim() !== '') await fill(`Back to ${id}: ${note.trim()}`)
          // A write still running refreshes when it answers (once); otherwise refresh now.
          if (out.pending === undefined) d.afterWrite(id, 'failed')
          return holdOf(out)
        }
        await d.write.snapshot(s => (s === null ? s : afterSendBack(s, id)))
        await d.write.cursor(c => ({ ...c, mode: 'card', refusal: '', currentId: '' }))
        await d.write.band(b => (b.refusalId === id ? { confirmingId: b.confirmingId, refusal: '' } : b))
        await d.host.closePane(REVIEW)
        await fill(note.trim() ? `Back to ${id}: ${note.trim()}` : `Back to ${id}`)
        d.afterWrite(id, 'sent-back')
        return undefined
      }),
    /**
     * j / k, or the Tab ring landing on a card's box: that card takes the focus and expands; a confirm or refusal on the card
     * left behind goes with it (a y can never land on a card the person moved off). The ring follows to the card's box.
     */
    focusCard: async (id: string, page: number): Promise<void> => {
      await d.write.cursor(c => (c.currentId === id ? c : { ...c, currentId: id, mode: 'card', refusal: '' }))
      await d.write.reviewList(l => ({ ...l, page, ticking: false }))
      await d.host.focus(REVIEW, `sel:${id}`)
    },
    /** p / n: that page, its first card focused. */
    turnReviewPage: async (page: number): Promise<void> => {
      await d.write.cursor(c => ({ ...c, currentId: '', mode: 'card', refusal: '' }))
      await d.write.reviewList(l => ({ ...l, page, ticking: false }))
    },
    /**
     * `t`: the focused card's checks take the digits 1–9 (the strip lets go of them) until one is ticked, `t` again, `f`, a
     * move or a page turn: ticking is two keystrokes, and a stray digit never ticks.
     */
    armTick: async (): Promise<void> => {
      const armed = { now: false }
      await d.write.reviewList(l => {
        armed.now = !l.ticking
        return { ...l, ticking: !l.ticking }
      })
      // The sidebar's 1-4 and the checks' 1-9 never stand on Buttons together (F5).
      if (armed.now) await d.write.nav(n => (n.sidebar ? { ...n, sidebar: false } : n))
    },
    /** s, or a press on a card's box. */
    toggleSelect: async (id: string): Promise<void> => {
      await d.write.reviewList(l => ({ ...l, selected: toggled(l.selected, id) }))
    },
    /** `all`: these tasks join the selection (the page's); `none` clears it. */
    selectAll: async (ids: readonly string[]): Promise<void> => {
      await d.write.reviewList(l => ({ ...l, selected: [...l.selected, ...ids.filter(id => !l.selected.includes(id))] }))
    },
    selectNone: async (): Promise<void> => {
      await d.write.reviewList(l => ({ ...l, selected: [] }))
    },
    /**
     * `Done N` / `To agent N`: the y/n (n focused) or the shared note, for exactly these tasks; a refresh before y changes
     * nothing. A card's own y/n goes down.
     */
    bulkAsk: async (kind: 'done' | 'agent', ids: readonly string[]): Promise<void> => {
      if (ids.length === 0) return
      await d.write.cursor(c => ({ ...c, mode: 'card', refusal: '' }))
      await d.write.reviewList(l => (l.bulk === 'busy' ? l : { ...l, bulk: kind, bulkIds: [...ids], ticking: false }))
      await d.host.focus(REVIEW, kind === 'done' ? 'bulk-no' : 'bulk-note')
    },
    bulkCancel: async (): Promise<void> => {
      await d.write.reviewList(l => (l.bulk === 'busy' ? l : { ...l, bulk: 'none', bulkIds: [] }))
    },
    /**
     * y on `Done N`: each task signed off in turn with the card's own call (one write per task at a time, a timed-out one
     * held), then one toast naming every refusal in the server's words; refused tasks stay selected.
     */
    bulkDone: async (ids: readonly string[]): Promise<void> => {
      if (!(await bulkArmed('done', ids))) return
      const results: BulkResult[] = []
      try {
        for (const id of ids) {
          if (!claim(id)) {
            results.push({ id, refusal: 'an earlier write on it has not answered yet' })
            continue
          }
          let pending: Promise<unknown> | undefined
          try {
            const out = await d.actions.done(id)
            if (out.ok) {
              await d.write.cursor(c => ({ ...c, done: c.done.includes(id) ? c.done : [...c.done, id] }))
              await signedOff(id)
              results.push({ id, refusal: null })
            } else {
              await disarm(id)
              pending = out.pending
              results.push({ id, refusal: out.refusal })
            }
          } catch (error) {
            // Stop at the first throw: what state it left is unknown, so no further write goes out beside it.
            results.push({ id, refusal: error instanceof Error ? error.message : String(error) })
            break
          } finally {
            release(id, pending)
          }
        }
      } finally {
        for (const id of ids) if (!results.some(r => r.id === id)) results.push({ id, refusal: 'the run stopped before it', notRun: true })
        try {
          const went = results.filter(r => r.refusal === null).map(r => r.id)
          await d.write.reviewList(l => ({ ...l, bulk: 'none', bulkIds: [], selected: l.selected.filter(id => !went.includes(id)) }))
        } finally {
          d.host.toast(bulkSummary('done', results))
        }
      }
    },
    /**
     * The note on `To agent N`: each task goes back in turn with the one note, as a card's `a` does (`actions`: the check each
     * card showed; a task no longer listed is not sent). One toast; the prompt names the tasks that went and holds the note
     * (with none gone, it still keeps the note); the pane closes only when every one went.
     */
    bulkSendBack: async (ids: readonly string[], note: string, actions: Readonly<Record<string, string>>): Promise<void> => {
      if (!(await bulkArmed('agent', ids))) return
      const results: BulkResult[] = []
      const moved: string[] = []
      try {
        for (const id of ids) {
          const humanAction = actions[id]
          if (humanAction === undefined) {
            results.push({ id, refusal: 'no longer listed: not sent back' })
            continue
          }
          if (!claim(id)) {
            results.push({ id, refusal: 'an earlier write on it has not answered yet' })
            continue
          }
          let pending: Promise<unknown> | undefined
          try {
            const out = await d.actions.backToAgent(id, note, humanAction)
            await disarm(id)
            if (out.ok || out.mayHaveMoved === true) {
              await d.write.snapshot(s => (s === null ? s : afterSendBack(s, id)))
              moved.push(id)
            }
            if (out.ok) {
              await d.write.band(b => (b.refusalId === id ? { confirmingId: b.confirmingId, refusal: '' } : b))
              d.afterWrite(id, 'sent-back')
            } else {
              pending = out.pending
              if (pending === undefined) d.afterWrite(id, 'failed')
            }
            results.push({ id, refusal: out.ok ? null : out.refusal })
          } catch (error) {
            // Stop at the first throw (see bulkDone); the note below is still handed back.
            results.push({ id, refusal: error instanceof Error ? error.message : String(error) })
            break
          } finally {
            release(id, pending)
          }
        }
      } finally {
        for (const id of ids) if (!results.some(r => r.id === id)) results.push({ id, refusal: 'the run stopped before it', notRun: true })
        try {
          await d.write.reviewList(l => ({ ...l, bulk: 'none', bulkIds: [], selected: l.selected.filter(id => !moved.includes(id)) }))
        } finally {
          d.host.toast(bulkSummary('back to agent', results))
        }
      }
      const text = note.trim()
      const named = moved.length > 0 ? moved : text === '' ? [] : [...ids]
      if (named.length > 0) await fill(text === '' ? `Back to ${named.join(', ')}` : `Back to ${named.join(', ')}: ${text}`)
      if (moved.length === ids.length) await d.host.closePane(REVIEW)
    },
    /** u (and Retry): a fresh snapshot from tm, and the pane's phases read again. Demo says it has nothing newer. */
    refresh: async (pane: TmPhasePane): Promise<void> => {
      if (d.refresh === undefined) d.host.toast('taskmaster-mods: demo data has nothing newer to read')
      else d.refresh()
      await loadPhases(pane)
    },
    fill,
    bandAskDone: async (id: string): Promise<void> => {
      if (!(await isBound(id))) {
        await d.write.band(() => ({ confirmingId: '', refusal: notBound(id, 'sign off'), refusalId: id }))
        return
      }
      await d.write.band(() => ({ confirmingId: id, refusal: '' }))
    },
    bandSendBack: async (id: string): Promise<void> => {
      if (!(await isBound(id))) {
        await d.write.band(() => ({ confirmingId: '', refusal: notBound(id, 'send back'), refusalId: id }))
        return
      }
      await d.write.band(() => ({ confirmingId: '', refusal: '' }))
      await openReview(id, 'note')
    },
    bandCancel: async (): Promise<void> => {
      await d.write.band(() => ({ confirmingId: '', refusal: '' }))
    },
    bandConfirmDone: (id: string): Promise<void> =>
      once(id, async () => {
        if (!(await bandArmed(id))) return undefined
        if (!(await isBound(id))) {
          await d.write.band(() => ({ confirmingId: '', refusal: notBound(id, 'sign off'), refusalId: id }))
          return undefined
        }
        const out = await d.actions.done(id)
        if (out.ok) {
          await signedOff(id)
          return undefined
        }
        await disarm(id)
        await d.write.band(() => ({ confirmingId: '', refusal: out.refusal, refusalId: id }))
        return holdOf(out)
      }),
    pick,
    /** The pane's own list, read again (`u` runs it after Stage 3's `refresh('handovers')`). */
    loadHandovers,
    /** `◐ closed`: closed handovers join (read when the list holds none yet) or leave; the pane starts over at its top. */
    toggleClosed: async (): Promise<void> => {
      const held = { show: false, has: false }
      await d.write.handoverMarks(m => {
        held.show = !m.showClosed
        return { ...m, showClosed: held.show, ask: null }
      })
      await d.write.handoverPage(0)
      await pick('')
      await d.write.handoverList(l => {
        held.has = l?.closed === true
        return l
      })
      if (held.show && !held.has) await loadHandovers()
    },
    /** `s` or a click on a card's box; any change of selection takes the close row down. */
    selectHandover: async (id: string): Promise<void> => {
      await d.write.handoverMarks(m => ({ ...m, ask: null, selected: m.selected.includes(id) ? m.selected.filter(x => x !== id) : [...m.selected, id] }))
    },
    /** `all` (this page's open ones) and `none` ([]). */
    selectHandovers: async (ids: readonly string[]): Promise<void> => {
      await d.write.handoverMarks(m => ({ ...m, ask: null, selected: [...ids] }))
    },
    /** j / k: show that page and focus that handover. */
    moveTo,
    /** `x` on the focused card, or `Close N`: the y/n row comes up, `n` (the safe default) focused. */
    askClose: async (ask: TmCloseAsk): Promise<void> => {
      if (ask.ids.length === 0) return
      await d.write.handoverMarks(m => ({ ...m, ask }))
      await d.host.focus(HANDOVERS, ask.kind === 'one' ? 'close-no' : 'bulk-close-no')
    },
    cancelClose: async (): Promise<void> => {
      await d.write.handoverMarks(m => (m.ask === null ? m : { ...m, ask: null }))
    },
    /** `y`: closes what the row asked about, only while that row is up (a second `y` finds nothing armed). */
    confirmClose: async (): Promise<void> => {
      const held: { ask: TmCloseAsk | null } = { ask: null }
      await d.write.handoverMarks(m => {
        held.ask = m.ask
        return m.ask === null ? m : { ...m, ask: null }
      })
      if (held.ask !== null) await closeHandovers(held.ask.ids)
    },
    closeHandovers,
    /** `w`: a fresh session in a new tab, as the Session tab's `w` starts one: this cwd, `handover <id>`, the resume line. */
    handoverLaunch: async (h: TmHandover): Promise<LaunchSpec> => ({
      cwd: await d.host.cwd(),
      title: `handover ${h.id}`,
      mode: 'fresh',
      prompt: resumePrompt(h.id, h.path),
    }),
    /** n / p: turn to a page and pick its first handover. */
    turnPage: async (page: number, firstId: string): Promise<void> => {
      await d.write.handoverPage(page)
      await pick(firstId)
    },
    /** Band, Session and handovers `c`: the handover's full Telegram-ready block on the clipboard, and a toast. */
    copyHandover: async (handoverId: string, surface?: RenderSurface): Promise<void> => {
      const found = await handoverText(handoverId)
      if (found === null) {
        d.host.toast(`taskmaster-mods: handover ${handoverId} not found`)
        return
      }
      const r = await d.host.copy(found.text, surface)
      d.host.toast(r.isCopied ? `Copied handover ${handoverId}` : `Clipboard unavailable (${r.reason}): copy by hand from ${found.path}`)
    },
    /** `r`: the resume line in the prompt; true when the prompt took it (the caller then records the resume). */
    resumeHandover: async (h: TmHandover): Promise<boolean> => {
      const filled = await d.host.fill(resumePrompt(h.id, h.path))
      if (!filled) d.host.toast(PROMPT_REFUSED)
      return filled
    },
    /** This session continues the work of `handoverId` (spec §10.5): kept per session id (not in demo), then D5 again. */
    recordResumed: async (handoverId: string): Promise<void> => {
      if (d.source !== 'demo') await recordResumedFrom(phaseStoreOf(d.host), await d.host.sessionId(), handoverId)
      await d.write.resumedFrom(handoverId)
      await d.resolve?.()
    },
    // The summary is shown only while its handover is the picked one; opening asks for its decisions and blockers once (an
    // unavailable one is asked again, "loading summary…" meanwhile).
    toggleSummary: async (h: { readonly id: string }, open: boolean): Promise<void> => {
      await d.write.summaryOpen(() => (open ? h.id : ''))
      if (!open || d.summary === undefined || summaryAsked.has(h.id)) return
      await d.write.summaries(all => {
        if (all[h.id]?.unavailable !== true) return all
        const { [h.id]: _gone, ...rest } = all
        return rest
      })
      await askSummary(h.id)
    },
    /** The open card's summary is missing or stale (a refresh marked it): read it again, once per refresh. */
    askSummary,
    /**
     * After a refresh: every cached summary is marked stale and read again when next shown, so an edited handover is never
     * stale for long; the old one stays drawn until the new one lands (no flicker). An unavailable one is dropped.
     */
    forgetSummaries: async (): Promise<void> => {
      generation += 1
      summaryAsked.clear()
      await d.write.summaries(all => {
        const kept = Object.entries(all).filter(([, s]) => s.unavailable !== true)
        if (kept.length === Object.keys(all).length && kept.every(([, s]) => s.stale === true)) return all
        return Object.fromEntries(kept.map(([id, s]) => [id, s.stale === true ? s : { ...s, stale: true as const }]))
      })
    },
    // Ticks are local UI state: read the stored set first (another session may have ticked), toggle, write it back, then
    // mirror it into $.state so the card redraws. Never sent to Taskmaster.
    toggleTick: async (taskId: string, item: string): Promise<void> => {
      const key = `${TICKS_PREFIX}${taskId}`
      const items = toggleTick(ticksOf(await d.host.storeGet(key)), item)
      await d.host.storeSet(key, { at: await d.host.now(), items })
      await d.write.ticks(all => ({ ...all, [taskId]: items }))
      await d.write.reviewList(l => (l.ticking ? { ...l, ticking: false } : l))
    },
    toggleDetails: async (): Promise<void> => {
      await d.write.detailsOpen(open => !open)
    },
    openViewer: async (taskId: string): Promise<void> => {
      if (d.source === 'demo') {
        d.host.toast(`would open ${taskId} in the viewer`)
        return
      }
      // backlog_open_viewer takes no task id (it opens the board); the review mode of spec §6.5 will take one.
      try {
        const r = await d.host.call('backlog_open_viewer', {})
        d.host.toast(r.isError ? `taskmaster-mods: the viewer did not open (${r.text})` : `Viewer opened: look for ${taskId}`)
      } catch (error) {
        d.host.toast(`taskmaster-mods: the viewer did not open (${error instanceof Error ? error.message : String(error)})`)
      }
    },
    copyCheck: async (taskId: string, text: string, surface: RenderSurface | undefined): Promise<void> => {
      const r = await d.host.copy(text, surface)
      d.host.toast(r.isCopied ? `Copied the check for ${taskId}` : `Clipboard unavailable (${r.reason}): o puts ${taskId} in the prompt`)
    },
  }
}
