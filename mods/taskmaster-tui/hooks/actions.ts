// User intent: the two Taskmaster writes the review surfaces make — sign off (done) and send back to the agent — behind one
// interface, so demo mode and the real tm server are interchangeable and a refusal always comes back as text to show.
import { DEMO_REFUSAL, DEMO_REFUSING_ID } from './demo'

export type TmWriteOutcome = { readonly ok: true } | { readonly ok: false; readonly refusal: string }

export type TmActions = {
  done: (taskId: string) => Promise<TmWriteOutcome>
  backToAgent: (taskId: string, note: string) => Promise<TmWriteOutcome>
}

/** tm mode until Task 4 wires the real writes: every write refuses, so nothing is ever shown as signed off that was not. */
export function readOnlyActions(): TmActions {
  const refusal = 'taskmaster-tui is read-only in this build: sign off and send back arrive with the write actions.'
  return { done: async () => ({ ok: false, refusal }), backToAgent: async () => ({ ok: false, refusal }) }
}

export function demoActions(): TmActions {
  return {
    done: async taskId => (taskId === DEMO_REFUSING_ID ? { ok: false, refusal: DEMO_REFUSAL } : { ok: true }),
    backToAgent: async () => ({ ok: true }),
  }
}
