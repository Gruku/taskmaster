// User intent: refresh Taskmaster data at most once at a time — later triggers collapse into one trailing refresh — never on a
// timer and never awaited by a turn, so a slow or hung tm server can't stack calls or stall the session.
export type Refresher = { readonly request: () => void; readonly idle: () => Promise<void> }

export function singleFlight(run: () => Promise<void>, schedule: (fn: () => void) => void): Refresher {
  let running: Promise<void> | null = null
  let again = false
  const loop = async (): Promise<void> => {
    try {
      do {
        again = false
        await run()
      } while (again)
    } finally {
      running = null
    }
  }
  return {
    request: () => {
      if (running !== null) {
        again = true
        return
      }
      running = new Promise<void>(resolve => {
        schedule(() => {
          void loop().then(resolve, resolve)
        })
      })
    },
    idle: () => running ?? Promise.resolve(),
  }
}
