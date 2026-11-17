// User intent: one windowing rule for the review, handovers and restore lists — show as many items as the pane's height allows,
// keep the focused one in view with the least movement, and say how many are hidden — so a list scrolls instead of paging.
// Pure row arithmetic: the panes draw what it picks (vocab.tsx `scrollListing`).

/** One item of a scrolling list, as the pane would draw it. */
export type ScrollEntry = {
  /** Rows the item takes on its own (a collapsed item's head and lines; the focused card's border, lines, extras and actions). */
  readonly rows: number
  readonly focused: boolean
  /** Items sharing a group sit in one listing (an item rule between collapsed neighbours); a new group starts after a gap row. */
  readonly group?: string
  /** Rows of the group's header, drawn above its first visible item (restore's `UNFINISHED  3`). */
  readonly header?: number
}

export type ScrollWindow = {
  /** The first item drawn and one past the last (`start === end` only for an empty list). */
  readonly start: number
  readonly end: number
  /** Items hidden above and below. */
  readonly above: number
  readonly below: number
  /** False when even the one forced item leaves no room for the `↑ / ↓ N more` lines: they are left out, the toolbar's position says it. */
  readonly more: boolean
  /** Rows the window takes, indicator lines included. */
  readonly used: number
}

/** The rows of the `↑ N more` / `↓ N more` lines. */
export const MORE_ROWS = 1
/** The blank rows between two groups. */
export const GROUP_GAP = 1

const groupOf = (e: ScrollEntry | undefined): string => e?.group ?? ''

/** Rows of items [start, end) drawn together: their own rows, group headers and gaps, the rule between collapsed neighbours. */
export function spanRows(entries: readonly ScrollEntry[], start: number, end: number): number {
  let rows = 0
  for (let i = start; i < end; i++) {
    const e = entries[i]!
    const prev = entries[i - 1]
    rows += e.rows
    const starts = i === start || groupOf(prev) !== groupOf(e)
    if (starts) rows += e.header ?? 0
    if (i > start) {
      if (groupOf(prev) !== groupOf(e)) rows += GROUP_GAP
      else if (prev !== undefined && !prev.focused && !e.focused) rows += 1
    }
  }
  return rows
}

const totalRows = (entries: readonly ScrollEntry[], start: number, end: number): number =>
  spanRows(entries, start, end) + (start > 0 ? MORE_ROWS : 0) + (end < entries.length ? MORE_ROWS : 0)

/** The last item that fits from `start` in `room` rows, at least one past it (a list always shows something). */
function fit(entries: readonly ScrollEntry[], start: number, room: number): number {
  let best = start + 1
  for (let end = start + 1; end <= entries.length; end++) if (totalRows(entries, start, end) <= room) best = end
  return best
}

/**
 * Which items to draw in `room` rows. `first` is the stored first-visible index and `focus` the focused item (-1: none): the
 * window keeps `first` unless the focused item lies outside it, then moves the least that brings it in (as `nearest` does), and
 * never leaves empty room under the last item while items hide above it.
 */
export function windowOf(a: { readonly entries: readonly ScrollEntry[]; readonly room: number; readonly first: number; readonly focus: number }): ScrollWindow {
  const { entries } = a
  const n = entries.length
  if (n === 0) return { start: 0, end: 0, above: 0, below: 0, used: 0, more: true }
  const room = Math.max(1, a.room)
  const focus = a.focus < 0 ? -1 : Math.min(a.focus, n - 1)
  const first = Number.isFinite(a.first) ? Math.floor(a.first) : 0
  let start = Math.min(Math.max(0, first), n - 1)
  if (focus >= 0 && focus < start) start = focus
  let end = fit(entries, start, room)
  while (focus >= end && start < focus) {
    start += 1
    end = fit(entries, start, room)
  }
  while (start > 0 && end >= n && fit(entries, start - 1, room) >= n) start -= 1
  end = fit(entries, start, room)
  const used = totalRows(entries, start, end)
  if (used > room && end - start === 1) return { start, end, above: start, below: n - end, used: spanRows(entries, start, end), more: false }
  return { start, end, above: start, below: n - end, used, more: true }
}

/** The first index that shows the list's end: the furthest the window scrolls (a wheel never goes past it). */
export function lastFirst(entries: readonly ScrollEntry[], room: number): number {
  return windowOf({ entries, room, first: entries.length - 1, focus: -1 }).start
}

/**
 * The wheel: `by` items down (negative up) from the window's first, held to [0, lastFirst]. The focus moves only when it left
 * the window, to the nearest item still showing; `focus` -1 (nothing focused) stays.
 */
export function scrolled(a: { readonly entries: readonly ScrollEntry[]; readonly room: number; readonly first: number; readonly focus: number; readonly by: number }): { readonly first: number; readonly focus: number } {
  const n = a.entries.length
  if (n === 0) return { first: 0, focus: a.focus }
  const now = windowOf(a)
  const first = Math.min(Math.max(0, now.start + Math.trunc(a.by)), lastFirst(a.entries, a.room))
  const next = windowOf({ entries: a.entries, room: a.room, first, focus: -1 })
  if (a.focus < 0) return { first: next.start, focus: a.focus }
  const focus = Math.min(Math.max(a.focus, next.start), next.end - 1)
  return { first: next.start, focus }
}

/**
 * p / n: one window's worth of items (at least one) up or down. The window scrolls by as much as the focus moves, so the focus
 * keeps its place on the screen; both stop at the list's ends.
 */
export function pagedBy(a: { readonly entries: readonly ScrollEntry[]; readonly room: number; readonly first: number; readonly focus: number; readonly dir: -1 | 1 }): { readonly first: number; readonly focus: number } {
  const n = a.entries.length
  if (n === 0) return { first: 0, focus: a.focus }
  const now = windowOf(a)
  const size = Math.max(1, now.end - now.start)
  const focus = Math.min(Math.max(0, (a.focus < 0 ? now.start : a.focus) + a.dir * size), n - 1)
  const first = Math.min(Math.max(0, now.start + a.dir * size), lastFirst(a.entries, a.room))
  return { first: windowOf({ entries: a.entries, room: a.room, first, focus }).start, focus }
}

/** The toolbar's place in the list: `4–9 of 23` (`5 of 23` for one item showing; empty for an empty list). */
export function positionLabel(w: ScrollWindow, total: number): string {
  if (total === 0 || w.end <= w.start) return ''
  const from = w.start + 1
  return w.end - w.start === 1 ? `${from} of ${total}` : `${from}–${w.end} of ${total}`
}

/** What a pane last drew, kept so the wheel (a hook, not a render) scrolls the list the person sees. */
export type ScrollSpec = {
  readonly entries: readonly ScrollEntry[]
  /** The item ids in list order; '' for an item that cannot take the focus. */
  readonly ids: readonly string[]
  readonly room: number
  readonly first: number
  /** The focused item's index (-1: none). */
  readonly focus: number
}
const drawn = new Map<string, ScrollSpec>()
export const rememberScroll = (pane: string, spec: ScrollSpec): void => void drawn.set(pane, spec)

/**
 * The wheel over `pane`'s list: the window `by` items on (negative: up), the focus moved only when it scrolled out, onto the
 * nearest item that can take it. Null when the pane has drawn no list or nothing would change.
 */
export function wheelMove(pane: string, by: number): { readonly first: number; readonly id: string | null } | null {
  const spec = drawn.get(pane)
  if (spec === undefined || spec.entries.length === 0) return null
  const moved = scrolled({ ...spec, by })
  const keep = spec.ids[spec.focus]
  const near = (from: number): string | null => {
    const win = windowOf({ entries: spec.entries, room: spec.room, first: moved.first, focus: -1 })
    for (let d = 0; d < spec.entries.length; d++) {
      for (const i of [from + d, from - d]) {
        if (i >= win.start && i < win.end && (spec.ids[i] ?? '') !== '') return spec.ids[i]!
      }
    }
    return keep === undefined || keep === '' ? null : keep
  }
  const id = moved.focus === spec.focus ? null : near(moved.focus)
  if (moved.first === spec.first && (id === null || id === keep)) return null
  return { first: moved.first, id: id === keep ? null : id }
}
