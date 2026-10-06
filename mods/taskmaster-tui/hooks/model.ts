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

/**
 * `branch: X · tasks: a, b`, leaving out whichever is empty ('' when both are). Either may be missing at run time (a reply
 * without them, a snapshot older than the fields): missing reads as empty, never throws.
 */
export function handoverRefs(h: TmHandover): string {
  const branch = typeof h.branch === 'string' ? h.branch : ''
  const tasks = Array.isArray(h.taskIds) ? h.taskIds.filter(id => typeof id === 'string' && id !== '') : []
  return [branch ? `branch: ${branch}` : '', tasks.length > 0 ? `tasks: ${tasks.join(', ')}` : ''].filter(Boolean).join(' · ')
}

// ── The review card (redesigned 2026-10-06, spec §6.1) ─────────────────────────────────────────────────────────────

/** A human_action split into the card's checklist: an optional section label (with its dim detail) and the items. */
export type TmCheck = { readonly label: string; readonly detail: string; readonly items: readonly string[] }

const LIST_LINE = /^(?:[-*•]|\d+[.)])\s+(.*)$/
// `<label>:` or `<label> (<detail>):` at the start, the colon followed by whitespace or the end: a time (`10:30`) or a
// URL's `://` is never a label.
const LEADING_LABEL = /^([A-Za-z][^:;()]{0,39}?)\s*(?:\(([^)]*)\))?\s*:(?=\s|$)\s*([\s\S]*)$/

function labelOf(text: string): { label: string; detail: string; rest: string } | null {
  const m = LEADING_LABEL.exec(text)
  if (m === null) return null
  return { label: (m[1] ?? '').trim(), detail: (m[2] ?? '').trim(), rest: (m[3] ?? '').trim() }
}

/**
 * Bullet or numbered lines are the items (continuation lines join the item above; a `<label>:` intro labels them).
 * Else a leading `<label>:` labels the rest, split into `;` clauses. Else the whole text is one item.
 */
export function splitCheck(text: string): TmCheck {
  const lines = text
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(line => line !== '')
  if (lines.length === 0) return { label: '', detail: '', items: [] }
  const first = lines.findIndex(line => LIST_LINE.test(line))
  if (first >= 0) {
    const items: string[] = []
    for (const line of lines.slice(first)) {
      const m = LIST_LINE.exec(line)
      if (m !== null) items.push((m[1] ?? '').trim())
      else items[items.length - 1] = `${items[items.length - 1] ?? ''} ${line}`.trim()
    }
    const intro = lines.slice(0, first).join(' ')
    const head = labelOf(intro)
    return head !== null && head.rest === '' ? { label: head.label, detail: head.detail, items } : { label: '', detail: intro, items }
  }
  const whole = oneLine(text)
  const head = labelOf(whole)
  if (head !== null && head.rest !== '') {
    const items = head.rest
      .split(';')
      .map(clause => clause.trim().replace(/\.$/, ''))
      .filter(clause => clause !== '')
    return { label: head.label, detail: head.detail, items }
  }
  return { label: '', detail: '', items: [whole] }
}

/** Word-wraps to lines of at most `width` cells; a word longer than the width is cut. Empty text gives no lines. */
export function wrapText(text: string, width: number): string[] {
  const room = Math.max(1, Math.floor(width))
  const lines: string[] = []
  let line = ''
  for (const word of oneLine(text).split(' ').filter(w => w !== '')) {
    let rest = word
    while (rest.length > room) {
      if (line !== '') {
        lines.push(line)
        line = ''
      }
      lines.push(rest.slice(0, room))
      rest = rest.slice(room)
    }
    if (rest === '') continue
    if (line === '') line = rest
    else if (line.length + 1 + rest.length <= room) line = `${line} ${rest}`
    else {
      lines.push(line)
      line = rest
    }
  }
  if (line !== '') lines.push(line)
  return lines
}

/** The queue as dots: `●` done and current, `○` ahead; past `cap` dots the rest is `+N`. */
export function queueDots(n: number, total: number, cap = 10): string {
  const all = Math.max(0, total)
  const shown = Math.min(all, cap)
  const filled = Math.max(0, Math.min(n, shown))
  return `${'●'.repeat(filled)}${'○'.repeat(shown - filled)}${all > cap ? `+${all - cap}` : ''}`
}

/** `review-gate:pass` → a success signal `review-gate pass`; fail critical, skip info, anything else pending (warning). */
export function gateSignal(gateState: string): { kind: 'success' | 'warning' | 'critical' | 'info'; word: string } | null {
  const [gate = '', state = ''] = gateState.split(':').map(part => part.trim())
  if (gate === '') return null
  const word = state === '' ? gate : `${gate} ${state}`
  if (state === 'pass' || state === 'passed') return { kind: 'success', word }
  if (state === 'fail' || state === 'failed') return { kind: 'critical', word }
  if (state === 'skip' || state === 'skipped') return { kind: 'info', word }
  return { kind: 'warning', word }
}

// Ticks are local UI state only: `$.store` `ticks:<task id>` = { at, items } (the ticked items' text, so an edited
// human_action never moves a tick to another item). Never written to Taskmaster.
export const TICKS_PREFIX = 'ticks:'
export const TICKS_MAX_AGE_MS = 30 * 24 * 3_600_000

export function ticksOf(stored: unknown): readonly string[] {
  if (typeof stored !== 'object' || stored === null) return []
  const items = (stored as { items?: unknown }).items
  return Array.isArray(items) ? items.filter((i): i is string => typeof i === 'string') : []
}

export function toggleTick(ticks: readonly string[], item: string): string[] {
  return ticks.includes(item) ? ticks.filter(t => t !== item) : [...ticks, item]
}

/** True for a ticks entry older than 30 days or unreadable: pruned at session start with the binding keys. */
export function isStaleTicks(stored: unknown, now: number): boolean {
  const at = typeof stored === 'object' && stored !== null ? (stored as { at?: unknown }).at : undefined
  return typeof at !== 'number' || now - at > TICKS_MAX_AGE_MS
}
