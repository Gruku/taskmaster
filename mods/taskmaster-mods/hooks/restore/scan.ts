// User intent: the restore pane's two outside readers — scanner/scan.py run as its own process (transcripts outgrow the mod
// runtime's 4 MiB read) and the one backlog_query that resolves what it found — each failing to a reason, never a crash.
import type { TmRefs, TmScan, TmSessionScan } from '../../types'
import type { TmHost } from '../host'
import { exitReason, PYTHONS, runPython, type RunOut } from '../python'
import { callTm } from '../tm'
import { parseRefs, refsSql, refsSqlFor, scanOf, sessionScanOf } from './model'

export type { RunOut }
/** Where the scanner lives and what it reads: the config folder, its cache file, the script. */
export type RestorePaths = { readonly configDir: string; readonly cache: string; readonly script: string }
/** What the scan needs from the engine, bound once from `$` in hooks.tsx. `now` tells a quick start failure from a timeout. */
export type RestoreHost = {
  readonly run: (argv: readonly string[], timeoutMs: number) => Promise<RunOut>
  readonly paths: () => Promise<RestorePaths>
  readonly now?: () => Promise<number>
}
export type Digest = { readonly first: string; readonly tail: string }

export const SCAN_TIMEOUT_MS = 120_000
export const DIGEST_TIMEOUT_MS = 60_000
export const SESSION_SCAN_TIMEOUT_MS = 60_000
export { PYTHONS }

/**
 * Runs scan.py isolated (`-I`) under the first Python that starts (python.ts's rule: a crash or a timeout stands, never
 * retried under another interpreter); throws an Error naming why it gave no JSON.
 */
async function runScanner(rh: RestoreHost, args: readonly string[], timeoutMs: number): Promise<unknown> {
  const { script } = await rh.paths()
  const { py, out } = await runPython(rh.run, script, args, { timeoutMs, ...(rh.now === undefined ? {} : { now: rh.now }) })
  if (out.exitCode !== 0) throw new Error(exitReason(py, out))
  try {
    return JSON.parse(out.stdout) as unknown
  } catch {
    throw new Error('the scanner printed no JSON')
  }
}
/** This repo's sessions of the last `days` days, less `exclude` (spec §10.1). */
export async function runScan(rh: RestoreHost, a: { readonly root: string; readonly days: number; readonly exclude: string }): Promise<TmScan> {
  const { configDir, cache } = await rh.paths()
  const args = ['scan', '--config-dir', configDir, '--root', a.root, '--days', String(a.days), '--cache', cache]
  const scan = scanOf(await runScanner(rh, a.exclude === '' ? args : [...args, '--exclude', a.exclude], SCAN_TIMEOUT_MS))
  if (scan === null) throw new Error('the scanner answered no session list')
  return scan
}

/** One transcript's condensed tail for a brief; an Error names why it could not be read. */
export async function runDigest(rh: RestoreHost, sessionId: string, root: string): Promise<Digest> {
  const { configDir } = await rh.paths()
  const out = await runScanner(rh, ['digest', '--config-dir', configDir, '--session', sessionId, '--root', root], DIGEST_TIMEOUT_MS)
  const v = typeof out === 'object' && out !== null ? (out as Record<string, unknown>) : {}
  if (typeof v.error === 'string') throw new Error(v.error)
  return { first: typeof v.first === 'string' ? v.first : '', tail: typeof v.tail === 'string' ? v.tail : '' }
}

/** The current session's own transcript: every task it touched and handover it wrote (its own cache file, beside restore's). */
export async function runSessionScan(rh: RestoreHost, a: { readonly root: string; readonly sessionId: string }): Promise<TmSessionScan> {
  const { configDir, cache } = await rh.paths()
  const args = ['session', '--config-dir', configDir, '--root', a.root, '--session', a.sessionId, '--cache', cache.replace(/\.json$/, '-session.json')]
  const out = await runScanner(rh, args, SESSION_SCAN_TIMEOUT_MS)
  if (typeof out === 'object' && out !== null && typeof (out as Record<string, unknown>).error === 'string') throw new Error(String((out as Record<string, unknown>).error))
  const scan = sessionScanOf(out)
  if (scan === null) throw new Error('the scanner answered no session history')
  return scan
}

/**
 * The scan's task and handover ids as tm holds them, from one backlog_query (spec §10.2). Nothing to ask resolves empty
 * without a call; a refusal, a timeout or an unreadable reply is null (statuses then read `?`), its reason logged.
 */
export async function resolveRefs(tm: Pick<TmHost, 'call' | 'sleep' | 'log' | 'remote'>, scan: TmScan): Promise<TmRefs | null> {
  return resolveRefsSql(tm, refsSql(scan))
}

/** The Session tab's task ids as tm holds them (same query, same degrade-to-null). */
export async function resolveTaskRefs(tm: Pick<TmHost, 'call' | 'sleep' | 'log' | 'remote'>, taskIds: readonly string[]): Promise<TmRefs | null> {
  return resolveRefsSql(tm, refsSqlFor(taskIds, []))
}

async function resolveRefsSql(tm: Pick<TmHost, 'call' | 'sleep' | 'log' | 'remote'>, sql: string | null): Promise<TmRefs | null> {
  if (sql === null) return { tasks: {}, handovers: {} }
  try {
    const text = (await callTm(tm, 'backlog_query', { sql, limit: 500 })).text
    const parsed = parseRefs(text)
    if (parsed.ok) return parsed.value
    tm.log(`taskmaster-mods: restore refs unreadable: ${parsed.reason}`)
    return null
  } catch (error) {
    tm.log(`taskmaster-mods: restore refs not read: ${error instanceof Error ? error.message : String(error)}`)
    return null
  }
}
