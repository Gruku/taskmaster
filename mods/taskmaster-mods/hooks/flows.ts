// User intent: what every key in the band, the review queue and the handovers pane does, in one place, so the band and the
// pane can never disagree and every write goes through one explicit confirmation and at most one write per task at a time.
import type { RenderSurface } from 'claude-code'

import type {
  TmBandMode,
  TmCursor,
  TmHandover,
  TmHandoverNotice,
  TmHandoverSummary,
  TmPhase,
  TmPhasePane,
  TmPhaseView,
  TmSnapshot,
  TmTaskDetail,
} from '../types'
import type { TmActions } from './actions'
import type { TmHost } from './host'
import { launchAll, type LaunchResult, type LaunchSpec, launchSummary } from './launch'
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
import { ALL, fallback, isDoneShown, orderPhases, phaseStoreOf, saveChoice } from './phases'
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
  resumedFrom: (handoverId: string) => Promise<void>
  restoreHidden: (epoch: number) => Promise<void>
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
  /** Resolves the session's handover again (D5) after something it depends on changed. */
  resolve?: () => Promise<void>
  /** Reads one handover by id (tm: backlog_handover_get), for a copy the open list and the notice cannot answer. */
  readHandover?: (handoverId: string) => Promise<TmHandover | null>
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
  const once = async (id: string, work: () => Promise<Hold>): Promise<void> => {
    if (busy.has(id)) {
      if (held.has(id)) {
        // Not left armed either: once the earlier call answers, a y must not fire off this press.
        await disarm(id)
        d.host.toast(`taskmaster-mods: the earlier write on ${id} has not answered yet; try again once it has`)
      }
      return
    }
    busy.add(id)
    let pending: Promise<unknown> | undefined
    try {
      pending = (await work())?.pending
    } finally {
      if (pending === undefined) busy.delete(id)
      else {
        held.add(id)
        const release = () => {
          held.delete(id)
          busy.delete(id)
          d.afterWrite(id, 'failed')
        }
        void pending.then(release, release)
      }
    }
  }
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
  }
  const isBound = async (id: string): Promise<boolean> => (await d.boundId()) === id
  const open = async (id: string, title: string): Promise<void> => {
    if (!(await d.host.openPane(id, title))) d.host.toast('taskmaster-mods: widen the terminal to see the pane')
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
    const found = seen.listed ?? (await d.readHandover?.(id)) ?? null
    return found === null ? null : { text: handoverCopyText(found), path: found.path }
  }
  const openHandovers = async (): Promise<void> => {
    await d.write.pick('')
    await d.write.summaryOpen(() => '')
    await d.write.handoverPage(0)
    await open(HANDOVERS, 'Handovers')
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
  // starts over at its top (review's cursor: skips forgotten, or a skipped task of the new phase would hide).
  const choosePhase = async (pane: TmPhasePane, phaseId: string): Promise<void> => {
    let changed = false
    await d.write.phaseView(pane, v => {
      changed = v.choice !== phaseId
      return { ...v, choice: phaseId, overlay: false }
    })
    if (!changed) return
    await keepChoice(pane, phaseId)
    if (pane === 'review') await d.write.cursor(c => ({ ...c, mode: 'card', refusal: '', currentId: '', skipped: [] }))
    if (pane === 'handovers') {
      await d.write.handoverPage(0)
      await pick('')
    }
    d.phaseChosen?.(pane, phaseId)
  }
  const openReview = async (pin = '', mode: TmCursor['mode'] = 'card'): Promise<void> => {
    await d.write.cursor(() => ({ ...FRESH_CURSOR, currentId: pin, mode }))
    await open(REVIEW, 'Review')
    // The phase list is read when the pane opens, off the open: the pane draws at once, the filter label follows.
    if (pin === '') {
      d.host.after(0, () => {
        void loadPhases('review').catch(() => undefined)
      })
    }
    if (mode === 'note') await d.host.focus(REVIEW, 'note')
  }

  return {
    openReview,
    openHandovers,
    /** Band and Session `1`–`4`: the pane, opened as its own command opens it. */
    openPane: async (pane: TmPane): Promise<void> => {
      if (pane === 'review') return openReview()
      if (pane === 'handovers') return openHandovers()
      return pane === 'session' ? open(SESSION, 'Session') : open(RESTORE, 'Restore')
    },
    /** Session `h`: the handovers pane on the page that holds this handover, picked and focused (Stage 4 adds its phase). */
    openHandover: async (handoverId: string): Promise<void> => {
      const at = { index: -1 }
      await d.write.snapshot(s => {
        at.index = s?.handovers.findIndex(h => h.id === handoverId) ?? -1
        return s
      })
      await d.write.summaryOpen(() => '')
      await d.write.handoverPage(at.index < 0 ? 0 : Math.floor(at.index / PAGE_SIZE))
      await d.write.pick(at.index < 0 ? '' : handoverId)
      await open(HANDOVERS, 'Handovers')
      if (at.index < 0) d.host.toast(`taskmaster-mods: handover ${handoverId} is not in the open list`)
      else await d.host.focus(HANDOVERS, `ho:${handoverId}`)
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
      if (open) await loadPhases(pane)
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
    skip: async (id: string): Promise<void> => {
      await d.write.cursor(c => ({ ...c, mode: 'card', refusal: '', currentId: '', skipped: [...c.skipped, id] }))
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
    resumeHandover: async (h: TmHandover): Promise<void> => {
      await fill(`Resume from handover ${h.id} (${h.path})`)
    },
    /** This session continues the work of `handoverId` (spec §10.5): kept per session id (not in demo), then D5 again. */
    recordResumed: async (handoverId: string): Promise<void> => {
      if (d.source !== 'demo') await recordResumedFrom(phaseStoreOf(d.host), await d.host.sessionId(), handoverId)
      await d.write.resumedFrom(handoverId)
      await d.resolve?.()
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
