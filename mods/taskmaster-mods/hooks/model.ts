// User intent: the Taskmaster semantics behind every taskmaster-mods surface — what waits on the user, queue order, card and
// band wording, Telegram-ready handover text — as plain functions over snapshot data, tested without drawing.
import type {
  TmCursor,
  TmHandover,
  TmHandoverNotice,
  TmPhase,
  TmPipeline,
  TmPriority,
  TmQueueItem,
  TmSessionHandover,
  TmSnapshot,
  TmTaskDetail,
} from '../types'

export const REVIEW = 'tm-review'
export const HANDOVERS = 'tm-handovers'
export const SESSION = 'tm-session'
export const RESTORE = 'tm-restore'
/** `$.store` key of a restore burst the band's `x` hid (spec §10.4): hidden for that burst in every session. */
export const restoreHiddenKey = (epoch: number): string => `restore-hidden:${epoch}`
export const FRESH_CURSOR: TmCursor = { currentId: '', done: [], mode: 'card', refusal: '' }

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

// The queue the review pane walks: the loaded window, plus the pinned task (band `a`, a card a confirm or note was asked
// on) when the window does not hold it, built from that task's own detail by id — never a stand-in from the window.
export function reviewQueue(s: TmSnapshot, c: TmCursor, details: Readonly<Record<string, TmTaskDetail>>): readonly TmQueueItem[] {
  const id = c.currentId
  if (id === '' || s.queue.some(i => i.id === id) || c.done.includes(id)) return s.queue
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

/** Status words as the surfaces show them: `in-review` → `IN REVIEW`. */
export const statusWord = (status: string): string => status.toUpperCase().replace(/-/g, ' ')

/** A status badge's tone: in review is information, blocked is critical, done is success, anything else the signature. */
export function statusTone(status: string): 'success' | 'critical' | 'info' | 'signature' {
  if (status === 'in-review') return 'info'
  if (status === 'blocked') return 'critical'
  if (status === 'done' || status === 'archived') return 'success'
  return 'signature'
}

/** Ready for done: not done yet, and in review, or with a pipeline that has no gate outstanding. */
export function isReady(d: TmTaskDetail, p: TmPipeline | null): boolean {
  if (d.status === 'done' || d.status === 'archived') return false
  return d.status === 'in-review' || (p !== null && !p.laneless && p.outstanding.length === 0)
}

export type BandTask = {
  readonly id: string
  readonly title: string
  /** '' while Taskmaster's detail is not known (offline, or not read yet): no status badge. */
  readonly status: string
  readonly inferred: boolean
  /** `d done` shows: bound by this session (never inferred), live, and ready for done. */
  readonly ready: boolean
}
/** The neutral note while a transient failure is retried and there is no good snapshot to show. */
export const CONNECTING = 'Connecting to Taskmaster…'

/**
 * What the band draws (spec §6). `waitingCapped`: the count was cut at the window with no server total (drawn `N+`);
 * `restore`: the pending burst's unfinished sessions (null: no `4`); `fault`: offline fades the counts, connecting says so.
 */
export type BandModel = {
  readonly task: BandTask | null
  readonly handover: TmSessionHandover | null
  readonly waiting: number
  readonly waitingCapped: boolean
  readonly handovers: number
  readonly restore: number | null
  readonly fault: 'none' | 'offline' | 'connecting'
}

/**
 * The band's model; null (no band) when there is nothing to show: no task, no handover, no restore burst and, offline or
 * not, nothing counted. While a transient failure is retried with no good snapshot, the band says it is connecting.
 */
export function bandModel(
  s: TmSnapshot | null,
  a: { readonly handover: TmSessionHandover | null; readonly restore: number | null; readonly connecting: boolean },
): BandModel | null {
  const quiet = { waiting: 0, waitingCapped: false, handovers: 0, handover: a.handover, restore: a.restore }
  if (a.connecting && (s === null || !s.reachable)) return { task: null, fault: 'connecting', ...quiet }
  if (s === null) return a.handover === null && a.restore === null ? null : { task: null, fault: 'none', ...quiet }
  const b = s.bound
  const detail = b?.detail ?? null
  const task: BandTask | null =
    b === null
      ? null
      : {
          id: b.taskId,
          title: detail?.title ?? '',
          status: s.reachable ? (detail?.status ?? '') : '',
          inferred: b.inferred,
          ready: s.reachable && !b.inferred && detail !== null && isReady(detail, b.pipeline),
        }
  const m: BandModel = s.reachable
    ? { ...quiet, task, fault: 'none', waiting: s.queueTotal, waitingCapped: s.queueTotal > 0 && s.queueCapped === true, handovers: s.handoversTotal }
    : { ...quiet, task, fault: 'offline' }
  if (task === null && a.handover === null && a.restore === null && m.waiting === 0 && m.handovers === 0) return null
  return m
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

/**
 * The one Telegram-ready handover block (decided 2026-10-06), its last line worded as the server's own receipt words it:
 * `<tldr>` / `<absolute path>` / `Resume: <thread> — <next action, else the tldr>`; an empty thread is left out.
 */
export function copyBlock(tldr: string, path: string, thread: string, nextAction: string): string {
  const resume = [thread.trim(), nextAction.trim() || tldr.trim()].filter(Boolean).join(' — ')
  return [tldr.trim(), path, `Resume: ${resume}`].join('\n')
}

/** A listed handover's block; a snapshot older than the `thread` field reads it as empty, never throws. */
export function handoverCopyText(h: TmHandover): string {
  return copyBlock(h.tldr, h.path, typeof h.thread === 'string' ? h.thread : '', h.nextAction)
}

/**
 * The band notice for a handover this session just wrote: id and path from the receipt (the path from `root` when the
 * receipt has none), tldr from the call's input; the block's Resume line is the receipt's own (the server derives the
 * thread when the input names none), else built from the input.
 */
export function handoverNotice(
  input: Readonly<Record<string, unknown>>,
  receipt: { readonly id: string; readonly path: string; readonly resume: string },
  root: string,
): TmHandoverNotice {
  const field = (key: string): string => {
    const v = input[key]
    return typeof v === 'string' ? v.trim() : ''
  }
  const tldr = field('tldr')
  const path = receipt.path || handoverPath(root, receipt.id)
  const text = receipt.resume === '' ? copyBlock(tldr, path, field('thread'), field('next_action')) : [tldr, path, receipt.resume].join('\n')
  return { id: receipt.id, tldr, path, text }
}

/**
 * The handover card's dim refs line, `<branch> · <task>, <task>` (the user-approved card, 2026-10-06), leaving out whichever
 * is empty ('' when both are). Either may be missing at run time (a reply without them, a snapshot older than the fields):
 * missing reads as empty, never throws.
 */
export function handoverRefs(h: TmHandover): string {
  const branch = typeof h.branch === 'string' ? h.branch : ''
  const tasks = Array.isArray(h.taskIds) ? h.taskIds.filter(id => typeof id === 'string' && id !== '') : []
  return [branch, tasks.join(', ')].filter(Boolean).join(' · ')
}

const fromHandover = (h: TmHandover, tag: TmSessionHandover['tag']): TmSessionHandover => ({
  id: h.id,
  tag,
  tldr: h.tldr,
  path: h.path,
  created: h.created,
  ...(h.nextAction === '' ? {} : { next: h.nextAction }),
})

/**
 * D5: the handover this session shows — the one it wrote (the notice), else the one it was resumed from (from the open list,
 * or `resumed` read by id), else the newest open handover linked to its bound task; null when none applies.
 */
export function sessionHandoverOf(a: {
  readonly notice: TmHandoverNotice | null
  readonly resumedId: string
  readonly resumed: TmHandover | null
  readonly snapshot: TmSnapshot | null
}): TmSessionHandover | null {
  const listed = (id: string): TmHandover | undefined => a.snapshot?.handovers.find(h => h.id === id)
  if (a.notice !== null) {
    const h = listed(a.notice.id)
    return { id: a.notice.id, tag: 'written here', tldr: a.notice.tldr, path: a.notice.path, created: h?.created ?? '', ...(h && h.nextAction !== '' ? { next: h.nextAction } : {}) }
  }
  if (a.resumedId !== '') {
    const h = listed(a.resumedId) ?? (a.resumed?.id === a.resumedId ? a.resumed : undefined)
    if (h !== undefined) return fromHandover(h, 'resumed from')
  }
  const taskId = a.snapshot?.bound?.taskId
  const linked = taskId === undefined ? undefined : a.snapshot?.handovers.find(h => h.taskIds.includes(taskId))
  return linked === undefined ? null : fromHandover(linked, 'task')
}

/** What a surface draws: the resolved handover, unless a handover notice arrived since (the guard writes one directly). */
export function withNotice(resolved: TmSessionHandover | null, notice: TmHandoverNotice | null, snapshot: TmSnapshot | null): TmSessionHandover | null {
  if (notice === null || (resolved?.id === notice.id && resolved.tag === 'written here')) return resolved
  return sessionHandoverOf({ notice, resumedId: '', resumed: null, snapshot })
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

export const PAGE_SIZE = 5
/** The rework's lists (spec §15.6): three items a page, review, handovers and restore alike. */
export const LIST_PAGE_SIZE = 3

const decodeAmp = (text: string): string => text.replace(/&amp;/g, '&')

/** A phase's short name: what its full name says before ` — `, `&amp;` decoded; its id when it has no name. */
export function phaseName(p: TmPhase): string {
  return decodeAmp(p.name.split(' — ')[0] ?? '').trim() || p.id
}


export type HandoverPage = {
  readonly entries: readonly TmHandover[]
  readonly page: number
  readonly pages: number
  readonly picked: TmHandover | undefined
}

/** One page of the handovers list (LIST_PAGE_SIZE a page, out-of-range pages clamped) and the handover its card shows. */
export function handoverPage(list: readonly TmHandover[], page: number, pick: string): HandoverPage {
  const pages = Math.max(1, Math.ceil(list.length / LIST_PAGE_SIZE))
  const at = Math.min(Math.max(0, page), pages - 1)
  const entries = list.slice(at * LIST_PAGE_SIZE, (at + 1) * LIST_PAGE_SIZE)
  return { entries, page: at, pages, picked: entries.find(h => h.id === pick) ?? entries[0] }
}
