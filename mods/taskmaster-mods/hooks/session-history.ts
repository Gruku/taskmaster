// User intent: a long /goal session must show on its Session tab everything it did — every task it touched and every handover
// it wrote — not just its one bound task; read from its own transcript off the render path, never crashing a pane.
import type { TmHistoryHandover, TmRefs, TmSessionHistory, TmSnapshot } from '../types'
import type { TmHost } from './host'
import { resolveTaskRefs, type RestoreHost, runSessionScan } from './restore/scan'

export const FRESH_SESSION_HISTORY: TmSessionHistory = { sessionId: '', scan: null, refs: null, pending: false, error: '', finishedAt: -1, page: 0 }
export const HISTORY_PAGE_SIZE = 8
/** Handovers listed under the latest one before a "N more" line. */
export const HANDOVER_ROWS = 6
/** The pane asks again on a draw only once the last scan is this old (ms); `u` asks at once. */
export const HISTORY_TTL_MS = 30_000

/** One touched task as the HISTORY section draws it; `status` and `title` are '' while tm has not said. */
export type HistoryRow = {
  readonly id: string
  readonly status: string
  readonly title: string
  /** Epoch seconds of its last touch (0 unknown). */
  readonly at: number
  /** Some touching call wrote (otherwise the session only read it). */
  readonly wrote: boolean
  readonly current: boolean
}

export type SessionHistoryView = {
  readonly state: 'loading' | 'ready' | 'failed'
  readonly error: string
  /** Every task touched, newest first, the live binding merged in. */
  readonly rows: readonly HistoryRow[]
  readonly page: number
  readonly pages: number
  /** The handovers this session wrote other than the latest one drawn with its actions, newest first. */
  readonly others: readonly TmHistoryHandover[]
  /** How many handovers this session wrote in all. */
  readonly written: number
}

/** The history the Session tab draws: scan + tm's refs + the live binding (so the current task shows before any scan lands). */
export function sessionHistoryView(a: {
  readonly history: TmSessionHistory
  readonly snapshot: TmSnapshot | null
  /** The handover drawn with actions (its id is left out of `others`). */
  readonly primaryId: string
  readonly nowSeconds: number
}): SessionHistoryView {
  const { history: h, snapshot } = a
  const bound = snapshot?.bound ?? null
  const touched = [...(h.scan?.tasks ?? [])].sort((x, y) => y.at - x.at)
  const known = new Set(touched.map(t => t.id))
  const live = bound !== null && !known.has(bound.taskId) ? [{ id: bound.taskId, at: a.nowSeconds, wrote: true }] : []
  const rows = [...live, ...touched].map((t): HistoryRow => {
    const ref = h.refs?.tasks[t.id]
    const detail = bound !== null && bound.taskId === t.id ? bound.detail : null
    return { id: t.id, status: ref?.status ?? detail?.status ?? '', title: ref?.title ?? detail?.title ?? '', at: t.at, wrote: t.wrote, current: bound !== null && bound.taskId === t.id }
  })
  const pages = Math.max(1, Math.ceil(rows.length / HISTORY_PAGE_SIZE))
  const all = [...(h.scan?.handovers ?? [])].sort((x, y) => y.at - x.at)
  return {
    state: h.error !== '' ? 'failed' : h.scan === null || h.pending ? 'loading' : 'ready',
    error: h.error,
    rows,
    page: Math.min(Math.max(0, h.page), pages - 1),
    pages,
    others: all.filter(x => x.id !== a.primaryId),
    written: all.length,
  }
}

export type HistoryDeps = {
  readonly source: 'tm' | 'demo'
  readonly rh: RestoreHost
  readonly tm: Pick<TmHost, 'call' | 'sleep' | 'log'>
  readonly sessionId: () => Promise<string>
  readonly repoRoot: () => Promise<string>
  /** Milliseconds, as $.clock.now. */
  readonly now: () => Promise<number>
  readonly read: () => Promise<TmSessionHistory>
  readonly write: (change: (h: TmSessionHistory) => TmSessionHistory) => Promise<void>
}

/** The Session tab's scan: single-flight, rescanned when the tab draws past the TTL or `u` forces it, failures kept as a line. */
export function createHistoryFlows(d: HistoryDeps) {
  let running = false
  return {
    ask: async (force: boolean): Promise<void> => {
      if (d.source === 'demo' || running) return
      running = true
      try {
        const id = await d.sessionId()
        const before = await d.read()
        if (!force && before.sessionId === id && before.finishedAt >= 0 && (await d.now()) - before.finishedAt < HISTORY_TTL_MS) return
        await d.write(h => ({ ...(h.sessionId === id ? h : FRESH_SESSION_HISTORY), sessionId: id, pending: true }))
        let error = ''
        try {
          const scan = await runSessionScan(d.rh, { root: await d.repoRoot(), sessionId: id })
          await d.write(h => ({ ...h, scan, error: '' }))
          // The last refs stay drawn until the new ones land, as restore's do.
          const refs: TmRefs | null = await resolveTaskRefs(d.tm, scan.tasks.map(t => t.id))
          await d.write(h => ({ ...h, refs }))
        } catch (e) {
          error = `History unavailable: ${e instanceof Error ? e.message : String(e)}`
          d.tm.log(`taskmaster-mods: session history not read: ${error}`)
        }
        const finishedAt = await d.now()
        await d.write(h => ({ ...h, pending: false, error, finishedAt }))
      } catch (e) {
        d.tm.log(`taskmaster-mods: session history failed: ${e instanceof Error ? e.message : String(e)}`)
      } finally {
        running = false
      }
    },
    turnPage: async (page: number): Promise<void> => {
      await d.write(h => (h.page === page ? h : { ...h, page: Math.max(0, page) }))
    },
  }
}
