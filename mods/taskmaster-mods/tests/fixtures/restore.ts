// User intent: small, readable restore-pane inputs for tests — a scanned session with every fact defaulted, a scan, and
// backlog_query reply tables shaped as the server prints them — so each test states only the facts it is about.
import type { TmRefs, TmRestoreData, TmRestorePending, TmRestoreView, TmScan, TmScannedSession, TmSessionBurst } from '../../types'
import type { LaunchResult, LaunchSpec } from '../../hooks/launch'
import type { RestoreDeps } from '../../hooks/restore/flows'
import { FRESH_RESTORE_DATA, FRESH_RESTORE_VIEW } from '../../hooks/restore/model'
import type { RunOut } from '../../hooks/restore/scan'

export const ROOT = 'C:\\work\\proj'
/** 2026-10-10 16:31:00 UTC, the demo burst's last close; tests pin `now` from it. */
export const T0 = Date.UTC(2026, 9, 10, 16, 31) / 1000

export function session(id: string, over: Partial<TmScannedSession> = {}): TmScannedSession {
  return {
    id,
    cwd: ROOT,
    cwdExists: true,
    branch: 'main',
    title: `Session ${id}`,
    firstPrompt: `start ${id}`,
    lastPrompt: `keep going on ${id}`,
    lastReply: `Done with step 2 of ${id}.`,
    lastEvent: 'end_turn',
    interrupted: false,
    prompts: 4,
    ctx: 120_000,
    cost: 3,
    closedEpoch: T0,
    hasClosed: true,
    running: false,
    resumedAfterClose: false,
    taskIds: [],
    handovers: [],
    endedSession: false,
    promptsAfterEnd: 0,
    ...over,
  }
}

export const scanOf = (sessions: readonly TmScannedSession[]): TmScan => ({ scannedAt: T0, root: ROOT, sessions })

export const NO_REFS: TmRefs = { tasks: {}, handovers: {} }

/** A refsSql reply: the header, one padded line a row, the footer. */
export function refsReply(rows: readonly string[]): string {
  return ['ref                                           title', ...rows, `${rows.length} ${rows.length === 1 ? 'row' : 'rows'}`].join('\n')
}

/**
 * The restore flows over memory: `scan` is what scan.py prints (an Error: every Python fails with it), `tm` answers the
 * refs query in turn (a string: the reply text; 'offline': the call throws), and every effect is recorded.
 */
export type FakeRestore = {
  readonly deps: RestoreDeps
  data: TmRestoreData
  view: TmRestoreView
  readonly signals: { readonly pending: TmRestorePending | null; readonly burst: TmSessionBurst | null }[]
  readonly toasts: string[]
  readonly logs: string[]
  readonly runs: (readonly string[])[]
  readonly waits: number[]
  readonly launches: (readonly LaunchSpec[])[]
  readonly copies: string[]
  readonly prompts: string[]
  readonly store: Map<string, unknown>
  opened: number
  phasesAsked: number
  launchResult: (specs: readonly LaunchSpec[]) => LaunchResult
  complete: (prompt: string) => { readonly ok: true; readonly text: string } | { readonly ok: false; readonly reason: string }
}

export function fakeRestore(
  o: {
    readonly source?: 'tm' | 'demo'
    readonly scan?: TmScan | Error
    readonly tm?: readonly (string | 'offline')[]
    readonly now?: number
    readonly self?: string
    readonly store?: Readonly<Record<string, unknown>>
    readonly digest?: { readonly first: string; readonly tail: string } | { readonly error: string }
  } = {},
): FakeRestore {
  const answers = [...(o.tm ?? [])]
  const f: FakeRestore = {
    data: FRESH_RESTORE_DATA,
    view: FRESH_RESTORE_VIEW,
    signals: [],
    toasts: [],
    logs: [],
    runs: [],
    waits: [],
    launches: [],
    copies: [],
    prompts: [],
    store: new Map(Object.entries(o.store ?? {})),
    opened: 0,
    phasesAsked: 0,
    launchResult: specs => ({ opened: specs.length, copied: null, failed: [] }),
    complete: () => ({ ok: true, text: '<state>Stopped at the parser.</state><next>Finish the error rows.</next><blocker>none</blocker>' }),
    deps: undefined as unknown as RestoreDeps,
  }
  const scan = o.scan ?? scanOf([])
  const out = (argv: readonly string[]): RunOut =>
    argv[3] === 'digest'
      ? { exitCode: 0, stdout: JSON.stringify(o.digest ?? { first: 'USER: start', tail: 'USER: start\nASSISTANT: stopped' }), stderr: '' }
      : scan instanceof Error
        ? { exitCode: 1, stdout: '', stderr: `Traceback\n${scan.message}\n` }
        : { exitCode: 0, stdout: JSON.stringify(scan), stderr: '' }
  ;(f as { deps: RestoreDeps }).deps = {
    source: o.source ?? 'tm',
    rh: {
      paths: async () => ({ configDir: 'C:\\Users\\me\\.claude', cache: 'C:\\tmp\\tm-restore-scan-cache.json', script: 'C:\\mod\\scanner\\scan.py' }),
      run: async argv => {
        f.runs.push(argv)
        return out(argv)
      },
    },
    tm: {
      call: async () => {
        const next = answers.length > 1 ? answers.shift()! : (answers[0] ?? refsReply([]))
        if (next === 'offline') throw new Error('$.mcp.call: no connected MCP tool "backlog_query" on a server named "plugin:taskmaster:tm"')
        return { text: next, isError: false }
      },
      sleep: () => new Promise<void>(() => undefined),
      log: text => void f.logs.push(text),
    },
    store: {
      get: async key => f.store.get(key),
      set: async (key, value) => void f.store.set(key, value),
      delete: async key => void f.store.delete(key),
    },
    read: { data: async () => f.data, view: async () => f.view },
    write: {
      // $.state hands back copies: so does this, or a flow keyed on object identity would pass here and fail live.
      data: async change => {
        f.data = JSON.parse(JSON.stringify(change(f.data))) as TmRestoreData
      },
      view: async change => {
        f.view = change(f.view)
      },
      signals: async (pending, burst) => void f.signals.push({ pending, burst }),
    },
    now: async () => o.now ?? (T0 + 3600) * 1000,
    wait: async ms => void f.waits.push(ms),
    sessionId: async () => o.self ?? 'me',
    repoRoot: async () => ROOT,
    toast: text => void f.toasts.push(text),
    openPane: async () => {
      f.opened += 1
      return true
    },
    loadPhases: () => {
      f.phasesAsked += 1
    },
    platform: async () => 'windows',
    launch: async specs => {
      f.launches.push(specs)
      return f.launchResult(specs)
    },
    copy: async text => {
      f.copies.push(text)
      return !text.includes('NOCLIP')
    },
    complete: async prompt => {
      f.prompts.push(prompt)
      return f.complete(prompt)
    },
  }
  return f
}
