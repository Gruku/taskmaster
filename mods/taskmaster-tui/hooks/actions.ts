// User intent: the two Taskmaster writes the review surfaces make — sign off (done) and send back to the agent — behind one
// interface, so demo mode and the real tm server are interchangeable and a refusal always comes back as text to show.
import { DEMO_REFUSAL, DEMO_REFUSING_ID } from './demo'

export type TmWriteOutcome = { readonly ok: true } | { readonly ok: false; readonly refusal: string }

export type TmActions = {
  done: (taskId: string) => Promise<TmWriteOutcome>
  backToAgent: (taskId: string, note: string) => Promise<TmWriteOutcome>
}

export function demoActions(): TmActions {
  return {
    done: async taskId => (taskId === DEMO_REFUSING_ID ? { ok: false, refusal: DEMO_REFUSAL } : { ok: true }),
    backToAgent: async () => ({ ok: true }),
  }
}
