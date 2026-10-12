// User intent: the restore pane's two outside readers — scanner/scan.py run as its own process (transcripts outgrow the mod
// runtime's 4 MiB read) and the one backlog_query that resolves what it found — each failing to a reason, never a crash.
import type { TmRefs, TmScan } from '../../types'
import type { TmHost } from '../host'
import { callTm } from '../tm'
import { parseRefs, refsSql, scanOf } from './model'

export type RunOut = { readonly exitCode: number; readonly stdout: string; readonly stderr: string }
/** Where the scanner lives and what it reads: the config folder, its cache file, the script. */
export type RestorePaths = { readonly configDir: string; readonly cache: string; readonly script: string }
/** What the scan needs from the engine, bound once from `$` in hooks.tsx. */
export type RestoreHost = {
  readonly run: (argv: readonly string[], timeoutMs: number) => Promise<RunOut>
  readonly paths: () => Promise<RestorePaths>
}
export type Digest = { readonly first: string; readonly tail: string }

export const SCAN_TIMEOUT_MS = 120_000
export const DIGEST_TIMEOUT_MS = 60_000
/** Tried in order: the first that runs the script answers. */
export const PYTHONS: readonly string[] = ['python', 'python3', 'py']

/** Runs scan.py isolated (`-I`) under the first Python that starts; throws an Error naming why none answered. */
async function runPython(rh: RestoreHost, args: readonly string[], timeoutMs: number): Promise<unknown> {
  const { script } = await rh.paths()
  let reason = 'no Python found (python, python3, py)'
  for (const py of PYTHONS) {
    let out: RunOut
    try {
      out = await rh.run([py, '-I', script, ...args], timeoutMs)
    } catch (error) {
      reason = error instanceof Error ? error.message : String(error)
      continue
    }
    if (out.exitCode !== 0) {
      reason = out.stderr.trim().split('\n').at(-1)?.trim() || `${py} exited ${out.exitCode}`
      continue
    }
    try {
      return JSON.parse(out.stdout) as unknown
    } catch {
      throw new Error('the scanner printed no JSON')
    }
  }
  throw new Error(reason)
}

/** This repo's sessions of the last `days` days, less `exclude` (spec §10.1). */
export async function runScan(rh: RestoreHost, a: { readonly root: string; readonly days: number; readonly exclude: string }): Promise<TmScan> {
  const { configDir, cache } = await rh.paths()
  const args = ['scan', '--config-dir', configDir, '--root', a.root, '--days', String(a.days), '--cache', cache]
  const scan = scanOf(await runPython(rh, a.exclude === '' ? args : [...args, '--exclude', a.exclude], SCAN_TIMEOUT_MS))
  if (scan === null) throw new Error('the scanner answered no session list')
  return scan
}

/** One transcript's condensed tail for a brief; an Error names why it could not be read. */
export async function runDigest(rh: RestoreHost, sessionId: string, root: string): Promise<Digest> {
  const { configDir } = await rh.paths()
  const out = await runPython(rh, ['digest', '--config-dir', configDir, '--session', sessionId, '--root', root], DIGEST_TIMEOUT_MS)
  const v = typeof out === 'object' && out !== null ? (out as Record<string, unknown>) : {}
  if (typeof v.error === 'string') throw new Error(v.error)
  return { first: typeof v.first === 'string' ? v.first : '', tail: typeof v.tail === 'string' ? v.tail : '' }
}

/**
 * The scan's task and handover ids as tm holds them, from one backlog_query (spec §10.2). Nothing to ask resolves empty
 * without a call; a refusal, a timeout or an unreadable reply is null (statuses then read `?`), its reason logged.
 */
export async function resolveRefs(tm: Pick<TmHost, 'call' | 'sleep' | 'log'>, scan: TmScan): Promise<TmRefs | null> {
  const sql = refsSql(scan)
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
