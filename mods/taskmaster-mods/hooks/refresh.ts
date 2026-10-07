// User intent: refresh Taskmaster data at most once at a time — later triggers collapse into one trailing refresh — never on a
// timer and never awaited by a turn, so a slow or hung tm server can't stack calls or stall the session. The one timed run
// is the retry of a transient failure (a server still connecting, or cold and slow): 2, 4, 8, 16 s, then the fault stands.
export type Refresher = { readonly request: () => void; readonly idle: () => Promise<void> }
/** What a run reports: `transient` asks for a retry while any remain; anything else (or nothing) is done. */
export type RunOutcome = 'done' | 'transient'

export const RETRY_DELAYS_MS: readonly number[] = [2000, 4000, 8000, 16000]

/**
 * `run(final)`: `final` is true when no retry remains after this run, so a transient failure must then be reported.
 * `schedule(fn, delayMs)`: 0 for a request, a backoff delay for a retry (the seam tests drive timers through).
 *
 * A request while a run (or a retry) is scheduled but not yet started is absorbed by it: that run reads the state as it then
 * is. A request while one runs asks for one trailing run. Any run that is not transient resets the backoff; once the retries
 * are spent, later runs are final until one succeeds.
 */
export function singleFlight(
  run: (final: boolean) => Promise<RunOutcome | void>,
  schedule: (fn: () => void, delayMs: number) => void,
): Refresher {
  let running: Promise<void> | null = null
  let started = false
  let again = false
  let retries = 0
  const start = (delayMs: number): void => {
    running = new Promise<void>(resolve => {
      schedule(() => {
        void loop().then(resolve, resolve)
      }, delayMs)
    })
  }
  const loop = async (): Promise<void> => {
    started = true
    let outcome: RunOutcome | void = 'done'
    try {
      do {
        again = false
        outcome = await run(retries >= RETRY_DELAYS_MS.length)
        if (outcome !== 'transient') retries = 0
      } while (again)
    } finally {
      running = null
      started = false
    }
    if (outcome === 'transient' && retries < RETRY_DELAYS_MS.length) {
      const delay = RETRY_DELAYS_MS[retries] ?? 0
      retries += 1
      start(delay)
    }
  }
  return {
    request: () => {
      if (running !== null) {
        if (started) again = true
        return
      }
      start(0)
    },
    idle: () => running ?? Promise.resolve(),
  }
}
