// User intent: what every key in the band, the review queue and the handovers pane does, in one place, so the band and the
// pane can never disagree and every write goes through one explicit confirmation and at most one write per task at a time.
import type { RenderSurface } from 'claude-code'

import type { TmBandMode, TmCursor, TmHandover, TmHandoverNotice, TmHandoverSummary, TmSnapshot, TmTaskDetail } from '../types'
import type { TmActions } from './actions'
import type { TmHost } from './host'
import { afterDone, afterSendBack, FRESH_CURSOR, handoverCopyText, HANDOVERS, REVIEW, TICKS_PREFIX, ticksOf, toggleTick } from './model'

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
}

export type TmFlowDeps = {
  host: TmHost
  write: TmWriter
  actions: TmActions
  afterWrite: (taskId: string, outcome: 'done' | 'sent-back') => void
  /** Which data the flows act on: demo never reaches the tm server (the viewer is only announced). Default tm. */
  source?: 'tm' | 'demo'
  /**
   * Reads a handover's decisions and blockers (tm: backlog_handover_get with sections decisions + blockers), or null when it
   * cannot. Absent in demo, whose summaries are seeded like the task details.
   */
  summary?: (handoverId: string) => Promise<TmHandoverSummary | null>
}

export type TmFlows = ReturnType<typeof createFlows>

const PROMPT_REFUSED = 'taskmaster-mods: the prompt did not take the text; close the dialog and try again'
const UNAVAILABLE: TmHandoverSummary = { decisions: [], blockers: [], unavailable: true }

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
  const once = async (id: string, work: () => Promise<void>): Promise<void> => {
    if (busy.has(id)) return
    busy.add(id)
    try {
      await work()
    } finally {
      busy.delete(id)
    }
  }
  const open = async (id: string, title: string): Promise<void> => {
    if (!(await d.host.openPane(id, title))) d.host.toast('taskmaster-mods: widen the terminal to see the pane')
  }
  const fill = async (text: string): Promise<void> => {
    if (!(await d.host.fill(text))) d.host.toast(PROMPT_REFUSED)
  }

  return {
    openReview: async (pin = '', mode: TmCursor['mode'] = 'card'): Promise<void> => {
      await d.write.cursor(() => ({ ...FRESH_CURSOR, currentId: pin, mode }))
      await open(REVIEW, 'Review')
      if (mode === 'note') await d.host.focus(REVIEW, 'note')
    },
    openHandovers: async (): Promise<void> => {
      await d.write.pick('')
      await d.write.summaryOpen(() => '')
      await open(HANDOVERS, 'Handovers')
    },
    askDone: async (id: string): Promise<void> => {
      await d.write.cursor(c => ({ ...c, mode: 'confirm', refusal: '', currentId: id }))
      await d.host.focus(REVIEW, 'confirm-no')
    },
    cancel: async (): Promise<void> => {
      await d.write.cursor(c => ({ ...c, mode: 'card' }))
    },
    confirmDone: (id: string): Promise<void> =>
      once(id, async () => {
        const out = await d.actions.done(id)
        if (out.ok) {
          await d.write.snapshot(s => (s === null ? s : afterDone(s, id)))
          await d.write.cursor(c => ({ ...c, mode: 'card', refusal: '', currentId: '', done: [...c.done, id] }))
          d.afterWrite(id, 'done')
          return
        }
        await d.write.cursor(c => ({ ...c, mode: 'card', refusal: out.refusal, currentId: id }))
      }),
    askNote: async (id: string): Promise<void> => {
      await d.write.cursor(c => ({ ...c, mode: 'note', refusal: '', currentId: id }))
      await d.host.focus(REVIEW, 'note')
    },
    sendBack: (id: string, note: string): Promise<void> =>
      once(id, async () => {
        const out = await d.actions.backToAgent(id, note)
        if (!out.ok) {
          await d.write.cursor(c => ({ ...c, mode: 'card', refusal: out.refusal, currentId: id }))
          return
        }
        await d.write.snapshot(s => (s === null ? s : afterSendBack(s, id)))
        await d.write.cursor(c => ({ ...c, mode: 'card', refusal: '', currentId: '' }))
        await d.host.closePane(REVIEW)
        await fill(note.trim() ? `Back to ${id}: ${note.trim()}` : `Back to ${id}`)
        d.afterWrite(id, 'sent-back')
      }),
    skip: async (id: string): Promise<void> => {
      await d.write.cursor(c => ({ ...c, mode: 'card', refusal: '', currentId: '', skipped: [...c.skipped, id] }))
    },
    fill,
    bandAskDone: async (id: string): Promise<void> => {
      await d.write.band(() => ({ confirmingId: id, refusal: '' }))
    },
    bandCancel: async (): Promise<void> => {
      await d.write.band(() => ({ confirmingId: '', refusal: '' }))
    },
    bandConfirmDone: (id: string): Promise<void> =>
      once(id, async () => {
        const out = await d.actions.done(id)
        if (out.ok) {
          await d.write.snapshot(s => (s === null ? s : afterDone(s, id)))
          await d.write.band(() => ({ confirmingId: '', refusal: '' }))
          d.afterWrite(id, 'done')
          return
        }
        await d.write.band(() => ({ confirmingId: '', refusal: out.refusal, refusalId: id }))
      }),
    pick: async (id: string): Promise<void> => {
      await d.write.pick(id)
      await d.write.summaryOpen(open => (open === id ? open : ''))
    },
    copyHandover: async (h: TmHandover, surface: RenderSurface | undefined): Promise<void> => {
      const r = await d.host.copy(handoverCopyText(h), surface)
      d.host.toast(r.isCopied ? `Copied handover ${h.id}` : `Clipboard unavailable (${r.reason}): copy by hand from ${h.path}`)
    },
    resumeHandover: async (h: TmHandover): Promise<void> => {
      await fill(`Resume from handover ${h.id} (${h.path})`)
    },
    // The summary is shown only while its handover is the picked one; opening asks for its decisions and blockers once (an
    // unavailable one is asked again, "loading summary…" meanwhile).
    toggleSummary: async (h: TmHandover, open: boolean): Promise<void> => {
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
    // The band's handover-written notice: `3` copies its block; the row goes once the copy landed.
    copyNotice: async (n: TmHandoverNotice, surface: RenderSurface | undefined): Promise<void> => {
      const r = await d.host.copy(n.text, surface)
      if (!r.isCopied) {
        d.host.toast(`Clipboard unavailable (${r.reason}): copy by hand from ${n.path}`)
        return
      }
      d.host.toast(`Copied handover ${n.id}`)
      await d.write.notice(current => (current?.id === n.id ? null : current))
    },
    // Ticks are local UI state: read the stored set first (another session may have ticked), toggle, write it back, then
    // mirror it into $.state so the card redraws. Never sent to Taskmaster.
    toggleTick: async (taskId: string, item: string): Promise<void> => {
      const key = `${TICKS_PREFIX}${taskId}`
      const items = toggleTick(ticksOf(await d.host.storeGet(key)), item)
      await d.host.storeSet(key, { at: await d.host.now(), items })
      await d.write.ticks(all => ({ ...all, [taskId]: items }))
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
