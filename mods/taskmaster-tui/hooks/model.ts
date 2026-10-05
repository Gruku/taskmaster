// User intent: the Taskmaster semantics behind every taskmaster-tui surface — what waits on the user, queue order, card and
// band wording, Telegram-ready handover text — as plain functions over snapshot data, tested without drawing.
import type { TmCursor, TmHandover, TmPipeline, TmPriority, TmQueueItem, TmSnapshot, TmTaskDetail } from '../types'

export const REVIEW = 'tm-review'
export const HANDOVERS = 'tm-handovers'
export const FRESH_CURSOR: TmCursor = { currentId: '', done: [], skipped: [], mode: 'card', refusal: '' }

export const PRIORITY_RANK: Readonly<Record<TmPriority, number>> = { critical: 0, high: 1, medium: 2, low: 3 }
export const PRIORITY_GLYPH: Readonly<Record<TmPriority, string>> = { critical: '◆', high: '▲', medium: 'ⓘ', low: '·' }
export const PRIORITY_WORD: Readonly<Record<TmPriority, string>> = { critical: 'Critical', high: 'High', medium: 'Medium', low: 'Low' }
export const PRIORITY_TONE: Readonly<Record<TmPriority, 'critical' | 'warning' | 'info' | 'quiet'>> = {
  critical: 'critical',
  high: 'warning',
  medium: 'info',
  low: 'quiet',
}

const KIND_RANK = { task: 0, issue: 1, decision: 2 } as const
const timeOf = (stamp: string): number => {
  const ms = Date.parse(stamp)
  return Number.isNaN(ms) ? Number.POSITIVE_INFINITY : ms
}

export function orderQueue(items: readonly TmQueueItem[]): TmQueueItem[] {
  const rank = (i: TmQueueItem) => (i.kind === 'task' ? PRIORITY_RANK[i.priority] : i.kind === 'issue' ? (i.severity === 'P0' ? 0 : 1) : 0)
  return items
    .map((item, index) => ({ item, index }))
    .sort(
      (a, b) =>
        KIND_RANK[a.item.kind] - KIND_RANK[b.item.kind] ||
        rank(a.item) - rank(b.item) ||
        (a.item.kind === 'task' ? 0 : timeOf(a.item.timestamp) - timeOf(b.item.timestamp)) ||
        a.index - b.index,
    )
    .map(entry => entry.item)
}

export type CardPosition = { readonly item: TmQueueItem | null; readonly n: number; readonly total: number }

// queueTotal is the server's count of everything waiting; the queue is a window of its first rows (priority order).
export function cardPosition(queue: readonly TmQueueItem[], c: TmCursor, queueTotal: number): CardPosition {
  const visible = queue.filter(i => !c.done.includes(i.id))
  const remaining = visible.filter(i => !c.skipped.includes(i.id))
  const item = remaining.find(i => i.id === c.currentId) ?? remaining[0] ?? null
  return { item, n: c.done.length + c.skipped.length + 1, total: c.done.length + Math.max(visible.length, queueTotal) }
}

// The queue the review pane walks: the loaded window, plus the pinned task (band `a`, a card a confirm or note was asked
// on) when the window does not hold it, built from that task's own detail by id — never a stand-in from the window.
export function reviewQueue(s: TmSnapshot, c: TmCursor, details: Readonly<Record<string, TmTaskDetail>>): readonly TmQueueItem[] {
  const id = c.currentId
  if (id === '' || s.queue.some(i => i.id === id) || c.done.includes(id) || c.skipped.includes(id)) return s.queue
  const detail = details[id] ?? (s.bound?.taskId === id ? s.bound.detail : null)
  if (detail === null) return s.queue
  const pinned: TmQueueItem = {
    kind: 'task',
    id,
    title: detail.title,
    priority: detail.priority,
    humanAction: detail.humanAction,
    timestamp: '',
  }
  return [pinned, ...s.queue]
}

// A confirm row or a note Input belongs to the card it was asked on; on any other card (the pinned one left the queue) the
// card shows plain, so `y` or a note can never land on a task the person did not press on.
export function cardMode(c: TmCursor, shown: TmQueueItem): TmCursor['mode'] {
  return c.currentId !== '' && c.currentId === shown.id ? c.mode : 'card'
}

const withoutItem = (s: TmSnapshot, id: string): Pick<TmSnapshot, 'queue' | 'queueTotal'> => ({
  queue: s.queue.filter(i => i.id !== id),
  queueTotal: Math.max(0, s.queueTotal - (s.queue.some(i => i.id === id) ? 1 : 0)),
})

export function afterDone(s: TmSnapshot, id: string): TmSnapshot {
  return { ...s, ...withoutItem(s, id), bound: s.bound?.taskId === id ? null : s.bound }
}

export function afterSendBack(s: TmSnapshot, id: string): TmSnapshot {
  const bound =
    s.bound !== null && s.bound.taskId === id && s.bound.detail !== null
      ? { ...s.bound, detail: { ...s.bound.detail, status: 'in-progress', humanAction: '' } }
      : s.bound
  return { ...s, ...withoutItem(s, id), bound }
}

export function metaLine(d: TmTaskDetail, withBranch: boolean): string {
  const parts = [d.lane ? d.lane.toUpperCase() : 'NO LANE', d.gateState || 'no gate state']
  if (withBranch) parts.push(d.branch || 'no branch')
  return parts.join(' · ')
}

export function stageLine(d: TmTaskDetail, p: TmPipeline | null): string {
  const status = d.status.toUpperCase().replace(/-/g, ' ')
  if (p === null) return status
  if (p.laneless) return `${status} → no pipeline`
  return p.outstanding.length === 0 ? `${status} → ready for done` : `${status} → next: ${p.outstanding[0]}`
}

export type BandTask = {
  readonly id: string
  readonly title: string
  readonly inferred: boolean
  readonly review: boolean
  readonly humanAction: string
  readonly meta: string
  readonly stage: string
}
export type BandModel = { readonly task: BandTask | null; readonly needsYou: number }
export type BandRow = 'task' | 'stage' | 'needs'

export function bandModel(s: TmSnapshot | null): BandModel | null {
  if (s === null) return null
  const b = s.bound
  const detail = b?.detail ?? null
  const live = s.reachable && detail !== null
  const task: BandTask | null =
    b === null
      ? null
      : {
          id: b.taskId,
          title: detail?.title ?? '',
          inferred: b.inferred,
          review: detail?.status === 'in-review',
          humanAction: detail?.humanAction ?? '',
          meta: live ? metaLine(detail, false) : '',
          stage: live ? stageLine(detail, b.pipeline) : '',
        }
  const needsYou = s.reachable ? s.queueTotal : 0
  if (task === null && needsYou === 0) return null
  return { task, needsYou }
}

export function bandRows(m: BandModel, maxRows: number): BandRow[] {
  const wanted: BandRow[] = []
  if (m.task !== null) wanted.push('task')
  if (m.needsYou > 0) wanted.push('needs')
  if (m.task !== null && (m.task.review || m.task.stage !== '')) wanted.push('stage')
  const kept = new Set(wanted.slice(0, Math.max(0, maxRows)))
  return (['task', 'stage', 'needs'] as const).filter(row => kept.has(row))
}

export function ageLabel(stamp: string, now: number): string {
  const ms = Date.parse(stamp)
  if (Number.isNaN(ms)) return ''
  const minutes = Math.max(0, Math.floor((now - ms) / 60_000))
  if (minutes < 60) return `${minutes}m`
  const hours = Math.floor(minutes / 60)
  if (hours < 48) return `${hours}h`
  return `${Math.floor(hours / 24)}d`
}

export function oneLine(text: string): string {
  return text.replace(/\s*\n\s*/g, ' ').trim()
}

export function truncate(text: string, width: number): string {
  if (width <= 0) return ''
  if (text.length <= width) return text
  if (width === 1) return '…'
  return `${text.slice(0, width - 1)}…`
}

export function dateOf(stamp: string): string {
  return stamp.slice(0, 10)
}

export function handoverPath(root: string, id: string): string {
  const sep = root.includes('\\') ? '\\' : '/'
  return [root.replace(/[\\/]+$/, ''), '.taskmaster', 'handovers', `${id}.md`].join(sep)
}

export function handoverCopyText(h: TmHandover): string {
  return [h.tldr.trim(), h.nextAction.trim() ? `Next: ${h.nextAction.trim()}` : '', h.path].filter(Boolean).join('\n\n')
}
