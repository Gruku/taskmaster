// User intent: one rule for running a Python helper from the mod (the restore scanner, the team-server reader): try python,
// python3, py in turn, and move on only when that interpreter did not start; a script that ran, crashed or timed out stands,
// so a slow server or a crash costs one process, never three (team-server-007 D14, review I3 and N1).

export type RunOut = { readonly exitCode: number; readonly stdout: string; readonly stderr: string }
/** A command by its argv: resolves on any exit; rejects when it cannot start, or is still running at `timeoutMs`. */
export type PyRun = (argv: readonly string[], timeoutMs: number, stdin?: string) => Promise<RunOut>

/** Tried in order: the first that starts answers. */
export const PYTHONS: readonly string[] = ['python', 'python3', 'py']
/** Exits that mean "not found": 9009 on Windows (also the Microsoft Store alias), 127 on POSIX shells. */
export const NOT_FOUND_EXITS: readonly number[] = [9009, 127]
/** A rejection this late in the bound is the timeout, not a failure to start. */
const TIMEOUT_SHARE = 0.9
const TIMEOUT_WORDS = /\btimed? ?out\b|\bstill running\b/i

/** The interpreter ran but answered nothing in time: it stands (no other interpreter is tried). */
export class PythonTimeout extends Error {}
/** No interpreter started; the message names the last reason. */
export class NoPython extends Error {}

export type PyOptions = {
  readonly timeoutMs: number
  readonly stdin?: string
  /** The clock that tells a quick rejection (did not start) from the timeout; absent, a rejection naming no timeout did not start. */
  readonly now?: () => Promise<number>
  /** An exit the script reserves for "this interpreter is too old" (tm_read.py: 3): the next one is tried. */
  readonly tooOldExit?: number
}

const lastLine = (text: string): string => text.trim().split('\n').at(-1)?.trim() ?? ''

/** Whether a finished run means this interpreter never ran the script (not found, too old to parse or run it). */
export function didNotStart(out: RunOut, tooOldExit?: number): boolean {
  if (NOT_FOUND_EXITS.includes(out.exitCode)) return true
  if (tooOldExit !== undefined && out.exitCode === tooOldExit) return true
  // Python 2 (or one too old for the syntax) fails to compile the script before running any of it.
  return out.exitCode !== 0 && /^SyntaxError\b/.test(lastLine(out.stderr))
}

/** Runs `<py> -I <script> ...args` under the first interpreter that starts: the run that stands, with the interpreter's name. */
export async function runPython(run: PyRun, script: string, args: readonly string[], o: PyOptions): Promise<{ readonly py: string; readonly out: RunOut }> {
  let reason = `no Python found (${PYTHONS.join(', ')})`
  for (const py of PYTHONS) {
    const started = o.now === undefined ? null : await o.now()
    let out: RunOut
    try {
      out = await run([py, '-I', script, ...args], o.timeoutMs, o.stdin)
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error)
      const late = started !== null && o.now !== undefined && (await o.now()) - started >= o.timeoutMs * TIMEOUT_SHARE
      if (late || TIMEOUT_WORDS.test(message)) throw new PythonTimeout(`${py}: no answer within ${Math.round(o.timeoutMs / 1000)} s`)
      reason = message
      continue
    }
    if (didNotStart(out, o.tooOldExit)) {
      reason = lastLine(out.stderr) || `${py} exited ${out.exitCode}`
      continue
    }
    return { py, out }
  }
  throw new NoPython(reason)
}

/** The reason a run that stands failed: its last stderr line, else its exit. */
export function exitReason(py: string, out: RunOut): string {
  return lastLine(out.stderr) || `${py} exited ${out.exitCode}`
}
