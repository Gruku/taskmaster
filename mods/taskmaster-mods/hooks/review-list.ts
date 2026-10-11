// User intent: the review pane as a paged card list (spec §8) — which cards, in what order (BLOCKED, ACTION, IN REVIEW, newest
// first), which page and card hold the focus, what a bulk action may touch and how its outcome is told — as plain functions
// tested without drawing.
import type { TmCursor, TmQueueItem, TmReviewList, TmSnapshot, TmTaskDetail } from '../types'
import { PAGE_SIZE, reviewQueue, truncate } from './model'

export type ReviewKind = 'blocked' | 'action' | 'review'
export const KIND_WORD: Readonly<Record<ReviewKind, string>> = { blocked: 'blocked', action: 'action', review: 'in review' }
export const KIND_TONE: Readonly<Record<ReviewKind, 'critical' | 'warning' | 'info'>> = { blocked: 'critical', action: 'warning', review: 'info' }
export const KINDS: readonly ReviewKind[] = ['blocked', 'action', 'review']
export const FRESH_REVIEW_LIST: TmReviewList = { page: 0, selected: [], ticking: false, bulk: 'none', bulkIds: [] }

/**
 * A card's kind (spec §8): BLOCKED for what holds a release (a critical task, a P0 issue); ACTION for anything else that asks
 * the person (a task naming a human action, a P1 issue, an open decision); IN REVIEW for a task in review that names none.
 */
export function reviewKind(item: TmQueueItem): ReviewKind {
  if (item.kind === 'task') return item.priority === 'critical' ? 'blocked' : item.humanAction.trim() !== '' ? 'action' : 'review'
  if (item.kind === 'issue') return item.severity === 'P0' ? 'blocked' : 'action'
  return 'action'
}

const RANK: Readonly<Record<ReviewKind, number>> = { blocked: 0, action: 1, review: 2 }
// An unreadable or missing timestamp sorts after every dated card of its kind.
const stampOf = (stamp: string): number => {
  const ms = Date.parse(stamp)
  return Number.isNaN(ms) ? Number.NEGATIVE_INFINITY : ms
}

/** Review order: BLOCKED, ACTION, IN REVIEW; newest first within a kind; the server's order where that ties. */
export function sortReview(items: readonly TmQueueItem[]): TmQueueItem[] {
  return items
    .map((item, index) => ({ item, index, rank: RANK[reviewKind(item)], at: stampOf(item.timestamp) }))
    .sort((a, b) => a.rank - b.rank || (a.at === b.at ? 0 : b.at - a.at) || a.index - b.index)
    .map(entry => entry.item)
}

/** The cards the pane lists: the loaded window plus a pinned task (reviewQueue), in review order, less what this pass signed off. */
export function reviewItems(s: TmSnapshot, c: TmCursor, details: Readonly<Record<string, TmTaskDetail>>): TmQueueItem[] {
  return sortReview(reviewQueue(s, c, details).filter(item => !c.done.includes(item.id)))
}

/** One page of the list and its focused card: `index` is the focused card's place in the whole list (-1: none). */
export type ListView = {
  readonly entries: readonly TmQueueItem[]
  readonly page: number
  readonly pages: number
  readonly focused: TmQueueItem | null
  readonly index: number
}

/** The focused card's page when it is listed; else the stored page (clamped) and its first card. */
export function listView(items: readonly TmQueueItem[], currentId: string, page: number): ListView {
  const pages = Math.max(1, Math.ceil(items.length / PAGE_SIZE))
  const at = currentId === '' ? -1 : items.findIndex(item => item.id === currentId)
  const shown = at >= 0 ? Math.floor(at / PAGE_SIZE) : Math.min(Math.max(0, page), pages - 1)
  const entries = items.slice(shown * PAGE_SIZE, (shown + 1) * PAGE_SIZE)
  const index = at >= 0 ? at : entries.length > 0 ? shown * PAGE_SIZE : -1
  return { entries, page: shown, pages, focused: items[index] ?? null, index }
}

/** `j` / `k`: the card `dir` away from the focused one, across pages, with its page; null at either end. */
export function neighbour(items: readonly TmQueueItem[], view: ListView, dir: -1 | 1): { readonly id: string; readonly page: number } | null {
  if (view.index < 0) return null
  const next = items[view.index + dir]
  return next === undefined ? null : { id: next.id, page: Math.floor((view.index + dir) / PAGE_SIZE) }
}

/** How many cards of each kind the list holds: the `2 BLOCKED  1 ACTION  9 IN REVIEW` row. */
export function kindCounts(items: readonly TmQueueItem[]): Record<ReviewKind, number> {
  const counts: Record<ReviewKind, number> = { blocked: 0, action: 0, review: 0 }
  for (const item of items) counts[reviewKind(item)] += 1
  return counts
}

/** The newest dated card's timestamp ('' when none is dated): the `newest 1d ago` of the summary line. */
export function newestStamp(items: readonly TmQueueItem[]): string {
  let best = ''
  for (const item of items) if (stampOf(item.timestamp) > stampOf(best)) best = item.timestamp
  return best
}

/** Only tasks are signed off or sent back from the list: issues and decisions take no selection. */
export const isSelectable = (item: TmQueueItem): boolean => item.kind === 'task'

/** The selected tasks still listed, in list order: what a bulk action may touch (a refresh can take some away). */
export function selectedTasks(items: readonly TmQueueItem[], selected: readonly string[]): TmQueueItem[] {
  return items.filter(item => isSelectable(item) && selected.includes(item.id))
}

export const toggled = (selected: readonly string[], id: string): string[] =>
  selected.includes(id) ? selected.filter(s => s !== id) : [...selected, id]

/** The pane's `done` question in `room` cells: the unchecked count while any is open, else the task by name. */
export function doneQuestion(open: number, total: number, id: string, room: number): string {
  const asks = open > 0 ? [`${open} of ${total} unchecked — done anyway?`, `${open}/${total} unchecked — done?`, `${open}/${total} unchecked?`] : [`done ${id}?`, `${id}?`]
  return asks.find(ask => ask.length <= room) ?? truncate(asks[asks.length - 1] ?? '', room)
}

/** One task's outcome in a bulk run: null when it went through, else the server's refusal as it worded it. */
export type BulkResult = { readonly id: string; readonly refusal: string | null }

/** One toast for a bulk run (spec §11): `3 done · 1 refused: tm-audit-031 (<server wording>)`. */
export function bulkSummary(verb: string, results: readonly BulkResult[]): string {
  const went = results.filter(r => r.refusal === null).length
  const refused = results.filter(r => r.refusal !== null)
  const head = `${went} ${verb}`
  return refused.length === 0 ? head : `${head} · ${refused.length} refused: ${refused.map(r => `${r.id} (${r.refusal})`).join('; ')}`
}
