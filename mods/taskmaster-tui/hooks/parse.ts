// User intent: read Taskmaster's MCP replies — markdown and JSON text — into plain data, purely, so every format quirk is
// pinned by a test against a captured real reply and an unreadable reply becomes a reason instead of a crash.
import type { TmHandover, TmPipeline, TmPriority, TmTaskDetail } from '../types'
import { handoverPath } from './model'

export type Parsed<T> = { readonly ok: true; readonly value: T } | { readonly ok: false; readonly reason: string }
export type ListRow = {
  readonly id: string
  readonly title: string
  readonly priority: TmPriority
  readonly epic: string
  readonly status: string
  readonly humanAction: string
}
export type ContinuityItem = {
  readonly id: string
  readonly type: string
  readonly title: string
  readonly next: string
  readonly actionClass: string
  readonly timestamp: string
  readonly taskId: string
}

const ok = <T>(value: T): Parsed<T> => ({ ok: true, value })
const fail = <T>(reason: string): Parsed<T> => ({ ok: false, reason })

const PRIORITY: Readonly<Record<string, TmPriority>> = {
  critical: 'critical',
  high: 'high',
  medium: 'medium',
  low: 'low',
  P0: 'critical',
  P1: 'high',
  P2: 'medium',
  P3: 'low',
}
const ROW = /^- `([^`]+)` — (.*?) \((critical|high|medium|low|P[0-3]), ([^,()]+), (todo|in-progress|in-review|done|archived|blocked)\)(?: — (.*))?$/
const HUMAN = /^ {4}waiting-on-human: ?(.*)$/
const HEADER = /^## `([^`]+)` — (.*)$/
const FIELD = /^\*\*([a-z_]+):\*\* ?(.*)$/

export function stripSeq(text: string): string {
  return text.replace(/ \[seq \d+\]/g, '').replace(/ \(export pending: [^)]*\)/g, '')
}

export function isRefusal(text: string): boolean {
  return /^(Error:|Cannot complete)/.test(text.trim())
}

export function firstParagraph(text: string): string {
  return text.trim().split(/\n\s*\n/)[0]?.trim() ?? ''
}

function clean(raw: string): string {
  return (stripSeq(raw).replace(/\r\n/g, '\n').split('\n\nWarning: ')[0] ?? '').trim()
}

const str = (v: unknown): string => (typeof v === 'string' ? v : v === null || v === undefined ? '' : String(v))
const isObject = (v: unknown): v is Record<string, unknown> => typeof v === 'object' && v !== null && !Array.isArray(v)

function json(raw: string): Parsed<Record<string, unknown>> {
  try {
    const value: unknown = JSON.parse(raw.trim())
    return isObject(value) ? ok(value) : fail('reply is JSON but not an object')
  } catch {
    return fail('reply is not JSON')
  }
}

export function claimOk(raw: string): boolean {
  const doc = json(raw)
  return doc.ok && doc.value.ok === true
}

export function parseListTasks(raw: string): Parsed<{ rows: ListRow[]; total: number }> {
  const text = clean(raw)
  if (text.startsWith('No tasks found')) return ok({ rows: [], total: 0 })
  if (isRefusal(text)) return fail(`list_tasks: ${firstParagraph(text)}`)
  const lines = text.split('\n')
  const header = /^\*\*(\d+) tasks/.exec(lines[0] ?? '')
  if (header === null) return fail('list_tasks: no "**N tasks:**" header')
  const rows: { id: string; title: string; priority: TmPriority; epic: string; status: string; human: string[] | null }[] = []
  for (const line of lines.slice(1)) {
    const row = ROW.exec(line)
    if (row !== null) {
      rows.push({
        id: row[1] ?? '',
        title: row[2] ?? '',
        priority: PRIORITY[row[3] ?? ''] ?? 'medium',
        epic: (row[4] ?? '').trim(),
        status: row[5] ?? '',
        human: null,
      })
      continue
    }
    const last = rows.at(-1)
    if (last === undefined || line.startsWith('…')) continue
    const human = HUMAN.exec(line)
    if (human !== null) {
      last.human = [human[1] ?? '']
      continue
    }
    if (last.human !== null) last.human.push(line)
  }
  if (rows.length === 0) return fail('list_tasks: a header but no rows it could read')
  return ok({
    rows: rows.map(r => ({ id: r.id, title: r.title, priority: r.priority, epic: r.epic, status: r.status, humanAction: (r.human ?? []).join('\n').trim() })),
    total: Number(header[1]),
  })
}

export function parseContinuity(raw: string): Parsed<ContinuityItem[]> {
  const doc = json(raw)
  if (!doc.ok) return fail(`continuity_items: ${doc.reason}`)
  if (typeof doc.value.error === 'string') return fail(`continuity_items: ${doc.value.error}`)
  const items = doc.value.items
  if (!Array.isArray(items)) return fail('continuity_items: no items array')
  return ok(
    items
      .filter(isObject)
      .map(i => ({
        id: str(i.id),
        type: str(i.type),
        title: str(i.title),
        next: str(i.next),
        actionClass: str(i.action_class),
        timestamp: str(i.timestamp),
        taskId: str(i.task_id),
      }))
      .filter(i => i.id !== ''),
  )
}

export function parseGetTask(raw: string): Parsed<TmTaskDetail | null> {
  const text = clean(raw)
  if (/^Error: task `[^`]+` not found/.test(text)) return ok(null)
  if (isRefusal(text)) return fail(`get_task: ${firstParagraph(text)}`)
  const lines = text.split('\n')
  const head = HEADER.exec(lines[0] ?? '')
  if (head === null) return fail('get_task: no "## `id` — title" header')
  const fields = new Map<string, string[]>()
  let current: string[] | null = null
  for (const line of lines.slice(1)) {
    if (/^\*\*links:\*\*/.test(line)) break
    const field = FIELD.exec(line)
    if (field !== null) {
      current = [field[2] ?? '']
      fields.set(field[1] ?? '', current)
      continue
    }
    if (current !== null) current.push(line)
  }
  const get = (key: string): string => (fields.get(key) ?? []).join('\n').trim()
  return ok({
    id: head[1] ?? '',
    title: (head[2] ?? '').trim(),
    status: get('status'),
    priority: PRIORITY[get('priority')] ?? 'medium',
    lane: get('lane'),
    gateState: get('gate_state'),
    branch: get('branch'),
    humanAction: get('human_action'),
  })
}

export function parsePipeline(raw: string): Parsed<TmPipeline> {
  const text = clean(raw)
  if (/is laneless \(pre-protocol\)/.test(text)) return ok({ laneless: true, lane: '', gateState: '', outstanding: [] })
  if (isRefusal(text)) return fail(`task_pipeline: ${firstParagraph(text)}`)
  const head = /^## Pipeline `[^`]+` — lane: \*\*([^*]+)\*\*/m.exec(text)
  if (head === null) return fail('task_pipeline: no "## Pipeline" header')
  const out = /^\*\*Outstanding:\*\* (.*)$/m.exec(text)
  if (out === null) return fail('task_pipeline: no "**Outstanding:**" line')
  const rest = (out[1] ?? '').trim()
  const outstanding = rest.startsWith('none') ? [] : rest.split(',').map(g => g.trim().replace(/^`|`$/g, '')).filter(g => g !== '')
  const gate = /^gate_state: `([^`]*)`/m.exec(text)?.[1] ?? ''
  return ok({ laneless: false, lane: (head[1] ?? '').trim(), gateState: gate === '(none)' ? '' : gate, outstanding })
}

export function parseHandovers(raw: string, root: string): Parsed<{ handovers: TmHandover[]; total: number }> {
  const doc = json(raw)
  if (!doc.ok) return fail(`handover_list: ${doc.reason}`)
  if (typeof doc.value.error === 'string') return fail(`handover_list: ${doc.value.error}`)
  const list = doc.value.handovers
  if (!Array.isArray(list)) return fail('handover_list: no handovers array')
  const handovers = list
    .filter(isObject)
    .filter(h => str(h.id) !== '' && str(h.status) !== 'superseded')
    .map(h => ({
      id: str(h.id),
      created: str(h.created) || str(h.date),
      tldr: str(h.tldr),
      nextAction: str(h.next_action),
      path: handoverPath(root, str(h.id)),
      branch: str(h.branch),
      taskIds: Array.isArray(h.task_ids) ? h.task_ids.filter((t): t is string => typeof t === 'string') : [],
    }))
    .sort((a, b) => (a.created < b.created ? 1 : a.created > b.created ? -1 : 0))
  const total = typeof doc.value.total === 'number' ? doc.value.total : handovers.length
  return ok({ handovers, total })
}
