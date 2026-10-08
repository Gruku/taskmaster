// User intent: read Taskmaster's MCP replies — markdown and JSON text — into plain data, purely, so every format quirk is
// pinned by a test against a captured real reply and an unreadable reply becomes a reason instead of a crash.
import type { TmHandover, TmHandoverSummary, TmPipeline, TmPriority, TmTaskDetail } from '../types'
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

/**
 * A reply's own text. Every tm tool declares an output schema `{"result": <text>}`, and since Claude Code 2.1.295 the
 * engine hands that structured result over as the text block (`{"result":"…"}`) in place of the text itself; it is
 * unwrapped here, from `structuredContent` when given, else from the block. Any other reply comes back unchanged.
 */
export function replyText(text: string, structured?: unknown): string {
  if (isObject(structured) && typeof structured.result === 'string') return structured.result
  const trimmed = text.trim()
  if (!/^\{\s*"result"\s*:/.test(trimmed)) return text
  try {
    const value: unknown = JSON.parse(trimmed)
    return isObject(value) && Object.keys(value).length === 1 && typeof value.result === 'string' ? value.result : text
  } catch {
    return text
  }
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

/** The continuity reply's server `total` (every item of the asked class, past the window), or null when it gives none. */
export function continuityTotal(raw: string): number | null {
  const doc = json(raw)
  return doc.ok && typeof doc.value.total === 'number' ? doc.value.total : null
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
      thread: str(h.thread),
    }))
    .sort((a, b) => (a.created < b.created ? 1 : a.created > b.created ? -1 : 0))
  const total = typeof doc.value.total === 'number' ? doc.value.total : handovers.length
  return ok({ handovers, total })
}

const SUMMARY_SECTION = /^### (decisions|blockers)\s*$/
const LIST_ITEM = /^(\s*)(?:[-*•]|\d+[.)])\s+(.*)$/

/**
 * A section block's items: its top-level list lines, each joined by the lines under it (continuations, nested bullets); a
 * block without one is prose, one item per paragraph.
 */
function itemsOf(block: readonly string[]): string[] {
  const topItem = (line: string): string | null => {
    const m = LIST_ITEM.exec(line)
    return m !== null && (m[1] ?? '').length < 2 ? (m[2] ?? '').trim() : null
  }
  const listed = block.some(line => topItem(line) !== null)
  const items: string[] = []
  let open = false
  for (const line of block) {
    const text = line.trim()
    if (text === '') {
      if (!listed) open = false
      continue
    }
    const head = topItem(line)
    if (listed && head !== null) {
      items.push(head)
      open = true
    } else if (open) {
      const last = items.length - 1
      items[last] = `${items[last] ?? ''} ${LIST_ITEM.exec(line)?.[2]?.trim() ?? text}`.trim()
    } else if (!listed) {
      items.push(text)
      open = true
    }
  }
  return items.filter(item => item !== '')
}

/**
 * backlog_handover_get with sections decisions + blockers: `## Handover: <id>`, then a `### decisions` / `### blockers`
 * block per section the body has (a missing one is left out, read as empty). Anything else, a refusal or "Handover not
 * found" included, is unreadable.
 */
export function parseHandoverSummary(raw: string): Parsed<TmHandoverSummary> {
  const text = raw.replace(/\r\n/g, '\n').trim()
  if (!text.startsWith('## Handover:')) return fail(`handover_get: ${firstParagraph(text) || 'empty reply'}`)
  const blocks: Record<'decisions' | 'blockers', string[]> = { decisions: [], blockers: [] }
  let current: string[] | null = null
  for (const line of text.split('\n').slice(1)) {
    const head = SUMMARY_SECTION.exec(line)
    if (head !== null) {
      current = blocks[head[1] as 'decisions' | 'blockers']
      continue
    }
    if (current !== null) current.push(line)
  }
  return ok({ decisions: itemsOf(blocks.decisions), blockers: itemsOf(blocks.blockers) })
}

export type HandoverReceipt = { readonly id: string; readonly path: string; readonly resume: string }

/** backlog_handover_create's receipt: `Handover written: <id>`, its `- Path:` line and its `Resume:` line; else null. */
export function parseHandoverWritten(raw: string): HandoverReceipt | null {
  const text = raw.replace(/\r\n/g, '\n')
  const id = /^Handover written: (\S+)\s*$/m.exec(text)?.[1]
  if (id === undefined) return null
  return {
    id,
    path: (/^- Path: (.+)$/m.exec(text)?.[1] ?? '').trim(),
    resume: (/^(Resume: .+)$/m.exec(text)?.[1] ?? '').trim(),
  }
}

export type IssueRow = { readonly id: string; readonly severity: string; readonly status: string; readonly title: string }

const ISSUE_ROW = /^- (\S+) (P[0-3]|\?) (\S+)\s+— (.*)$/
const ISSUE_FOOTER = /^…(\d+) more issues\b/
// The row's `[components]` tag, which ends the title (a tldr may follow it after ` — `).
const COMPONENTS = / \[[^\]]*\](?= — |$)/

/**
 * backlog_issue_list (slim): `- <id> <severity> <status padded> — <title>[ [components]][ — <tldr>]` per issue, and a
 * `…N more issues — …` footer when the limit hid some. The title ends at the components tag; without one the title and
 * tldr cannot be told apart (either may hold ` — `), so the rest is kept whole. Indented detail lines are skipped.
 */
export function parseIssueList(raw: string): Parsed<{ issues: IssueRow[]; hidden: number }> {
  const text = raw.replace(/\r\n/g, '\n').trim()
  if (/^No issues\b/.test(text)) return ok({ issues: [], hidden: 0 })
  if (isRefusal(text) || /^No backlog found/.test(text)) return fail(`issue_list: ${firstParagraph(text)}`)
  const issues: IssueRow[] = []
  let hidden = 0
  for (const line of text.split('\n')) {
    if (line.trim() === '' || /^\s/.test(line)) continue
    const footer = ISSUE_FOOTER.exec(line)
    if (footer !== null) {
      hidden = Number(footer[1])
      continue
    }
    const row = ISSUE_ROW.exec(line)
    if (row === null) return fail(`issue_list: unreadable line "${line.slice(0, 120)}"`)
    const rest = (row[4] ?? '').trim()
    const tag = COMPONENTS.exec(rest)
    issues.push({ id: row[1] ?? '', severity: row[2] ?? '', status: row[3] ?? '', title: (tag === null ? rest : rest.slice(0, tag.index)).trim() })
  }
  return ok({ issues, hidden })
}
