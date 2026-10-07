// User intent: the two Taskmaster writes the review surfaces make — sign off (done) and send back to the agent — behind one
// interface, so demo mode and the real tm server are interchangeable and a refusal always comes back as text to show.
import { DEMO_REFUSAL, DEMO_REFUSING_ID } from './demo'
import type { TmReply } from './host'
import { firstParagraph, stripSeq } from './parse'
import { TmUnreachable } from './tm'

/**
 * A refusal may carry: `mayHaveMoved`, the task may have left review (a step after the status move failed, or a write got
 * no answer in time); `pending`, a write that got no answer in time and is still running, settling when it does.
 */
export type TmWriteOutcome =
  | { readonly ok: true }
  | { readonly ok: false; readonly refusal: string; readonly mayHaveMoved?: true; readonly pending?: Promise<unknown> }

export type TmActions = {
  done: (taskId: string) => Promise<TmWriteOutcome>
  /** `humanAction`: the check the card shows; '' only when it is known to be empty already. */
  backToAgent: (taskId: string, note: string, humanAction: string) => Promise<TmWriteOutcome>
}

export function demoActions(): TmActions {
  return {
    done: async taskId => (taskId === DEMO_REFUSING_ID ? { ok: false, refusal: DEMO_REFUSAL } : { ok: true }),
    backToAgent: async () => ({ ok: true }),
  }
}

/** The sign-off's changelog line (`done` text) on the task. */
export const SIGN_OFF = 'Signed off in review queue'

const COMPLETED = /^Completed `/
const UPDATED = /^(Updated `|No change to `)/

type Step = { readonly label: string; readonly args: Record<string, unknown>; readonly emptyValueOk: boolean }

/**
 * The tm server's writes. Success is read from the reply's first paragraph (tm answers a refusal as text, isError false);
 * anything else, "(not persisted)" included, is a refusal shown as the server worded it. Nothing is retried.
 */
export function tmActions(call: (tool: string, args: Record<string, unknown>) => Promise<TmReply>): TmActions {
  const attempt = async (tool: string, args: Record<string, unknown>, success: RegExp, emptyValueOk: boolean): Promise<TmWriteOutcome> => {
    let reply: TmReply
    try {
      reply = await call(tool, args)
    } catch (error) {
      const reason = error instanceof Error ? error.message : String(error)
      // The mod's own write timeout: the call runs on and the server may still save it.
      const pending = error instanceof TmUnreachable ? error.pending : undefined
      if (pending === undefined) return { ok: false, refusal: `Taskmaster unreachable: ${reason}` }
      const refusal = `Taskmaster unreachable: ${reason} — it may still have been saved: check before pressing again`
      return { ok: false, refusal, mayHaveMoved: true, pending }
    }
    const head = firstParagraph(stripSeq(reply.text))
    // Clearing a human_action that was already empty answers "(not persisted)": success there only. Clearing one that was
    // not (the legacy store answers a failed clear the same way) is a refusal.
    if (head.includes('(not persisted)') && !emptyValueOk) return { ok: false, refusal: `Taskmaster did not save it: ${head}` }
    if (!reply.isError && success.test(head)) return { ok: true }
    return { ok: false, refusal: head === '' ? 'Taskmaster refused without a reason' : head }
  }
  return {
    done: taskId => attempt('backlog_complete_task', { task_id: taskId, done: SIGN_OFF }, COMPLETED, false),
    // Ruling F14: status first (the server checks the transition), then the note, then the clear; so a failed clear never
    // loses the note. Stops at the first refusal and names its step; past the status step the task has left review.
    backToAgent: async (taskId, note, humanAction) => {
      const steps: Step[] = [{ label: 'status', args: { task_id: taskId, field: 'status', value: 'in-progress' }, emptyValueOk: false }]
      if (note.trim() !== '') {
        steps.push({ label: 'next_step', args: { task_id: taskId, next_step: `Back from review: ${note.trim()}` }, emptyValueOk: false })
      }
      steps.push({
        label: 'human_action',
        args: { task_id: taskId, field: 'human_action', value: '' },
        emptyValueOk: humanAction.trim() === '',
      })
      for (const step of steps) {
        const out = await attempt('backlog_update_task', step.args, UPDATED, step.emptyValueOk)
        if (out.ok) continue
        const moved = out.mayHaveMoved === true || step.label !== 'status'
        return { ...out, refusal: `${step.label}: ${out.refusal}`, ...(moved ? { mayHaveMoved: true as const } : {}) }
      }
      return { ok: true }
    },
  }
}
