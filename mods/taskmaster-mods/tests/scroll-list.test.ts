// User intent: pin the one windowing rule the review, handovers and restore lists share — what is drawn in the rows left, how the
// focus keeps in view, the wheel and p / n steps, and the position label — so a list scrolls the same way in every pane.
import { describe, expect, test } from 'claude-code/testing'

import { lastFirst, pagedBy, positionLabel, rememberScroll, scrolled, spanRows, type ScrollEntry, windowOf, wheelMove } from '../hooks/scroll-list'

const items = (...rows: number[]): ScrollEntry[] => rows.map(r => ({ rows: r, focused: false }))
const win = (entries: ScrollEntry[], room: number, first: number, focus: number) => windowOf({ entries, room, first, focus })

describe('windowOf', () => {
  test('everything fits: all items, no indicators; an empty list is an empty window', () => {
    const w = win(items(2, 2, 2), 20, 0, 0)
    expect(w).toMatchObject({ start: 0, end: 3, above: 0, below: 0 })
    expect(win([], 10, 0, -1)).toEqual({ start: 0, end: 0, above: 0, below: 0, used: 0, more: true })
  })

  test('a rule row sits between collapsed neighbours; the indicator lines count in the budget', () => {
    // 2 + rule + 2 + rule + 2 = 8 rows for three; a 7-row room holds two and the ↓ line.
    expect(spanRows(items(2, 2, 2), 0, 3)).toBe(8)
    const w = win(items(2, 2, 2), 7, 0, 0)
    expect(w).toMatchObject({ start: 0, end: 2, below: 1 })
    expect(w.used).toBeLessThanOrEqual(7)
  })

  test('variable heights: a tall item takes more of the room', () => {
    const w = win(items(1, 8, 1, 1), 12, 0, 0)
    expect(w.end).toBeLessThan(4)
    expect(w.used).toBeLessThanOrEqual(12)
  })

  test('a tiny room still shows one item, however tall', () => {
    expect(win(items(9, 9), 2, 0, 0)).toMatchObject({ start: 0, end: 1 })
    expect(win(items(9, 9), 0, 1, 1)).toMatchObject({ start: 1, end: 2 })
  })

  test('the focus below the window scrolls it the least that shows it; above, it scrolls back; inside, it stays', () => {
    const list = items(2, 2, 2, 2, 2, 2)
    const top = win(list, 9, 0, 0)
    expect(win(list, 9, 0, top.end - 1).start).toBe(0)
    expect(win(list, 9, 0, top.end).start).toBe(2) // one item more would not fit with both indicator lines
    expect(win(list, 9, 4, 1).start).toBe(1)
    expect(win(list, 9, 2, 3)).toMatchObject({ start: 2 })
  })

  test('the focus at the last item shows the end with no empty room left under it', () => {
    const list = items(2, 2, 2, 2, 2, 2)
    const w = win(list, 9, 0, 5)
    expect(w.end).toBe(6)
    expect(w.below).toBe(0)
    expect(win(list, 9, 5, 5).end).toBe(6) // a stored first past the useful end is pulled back to fill the room
    expect(win(list, 9, 5, 5).start).toBeLessThan(5)
  })

  test('a focused card is taller and has no rule against it; a non-finite stored first is the top', () => {
    const list: ScrollEntry[] = [{ rows: 6, focused: true }, { rows: 2, focused: false }, { rows: 2, focused: false }]
    expect(spanRows(list, 0, 3)).toBe(6 + 2 + 1 + 2)
    expect(win(list, 50, Number.NaN, 0).start).toBe(0)
  })

  test('groups: a header over the first visible item of each group, a gap between groups', () => {
    const list: ScrollEntry[] = [
      { rows: 2, focused: false, group: 'a', header: 1 },
      { rows: 2, focused: false, group: 'a', header: 1 },
      { rows: 2, focused: false, group: 'b', header: 1 },
    ]
    expect(spanRows(list, 0, 3)).toBe(1 + 2 + 1 + 2 + 1 + 1 + 2) // header, item, rule, item, gap, header, item
    expect(spanRows(list, 1, 3)).toBe(1 + 2 + 1 + 1 + 2) // group a's header shows above its first visible item
  })
})

describe('lastFirst, scrolled, pagedBy', () => {
  const list = items(2, 2, 2, 2, 2, 2)
  test('the wheel moves the window an item a tick, held to the list; the focus follows only when it leaves the window', () => {
    expect(scrolled({ entries: list, room: 9, first: 0, focus: 0, by: 1 })).toEqual({ first: 1, focus: 1 })
    expect(scrolled({ entries: list, room: 9, first: 0, focus: 2, by: 1 })).toEqual({ first: 1, focus: 2 })
    expect(scrolled({ entries: list, room: 9, first: 0, focus: 0, by: -3 }).first).toBe(0)
    expect(scrolled({ entries: list, room: 9, first: 0, focus: 0, by: 50 }).first).toBe(lastFirst(list, 9))
    expect(scrolled({ entries: list, room: 9, first: 0, focus: -1, by: 2 })).toEqual({ first: 2, focus: -1 })
  })

  test('p and n move the focus a window of items and stop at the ends', () => {
    const size = win(list, 9, 0, 0).end
    expect(pagedBy({ entries: list, room: 9, first: 0, focus: 0, dir: 1 }).focus).toBe(size)
    expect(pagedBy({ entries: list, room: 9, first: 0, focus: 5, dir: 1 }).focus).toBe(5)
    expect(pagedBy({ entries: list, room: 9, first: 0, focus: 0, dir: -1 })).toEqual({ first: 0, focus: 0 })
    const down = pagedBy({ entries: list, room: 9, first: 0, focus: 0, dir: 1 })
    expect(win(list, 9, down.first, down.focus).start).toBeLessThanOrEqual(down.focus)
  })
})

describe('positionLabel', () => {
  test('4–9 of 23; one item shows as 5 of 23; nothing shows nothing', () => {
    expect(positionLabel({ start: 3, end: 9, above: 3, below: 14, used: 10, more: true }, 23)).toBe('4–9 of 23')
    expect(positionLabel({ start: 4, end: 5, above: 4, below: 18, used: 5, more: true }, 23)).toBe('5 of 23')
    expect(positionLabel({ start: 0, end: 0, above: 0, below: 0, used: 0, more: true }, 0)).toBe('')
  })
})

describe('wheelMove', () => {
  const list = items(2, 2, 2, 2, 2, 2)
  const ids = ['a', 'b', 'c', 'd', 'e', 'f']
  test('reads what the pane last drew: the window moves, the focus only when it scrolled out, onto an item that takes focus', () => {
    rememberScroll('t-wheel', { entries: list, ids, room: 9, first: 0, focus: 0 })
    expect(wheelMove('t-wheel', 1)).toEqual({ first: 1, id: 'b' })
    rememberScroll('t-wheel', { entries: list, ids, room: 9, first: 0, focus: 2 })
    expect(wheelMove('t-wheel', 1)).toEqual({ first: 1, id: null })
    expect(wheelMove('t-nothing-drawn', 1)).toBeNull()
  })

  test('at the top or the end it is a no-op, not a write', () => {
    rememberScroll('t-wheel-top', { entries: list, ids, room: 9, first: 0, focus: 0 })
    expect(wheelMove('t-wheel-top', -1)).toBeNull()
    rememberScroll('t-wheel-end', { entries: list, ids, room: 9, first: lastFirst(list, 9), focus: 5 })
    expect(wheelMove('t-wheel-end', 3)).toBeNull()
  })

  test('a row that takes no focus is skipped for the nearest one that does', () => {
    rememberScroll('t-wheel-skip', { entries: list, ids: ['a', '', 'c', 'd', 'e', 'f'], room: 9, first: 0, focus: 0 })
    expect(wheelMove('t-wheel-skip', 1)?.id).toBe('c')
  })
})

describe('a room too small for the forced item', () => {
  test('the one item shows without its ↑ / ↓ lines so the window takes no more than it must', () => {
    const w = windowOf({ entries: items(6, 6, 6), room: 7, first: 1, focus: 1 })
    expect(w).toMatchObject({ start: 1, end: 2, more: false, used: 6 })
  })
})
