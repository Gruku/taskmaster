// User intent: which task this session is on — set by its own pick, claim renewal or move to in-progress, kept
// through review, let go when done — decided purely from the tool call, plus the stored mirror's housekeeping; never another
// session's task.
import type { TmBinding } from '../types'
import type { TmHost } from './host'
import { claimOk, firstParagraph, isRefusal, stripSeq } from './parse'

export const TM_TOOL_PREFIX = 'mcp__plugin_taskmaster_tm__'
export const STALE_MS = 30 * 24 * 3_600_000

export type TmCall = { readonly tool: string; readonly input: Readonly<Record<string, unknown>>; readonly ok: boolean }
export type BindingChange = { readonly kind: 'set'; readonly taskId: string } | { readonly kind: 'clear' } | { readonly kind: 'keep' }
export type TmRan = { readonly deny?: unknown; readonly isError?: unknown; readonly text?: unknown; readonly result?: unknown }

const shortName = (tool: string): string => (tool.startsWith(TM_TOOL_PREFIX) ? tool.slice(TM_TOOL_PREFIX.length) : tool)

export function bindingChange(call: TmCall, bound: string | null): BindingChange {
  if (!call.ok) return { kind: 'keep' }
  const tool = shortName(call.tool)
  const id = typeof call.input.task_id === 'string' ? call.input.task_id : ''
  if (id === '') return { kind: 'keep' }
  const mine = bound !== null && id === bound
  if (tool === 'backlog_pick_task') return { kind: 'set', taskId: id }
  // Spec §6.3 / ruling F11: a claim taken or kept binds. backlog_claim's actions are renew, release and status (a claim is
  // taken by backlog_pick_task, above): renew binds; release and status do not.
  if (tool === 'backlog_claim') return call.input.action === 'renew' ? { kind: 'set', taskId: id } : { kind: 'keep' }
  if (tool === 'backlog_update_task' && call.input.field === 'status') {
    const value = String(call.input.value ?? '')
    if (value === 'in-progress') return { kind: 'set', taskId: id }
    return mine && (value === 'todo' || value === 'done' || value === 'archived') ? { kind: 'clear' } : { kind: 'keep' }
  }
  if (tool === 'backlog_complete_task') {
    const target = String(call.input.target_status ?? '') || 'done'
    return mine && target === 'done' ? { kind: 'clear' } : { kind: 'keep' }
  }
  if (tool === 'backlog_archive_task') return mine ? { kind: 'clear' } : { kind: 'keep' }
  return { kind: 'keep' }
}

export function ranText(ran: TmRan): string {
  if (typeof ran.text === 'string') return ran.text
  if (typeof ran.result === 'string') return ran.result
  const content = (ran.result as { content?: unknown } | null | undefined)?.content
  if (!Array.isArray(content)) return ''
  return content
    .map(block => (typeof block === 'object' && block !== null && 'text' in block ? String((block as { text: unknown }).text) : ''))
    .join('\n')
}

/**
 * Whether the server did what the call asked, read from its reply: tm answers a refusal as text, not as an error. A
 * "(not persisted)" reply is a failure, except for clearing a `human_action` that was already empty (`input` says so): the
 * server answers that clear the same way, and it is a success for that call only.
 */
export function callSucceeded(tool: string, ran: TmRan, input: Readonly<Record<string, unknown>> = {}): boolean {
  if (ran.deny !== undefined || ran.isError === true) return false
  const text = ranText(ran)
  if (text === '') return true
  if (shortName(tool) === 'backlog_claim') return claimOk(text)
  const head = firstParagraph(stripSeq(text))
  if (isRefusal(head)) return false
  if (!head.includes('(not persisted)')) return true
  return shortName(tool) === 'backlog_update_task' && input.field === 'human_action' && input.value === '' && head.includes('`human_action`')
}

const READ_ONLY =
  /^backlog_(get_|list_|search|status$|context|continuity_items|handover_list|handover_get|task_pipeline|query|changes_since|dependencies|epic_status|phase_status|index_status|store_status|validate|last_session|blast_radius|project_get|area_get|area_list|bug_get|bug_list|idea_get|idea_list|issue_get|issue_list|thread_list|document$|batch_preview)/

export function isWriteTool(tool: string): boolean {
  const name = shortName(tool)
  return name.startsWith('backlog_') && !READ_ONLY.test(name)
}

const TASK_ID = /([a-z][a-z0-9]*(?:-[a-z][a-z0-9]*)*-\d{3})(?!\d)/

export function inferTaskId(names: readonly string[]): string | null {
  for (const name of names) {
    const found = TASK_ID.exec(name)
    if (found !== null) return found[1] ?? null
  }
  return null
}

export function baseName(path: string): string {
  return path.split(/[\\/]/).filter(part => part !== '').at(-1) ?? ''
}

export function parseStoredBinding(value: unknown): TmBinding | null {
  if (typeof value !== 'object' || value === null) return null
  const v = value as { taskId?: unknown; at?: unknown }
  return typeof v.taskId === 'string' && v.taskId !== '' && typeof v.at === 'number' ? { taskId: v.taskId, at: v.at } : null
}

export async function pruneBindings(host: Pick<TmHost, 'storeKeys' | 'storeGet' | 'storeDelete'>, now: number): Promise<number> {
  let pruned = 0
  for (const key of await host.storeKeys()) {
    if (!key.startsWith('binding:')) continue
    const binding = parseStoredBinding(await host.storeGet(key))
    if (binding === null || now - binding.at > STALE_MS) {
      await host.storeDelete(key)
      pruned += 1
    }
  }
  return pruned
}
