// User intent: in a team-server clone the mods read the server the way the hooks do: one tm_read.py process per batch,
// through the shared client, cache and key, its failures turned into the band's faults; with no marker nothing changes
// and no process runs (team-server-007 D14, D15).
import type { EngineInterface } from 'claude-code'

import type { TmReply } from './host'
import { exitReason, NoPython, type PyRun, PythonTimeout, runPython } from './python'
import { unframe } from './untrusted'

/** The process bound: interpreter start alone measured 5-7 s under load; the reader's own network deadline is 10 s. */
export const TM_REMOTE_TIMEOUT_MS = 15_000
/** Process time beyond the reader's own `elapsed_ms` that reads as a slow interpreter start. */
export const SLOW_START_MS = 3000
/** tm_read.py's exit for an interpreter too old to run it (the next one is tried). */
export const TM_READ_TOO_OLD = 3

export type TmCall = { readonly tool: string; readonly args: Record<string, unknown> }
/** How a failed read shows: `transient` takes the retry ladder, `offline` faults at once, `unreadable` is a bad reply. */
export type RemoteFault = 'transient' | 'offline' | 'unreadable'
/** `stale`: the reason the data came from the reader's stale cache (drawn with the offline fault), else null. */
export type RemoteRead = { readonly replies: readonly TmReply[]; readonly stale: string | null }
export type TmRemote = {
  readonly read: (calls: readonly TmCall[]) => Promise<RemoteRead>
  /** A write went to the server: the next read skips the cache. */
  readonly wrote: () => void
}

export class RemoteFailure extends Error {
  constructor(
    message: string,
    readonly fault: RemoteFault,
    readonly kind: string,
  ) {
    super(message)
  }
}

/** What the reader needs from the engine (bound in remoteDepsOf). */
export type RemoteDeps = {
  readonly exists: (path: string) => Promise<boolean>
  readonly run: PyRun
  readonly now: () => Promise<number>
  readonly log: (text: string) => void
  /** tm_read.py: taskmaster's hooks/, two levels above this mod's root (a checkout, or a plugin-dir run from one). */
  readonly script: string
  /** Where else an installed taskmaster plugin keeps it (the desktop upload copy and the marketplace cache hold the mod alone), or null. */
  readonly locate?: () => Promise<string | null>
}

/** What the reader search needs from the engine's file system. */
export type ReaderFs = {
  readonly exists: (path: string) => Promise<boolean>
  readonly read: (path: string) => Promise<string>
  readonly list: (path: string) => Promise<readonly string[]>
}

const slash = (path: string): string => path.replace(/\\/g, '/')
/** 7.5.0 above 7.5.0-rc.1 above 7.4.9; anything unparsable sorts last. */
export function compareVersions(a: string, b: string): number {
  const parts = (v: string): [number[], boolean] => [(v.split('-')[0] ?? '').split('.').map(n => (/^\d+$/.test(n) ? Number(n) : -1)), v.includes('-')]
  const [pa, ra] = parts(a)
  const [pb, rb] = parts(b)
  for (let i = 0; i < Math.max(pa.length, pb.length); i += 1) {
    const d = (pa[i] ?? 0) - (pb[i] ?? 0)
    if (d !== 0) return d
  }
  return ra === rb ? 0 : ra ? -1 : 1
}

/**
 * tm_read.py in an installed taskmaster plugin, for a mod that ships without the checkout around it (the desktop upload copy, the
 * marketplace cache). The plugins directory is read off this mod's own root; then the install paths Claude Code records for
 * `taskmaster@*`, then the newest cached version. Null when none holds the reader.
 */
export async function locateReader(fs: ReaderFs, pluginRoot: string): Promise<string | null> {
  const root = slash(pluginRoot)
  const at = root.lastIndexOf('/plugins/')
  if (at < 0) return null
  const plugins = root.slice(0, at + '/plugins'.length)
  const reader = (dir: string): string => `${slash(dir).replace(/\/$/, '')}/hooks/tm_read.py`
  // A mod installed from a marketplace cache looks for taskmaster in that same marketplace only: a same-named plugin from
  // another one must not become the reader that runs with the member's key in reach. A mod outside the cache (a desktop
  // upload) has no marketplace to prefer, so any one is searched.
  const own = /\/cache\/([^/]+)\/taskmaster-mods\/[^/]+\/?$/.exec(root)?.[1] ?? null
  try {
    const recorded = JSON.parse(await fs.read(`${plugins}/installed_plugins.json`)) as { plugins?: Record<string, { installPath?: unknown }[]> }
    for (const [key, installs] of Object.entries(recorded.plugins ?? {})) {
      if (!key.startsWith('taskmaster@') || (own !== null && key !== `taskmaster@${own}`) || !Array.isArray(installs)) continue
      for (const install of installs) {
        if (typeof install.installPath === 'string' && (await fs.exists(reader(install.installPath)))) return reader(install.installPath)
      }
    }
  } catch {
    // No record, or an unreadable one: the cache scan below.
  }
  try {
    for (const marketplace of await fs.list(`${plugins}/cache`)) {
      if (own !== null && marketplace !== own) continue
      let newest: string | null = null
      for (const version of await fs.list(`${plugins}/cache/${marketplace}/taskmaster`).catch(() => [])) {
        if (newest !== null && compareVersions(version, newest) <= 0) continue
        if (await fs.exists(reader(`${plugins}/cache/${marketplace}/taskmaster/${version}`))) newest = version
      }
      if (newest !== null) return reader(`${plugins}/cache/${marketplace}/taskmaster/${newest}`)
    }
  } catch {
    // No cache directory.
  }
  return null
}

/** 009's marker in the clone, then its latch in `.git/`. */
export function markerPaths(root: string): readonly string[] {
  return [`${root}/.taskmaster-server.json`, `${root}/.git/taskmaster-server.json`]
}

// The reader's error kinds (taskmaster/remote/client.py ReadUnavailable) as the band shows them (spec D14, review N2).
const FAULT_OF: Readonly<Record<string, RemoteFault>> = {
  unreachable: 'transient',
  busy: 'transient',
  server: 'transient',
  refused: 'offline',
  'no-key': 'offline',
  config: 'offline',
  version: 'offline',
  'bad-request': 'offline',
  'bad-response': 'unreadable',
}

type ReaderOut = {
  readonly error?: { readonly kind?: unknown; readonly message?: unknown }
  readonly data?: { readonly results?: unknown }
  readonly source?: unknown
  readonly age_s?: unknown
  readonly elapsed_ms?: unknown
}

function repliesOf(data: ReaderOut['data'], count: number): TmReply[] | null {
  const results = data?.results
  if (!Array.isArray(results) || results.length !== count) return null
  const replies: TmReply[] = []
  for (const r of results as unknown[]) {
    if (typeof r !== 'object' || r === null || typeof (r as { text?: unknown }).text !== 'string') return null
    replies.push({ text: unframe((r as { text: string }).text), isError: (r as { is_error?: unknown }).is_error === true })
  }
  return replies
}

/** One root's reader: null when the clone has no marker and no latch (solo: the MCP calls stand). Detection is once per root. */
export function remoteOf(deps: RemoteDeps): (root: string) => Promise<TmRemote | null> {
  const known = new Map<string, Promise<boolean>>()
  let fresh = false
  let slowStart = false
  const detect = async (root: string): Promise<boolean> => {
    for (const path of markerPaths(root)) if (await deps.exists(path)) return true
    return false
  }
  const read = async (root: string, calls: readonly TmCall[]): Promise<RemoteRead> => {
    const script = (await deps.exists(deps.script)) ? deps.script : ((await deps.locate?.()) ?? null)
    if (script === null) throw new RemoteFailure(`tm_read.py not found at ${deps.script}, nor in an installed taskmaster plugin`, 'offline', 'config')
    const useFresh = fresh
    fresh = false
    const started = await deps.now()
    let ran: Awaited<ReturnType<typeof runPython>>
    try {
      ran = await runPython(deps.run, script, ['tools', '--root', root, ...(useFresh ? ['--fresh'] : [])], {
        timeoutMs: TM_REMOTE_TIMEOUT_MS,
        stdin: JSON.stringify({ calls }),
        now: deps.now,
        tooOldExit: TM_READ_TOO_OLD,
      })
    } catch (error) {
      if (useFresh) fresh = true
      if (error instanceof PythonTimeout) {
        const why = slowStart ? 'Python is slow to start' : 'the team server did not answer'
        throw new RemoteFailure(`${error.message} (${why})`, 'transient', 'timeout')
      }
      if (error instanceof NoPython) throw new RemoteFailure(error.message, 'offline', 'no-python')
      throw error
    }
    const wall = (await deps.now()) - started
    if (ran.out.exitCode !== 0) throw new RemoteFailure(`tm_read.py: ${exitReason(ran.py, ran.out)}`, 'unreadable', 'crash')
    let out: ReaderOut
    try {
      out = JSON.parse(ran.out.stdout) as ReaderOut
    } catch {
      throw new RemoteFailure('tm_read.py printed no JSON', 'unreadable', 'bad-response')
    }
    const own = typeof out.elapsed_ms === 'number' ? out.elapsed_ms : wall
    slowStart = wall - own > SLOW_START_MS
    if (slowStart) deps.log(`taskmaster-mods: slow Python start (${Math.round((wall - own) / 1000)} s)`)
    if (out.error !== undefined) {
      const kind = typeof out.error.kind === 'string' ? out.error.kind : 'bad-response'
      const message = typeof out.error.message === 'string' && out.error.message !== '' ? out.error.message : `the team server read failed (${kind})`
      if (useFresh && FAULT_OF[kind] === 'transient') fresh = true
      throw new RemoteFailure(message, FAULT_OF[kind] ?? 'unreadable', kind)
    }
    const replies = repliesOf(out.data, calls.length)
    if (replies === null) throw new RemoteFailure('tm_read.py answered no results for the calls', 'unreadable', 'bad-response')
    const age = typeof out.age_s === 'number' ? Math.round(out.age_s) : 0
    return { replies, stale: out.source === 'stale' ? `team server unreachable; showing data read ${age} s ago` : null }
  }
  return async root => {
    let found = known.get(root)
    if (found === undefined) {
      found = detect(root).catch(() => false)
      known.set(root, found)
    }
    if (!(await found)) return null
    return {
      read: calls => read(root, calls),
      wrote: () => {
        fresh = true
      },
    }
  }
}

/** The reader's engine bindings: `$.fs.exists` for the marker, `$.process.run` with the calls on stdin. */
export function remoteDepsOf($: EngineInterface): RemoteDeps {
  return {
    exists: async path => {
      try {
        return await $.fs.exists(path)
      } catch {
        return false
      }
    },
    run: async (argv, timeoutMs, stdin) => {
      const r = await $.process.run(argv, stdin === undefined ? { timeoutMs } : { timeoutMs, stdin })
      return { exitCode: r.exitCode, stdout: r.stdout, stderr: r.stderr }
    },
    now: () => $.clock.now(),
    log: text => $.ui.log(text, { to: 'debug' }),
    script: `${$.plugin.root}/../../hooks/tm_read.py`,
    locate: () =>
      locateReader(
        {
          exists: async path => {
            try {
              return await $.fs.exists(path)
            } catch {
              return false
            }
          },
          read: path => $.fs.read(path) as Promise<string>,
          list: async path => (await $.fs.list(path)).map(entry => entry.name),
        },
        $.plugin.root,
      ),
  }
}
