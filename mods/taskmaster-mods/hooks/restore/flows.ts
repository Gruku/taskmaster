// User intent: what every key in the restore pane does, and the session-start scan behind the band's restore keycap, in
// one place over injected closures (no `$`), so the pane, the band and the Session tab never disagree about a burst.
import type { TmRefs, TmRestoreData, TmRestorePending, TmRestoreView, TmSessionBurst } from '../../types'
import { demoRestore } from '../demo'
import type { TmHost } from '../host'
import type { PhaseStore } from '../phases'
import { readResumedFrom } from '../resumed'
import {
  blockedWhy,
  burstsOf,
  defaultBurst,
  isTicked,
  paneRows,
  restorePendingOf,
  type RestoreRow,
  resumeModeOf,
  rowsOf,
  sessionBurstOf,
  SIGNAL_DAYS,
} from './model'
import { type RestoreHost, resolveRefs, runScan } from './scan'

/** tm connects seconds after a session starts: the start scan asks this many more times, this far apart. */
export const RESOLVE_RETRIES = 3
export const RESOLVE_RETRY_MS = 5_000
export const SCANNING = 'Scanning sessions…'

export type RestoreWriter = {
  readonly data: (change: (d: TmRestoreData) => TmRestoreData) => Promise<void>
  readonly view: (change: (v: TmRestoreView) => TmRestoreView) => Promise<void>
  /** The sidebar's restore count and the Session tab's burst line (spec §10.4, §7). */
  readonly signals: (pending: TmRestorePending | null, burst: TmSessionBurst | null) => Promise<void>
}

export type RestoreDeps = {
  readonly source: 'tm' | 'demo'
  readonly rh: RestoreHost
  readonly tm: Pick<TmHost, 'call' | 'sleep' | 'log'>
  readonly store: PhaseStore
  readonly read: { readonly data: () => Promise<TmRestoreData>; readonly view: () => Promise<TmRestoreView> }
  readonly write: RestoreWriter
  /** Milliseconds, as $.clock.now. */
  readonly now: () => Promise<number>
  readonly wait: (ms: number) => Promise<void>
  readonly sessionId: () => Promise<string>
  readonly repoRoot: () => Promise<string>
  readonly toast: (text: string) => void
  readonly openPane: () => Promise<boolean>
  /** Reads restore's phase list fresh into the strip, when the main flows are built (a no-op before). */
  readonly loadPhases: () => void
}

export type RestoreFlows = ReturnType<typeof createRestoreFlows>

export function createRestoreFlows(d: RestoreDeps) {
  const rowsNow = async (): Promise<RestoreRow[]> => paneRows(await d.read.data(), await d.sessionId())
  const rowById = async (id: string): Promise<RestoreRow | undefined> => (await rowsNow()).find(r => r.s.id === id)

  /** After every scan: page 1's burst for the sidebar, this session's burst for the Session tab. */
  const signal = async (): Promise<void> => {
    const data = await d.read.data()
    const self = await d.sessionId()
    const rows = rowsOf(data.scan, data.refs)
    const bursts = burstsOf(rows)
    const now = (await d.now()) / 1000
    await d.write.signals(restorePendingOf(bursts, now), sessionBurstOf(bursts, rows, self, await readResumedFrom(d.store, self)))
  }

  /** One scan (demo: the demo sessions), then tm's word on it; `retries` more asks while tm does not answer. */
  const scan = async (days: number, retries: number): Promise<void> => {
    if (d.source === 'demo') {
      const demo = demoRestore(await d.now())
      await d.write.data(() => ({ scan: demo.scan, refs: demo.refs, error: '', days }))
      return
    }
    const found = await runScan(d.rh, { root: await d.repoRoot(), days, exclude: '' })
    // The last refs stay drawn until the new ones land: a rescan never flickers every status to `?`.
    await d.write.data(prev => ({ ...prev, scan: found, error: '', days }))
    let refs: TmRefs | null = await resolveRefs(d.tm, found)
    for (let i = 0; refs === null && i < retries; i++) {
      await d.wait(RESOLVE_RETRY_MS)
      refs = await resolveRefs(d.tm, found)
    }
    // Only onto the scan they were read for ($.state hands back copies, so the scan is known by its time, not its identity).
    await d.write.data(prev => (prev.scan?.scannedAt === found.scannedAt ? { ...prev, refs } : prev))
  }

  const rescan = async (days: number, retries = 0): Promise<void> => {
    await d.write.view(v => ({ ...v, busy: SCANNING }))
    try {
      await scan(days, retries)
      await signal()
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error)
      await d.write.data(prev => ({ ...prev, error: `Scan failed: ${reason}`, days }))
      d.tm.log(`taskmaster-mods: restore scan failed: ${reason}`)
    } finally {
      await d.write.view(v => ({ ...v, busy: '' }))
    }
  }

  return {
    /** `/tm-restore [days]`: the pane opens at once on page 1 and fills when the scan lands; the answer names the burst. */
    open: async (days: number): Promise<string> => {
      await d.write.view(v => ({ ...v, page: 0, focus: '' }))
      if (!(await d.openPane())) d.toast('taskmaster-mods: widen the terminal to see the pane')
      d.loadPhases()
      await rescan(days)
      const data = await d.read.data()
      if (data.error !== '') return data.error
      const bursts = burstsOf(await rowsNow())
      const burst = bursts[defaultBurst(bursts)]
      if (burst === undefined) return 'Restore opened: no recent sessions in this repo.'
      return `Restore opened: ${burst.ids.length} ${burst.ids.length === 1 ? 'session' : 'sessions'} closed together, ${burst.open} unfinished.`
    },
    /** `u`: the pane's window again, the page kept. */
    rescan: async (): Promise<void> => {
      await rescan((await d.read.data()).days)
    },
    /** session.start (spec §10.4): a quiet 3-day scan for the sidebar, asking tm again while it is still connecting. */
    startScan: async (): Promise<void> => {
      await rescan(SIGNAL_DAYS, RESOLVE_RETRIES)
    },
    turnPage: async (page: number): Promise<void> => {
      await d.write.view(v => ({ ...v, page, focus: '' }))
    },
    focus: async (id: string): Promise<void> => {
      await d.write.view(v => (v.focus === id ? v : { ...v, focus: id }))
    },
    /** `s` / a click on the box: flips the tick; a session that cannot reopen says why instead. */
    toggle: async (id: string): Promise<void> => {
      const row = await rowById(id)
      if (row === undefined) return
      const why = blockedWhy(row)
      if (why !== '') {
        d.toast(why)
        return
      }
      await d.write.view(v => ({ ...v, selected: { ...v.selected, [id]: !isTicked(row, v.selected, v.opened) } }))
    },
    /** all · unfinished · none: explicit ticks for the page's sessions. */
    select: async (changes: Readonly<Record<string, boolean>>): Promise<void> => {
      await d.write.view(v => ({ ...v, selected: { ...v.selected, ...changes } }))
    },
    /** `m`: fresh from the handover ⇄ --resume; a session without a handover only has --resume. */
    toggleMode: async (id: string): Promise<void> => {
      const row = await rowById(id)
      if (row === undefined) return
      if (row.handover === null) {
        d.toast(`${row.s.title} wrote no handover: it reopens with --resume`)
        return
      }
      await d.write.view(v => ({ ...v, modes: { ...v.modes, [id]: resumeModeOf(row, v.modes) === 'fresh' ? 'resume' : 'fresh' } }))
    },
    /** `z`: --resume launches ask the session to compact first. */
    toggleCompact: async (): Promise<void> => {
      await d.write.view(v => ({ ...v, compact: !v.compact }))
    },
  }
}
