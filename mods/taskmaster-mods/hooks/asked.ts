// User intent: a pane the person asks for with a press (a tab, the sidebar, the band's m) opens before that press's hook
// returns, as /tm-review's open lands inside its command hook, so the engine places it as the person's open, at any width,
// never as the plugin's unasked open that waits undrawn below 144 columns (the narrow-terminal tab switch left no pane at all).

const held = new Set<Promise<unknown>>()

/** A press handler's pane open: the press hook handling it waits for it (whileAsked) instead of returning first. */
export function holdOpen(work: Promise<unknown>): void {
  const settled = work.then(
    () => undefined,
    () => undefined,
  )
  held.add(settled)
  void settled.then(() => held.delete(settled))
}

/** A press or input hook's `next(e)`, then every pane open its handlers started, so each open lands inside the press. */
export async function whileAsked<T>(next: () => Promise<T>): Promise<T> {
  const answered = await next()
  while (held.size > 0) await Promise.all([...held])
  return answered
}
