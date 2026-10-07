// User intent: the two Taskmaster writes the review surfaces make — sign off (done) and send back to the agent — behind one
// interface, so demo mode and the real tm server are interchangeable and a refusal always comes back as text to show.
import { DEMO_REFUSAL, DEMO_REFUSING_ID } from './demo'
import type { TmReply } from './host'
import { firstParagraph, stripSeq } from './parse'

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

/** The sign-off's changelog line (`done` text) on the task. */
export const SIGN_OFF = 'Signed off in review queue'

const COMPLETED = /^Completed `/
const UPDATED = /^(Updated `|No change to `)/
// The mod's own write timeout: the server may still have saved it, so the person checks before pressing again.
const NO_REPLY = /: no reply within \d+ s$/

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
      const caveat = NO_REPLY.test(reason) ? ' — it may still have been saved: check before pressing again' : ''
      return { ok: false, refusal: `Taskmaster unreachable: ${reason}${caveat}` }
    }
    const head = firstParagraph(stripSeq(reply.text))
    // Clearing an already-empty human_action answers "(not persisted)": that is the one place it means success.
    if (head.includes('(not persisted)') && !emptyValueOk) return { ok: false, refusal: `Taskmaster did not save it: ${head}` }
    if (!reply.isError && success.test(head)) return { ok: true }
    return { ok: false, refusal: head === '' ? 'Taskmaster refused without a reason' : head }
  }
  return {
    done: taskId => attempt('backlog_complete_task', { task_id: taskId, done: SIGN_OFF }, COMPLETED, false),
    // Ruling F14: status first (the server checks the transition), then the note, then the clear; so a failed clear never
    // loses the note. Stops at the first refusal and names its step.
    backToAgent: async (taskId, note) => {
      const steps: Step[] = [{ label: 'status', args: { task_id: taskId, field: 'status', value: 'in-progress' }, emptyValueOk: false }]
      if (note.trim() !== '') {
        steps.push({ label: 'next_step', args: { task_id: taskId, next_step: `Back from review: ${note.trim()}` }, emptyValueOk: false })
      }
      steps.push({ label: 'human_action', args: { task_id: taskId, field: 'human_action', value: '' }, emptyValueOk: true })
      for (const step of steps) {
        const out = await attempt('backlog_update_task', step.args, UPDATED, step.emptyValueOk)
        if (!out.ok) return { ok: false, refusal: `${step.label}: ${out.refusal}` }
      }
      return { ok: true }
    },
  }
}
