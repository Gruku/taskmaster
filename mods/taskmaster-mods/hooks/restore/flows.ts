// User intent: what every key in the restore pane does, and the session-start scan behind the band's restore keycap, in
// one place over injected closures (no `$`), so the pane, the band and the Session tab never disagree about a burst.
import type { TmRefs, TmRestoreData, TmResumeMode, TmRestorePending, TmRestoreView, TmSessionBurst } from '../../types'
import { DEMO_BRIEF, demoRestore } from '../demo'
import type { TmHost } from '../host'
import { isSessionId, type LaunchResult, launchRefusal, type LaunchSpec, type Platform } from '../launch'
import type { PhaseStore } from '../phases'
import { readResumedFrom, recordResumedFrom } from '../resumed'
import {
  blockedWhy,
  briefKey,
  briefPrompt,
  briefText,
  burstsOf,
  copyTextOf,
  defaultBurst,
  isTicked,
  launchSpecOf,
  paneRows,
  restorePendingOf,
  type RestoreRow,
  resumeModeOf,
  rowsOf,
  sessionBurstOf,
  SIGNAL_DAYS,
} from './model'
import { type RestoreHost, resolveRefs, runDigest, runScan } from './scan'

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
  /** The platform the copy line is written for (launch.ts's platformOf). */
  readonly platform: () => Promise<Platform>
  /** Stage 2's launcher (TmFlows.launch): staggered tabs, a copy fallback, one summary toast. */
  readonly launch: (specs: readonly LaunchSpec[]) => Promise<LaunchResult>
  readonly copy: (text: string) => Promise<boolean>
  /** One completion on the briefModel (spec §10.6). */
  readonly complete: (prompt: string) => Promise<{ readonly ok: true; readonly text: string } | { readonly ok: false; readonly reason: string }>
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

  /** A session's brief: cached per close in $.store, else the digest put to the briefModel; a failure is said in the brief. */
  const briefOf = async (row: RestoreRow): Promise<string> => {
    if (d.source === 'demo') return DEMO_BRIEF
    const key = briefKey(row)
    const cached = await d.store.get(key)
    if (typeof cached === 'string') return cached
    if (!isSessionId(row.s.id)) return 'Brief failed: unusable session id'
    let prompt: string
    try {
      prompt = briefPrompt(row, await runDigest(d.rh, row.s.id))
    } catch (error) {
      return `Brief failed: ${error instanceof Error ? error.message : String(error)}`
    }
    const answer = await d.complete(prompt)
    if (!answer.ok) return `Brief failed: ${answer.reason}`
    const text = briefText(answer.text)
    await d.store.set(key, text)
    return text
  }

  const runBrief = async (row: RestoreRow): Promise<void> => {
    const id = row.s.id
    await d.write.view(v => ({ ...v, briefing: [...v.briefing, id] }))
    try {
      const text = await briefOf(row)
      await d.write.view(v => ({ ...v, briefs: { ...v.briefs, [id]: text } }))
    } finally {
      await d.write.view(v => ({ ...v, briefing: v.briefing.filter(x => x !== id) }))
    }
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
    /**
     * `r` on a card, `b` for the ticked: what cannot reopen says why (a --resume of a session id the launcher would refuse
     * among them); a --resume of a session with a handover records `resumed-from:<id>` first (spec §10.5); then one
     * staggered launch. Only a launch that opened every tab marks them reopened and unticks them.
     */
    resume: async (ids: readonly string[]): Promise<void> => {
      if (ids.length === 0) {
        d.toast('Nothing ticked to resume')
        return
      }
      const view = await d.read.view()
      const rows = await rowsNow()
      const ready: RestoreRow[] = []
      for (const id of ids) {
        const row = rows.find(r => r.s.id === id)
        if (row === undefined || view.launching.includes(id)) continue
        const why = blockedWhy(row)
        if (why !== '') d.toast(why)
        else ready.push(row)
      }
      if (ready.length === 0) return
      const root = await d.repoRoot()
      const platform = await d.platform()
      // The launcher's own refusals, up front: a refused row is said and skipped before anything is recorded for it.
      const go: { row: RestoreRow; mode: TmResumeMode; spec: LaunchSpec }[] = []
      for (const row of ready) {
        const mode = resumeModeOf(row, view.modes)
        const spec = launchSpecOf(row, mode, view.compact, root)
        const refused = launchRefusal(spec, platform, undefined)
        if (refused !== '') d.toast(`${row.s.title}: ${refused}`)
        else go.push({ row, mode, spec })
      }
      if (go.length === 0) return
      const specs: LaunchSpec[] = []
      for (const { row, mode, spec } of go) {
        if (mode === 'resume' && row.handover !== null && d.source === 'tm') await recordResumedFrom(d.store, row.s.id, row.handover.id)
        specs.push(spec)
      }
      const launched = go.map(g => g.row.s.id)
      await d.write.view(v => ({ ...v, launching: [...v.launching, ...launched] }))
      try {
        const out = await d.launch(specs)
        if (out.opened === specs.length) {
          const at = (await d.now()) / 1000
          await d.write.view(v => ({
            ...v,
            opened: { ...v.opened, ...Object.fromEntries(launched.map(id => [id, at])) },
            selected: { ...v.selected, ...Object.fromEntries(launched.map(id => [id, false])) },
          }))
        }
      } catch (error) {
        d.toast(`taskmaster-mods: could not open the sessions (${error instanceof Error ? error.message : String(error)})`)
      } finally {
        await d.write.view(v => ({ ...v, launching: v.launching.filter(id => !launched.includes(id)) }))
      }
    },
    /** `c`: the launcher's own command that reopens it, to paste in a new terminal; what the launcher refuses is not copied. */
    copy: async (id: string): Promise<void> => {
      const row = await rowById(id)
      if (row === undefined) return
      const mode = resumeModeOf(row, (await d.read.view()).modes)
      const root = await d.repoRoot()
      const platform = await d.platform()
      const why = launchRefusal(launchSpecOf(row, mode, false, root), platform, undefined)
      if (why !== '') {
        d.toast(`${row.s.title}: cannot copy (${why})`)
        return
      }
      const text = copyTextOf(row, mode, root, platform)
      d.toast((await d.copy(text)) ? `Copied: ${text}` : `Clipboard unavailable: ${text}`)
    },
    /** `i` on a card: shows its brief (asking for one the first time), or hides it. */
    toggleBrief: async (id: string): Promise<void> => {
      const view = await d.read.view()
      if (view.briefs[id] !== undefined) {
        await d.write.view(v => ({ ...v, briefs: Object.fromEntries(Object.entries(v.briefs).filter(([k]) => k !== id)) }))
        return
      }
      const row = await rowById(id)
      if (row !== undefined && !view.briefing.includes(id)) await runBrief(row)
    },
    /** `g`: a brief for each ticked session that has none, one after another. */
    brief: async (ids: readonly string[]): Promise<void> => {
      if (ids.length === 0) {
        d.toast('Nothing ticked to brief')
        return
      }
      const rows = await rowsNow()
      for (const id of ids) {
        const view = await d.read.view()
        const row = rows.find(r => r.s.id === id)
        if (row !== undefined && view.briefs[id] === undefined && !view.briefing.includes(id)) await runBrief(row)
      }
    },
  }
}
