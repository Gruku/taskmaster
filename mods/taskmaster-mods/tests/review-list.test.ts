// User intent: pin the review list's rules (spec §8) — kinds and their order, pages and the focused card, j / k across pages,
// what a bulk action may touch and the one toast it ends with — without drawing anything.
import { describe, expect, test } from 'claude-code/testing'

import { FRESH_CURSOR } from '../hooks/model'
import {
  bulkSummary,
  doneQuestion,
  kindCounts,
  listView,
  neighbour,
  newestStamp,
  reviewItems,
  reviewKind,
  selectedTasks,
  sortReview,
  toggled,
} from '../hooks/review-list'
import type { TmQueueItem, TmSnapshot } from '../types'

const task = (id: string, priority: 'critical' | 'high' | 'medium' | 'low', timestamp = '', humanAction = 'check'): TmQueueItem => ({
  kind: 'task',
  id,
  title: id,
  priority,
  humanAction,
  timestamp,
})
const snap = (queue: readonly TmQueueItem[]): TmSnapshot => ({
  reachable: true,
  reason: '',
  fetchedAt: 0,
  queue,
  queueTotal: queue.length,
  handovers: [],
  handoversTotal: 0,
  bound: null,
})
const many = (n: number): TmQueueItem[] => Array.from({ length: n }, (_, i) => task(`t-${String(i + 1).padStart(3, '0')}`, 'high'))
const ids = (items: readonly TmQueueItem[]): string[] => items.map(i => i.id)

describe('review list', () => {
  test('kinds: a critical task or a P0 issue is BLOCKED; a human action, a P1 issue or a decision ACTION; else IN REVIEW', () => {
    expect(reviewKind(task('a', 'critical', '', ''))).toBe('blocked')
    expect(reviewKind(task('a', 'high'))).toBe('action')
    expect(reviewKind(task('a', 'low', '', '  '))).toBe('review')
    expect(reviewKind({ kind: 'issue', id: 'ISS-1', title: '', severity: 'P0', timestamp: '' })).toBe('blocked')
    expect(reviewKind({ kind: 'issue', id: 'ISS-2', title: '', severity: 'P1', timestamp: '' })).toBe('action')
    expect(reviewKind({ kind: 'decision', id: 'DEC-1', title: '', timestamp: '' })).toBe('action')
  })

  test('order: BLOCKED, ACTION, IN REVIEW; newest first within a kind; undated last; server order on a tie', () => {
    const items = [
      task('rev-old', 'low', '2026-10-01T10:00', ''),
      task('act-undated', 'high'),
      task('act-new', 'high', '2026-10-05T10:00'),
      task('blk', 'critical', '2026-09-01T10:00'),
      { kind: 'decision' as const, id: 'DEC-1', title: '', timestamp: '2026-10-03T10:00' },
      task('act-tie-1', 'low'),
    ]
    expect(ids(sortReview(items))).toEqual(['blk', 'act-new', 'DEC-1', 'act-undated', 'act-tie-1', 'rev-old'])
  })

  test('the listed cards leave out what this pass signed off and keep a pinned task, in review order', () => {
    const queue = [task('a-001', 'high', '2026-10-01T10:00'), task('b-001', 'critical')]
    expect(ids(reviewItems(snap(queue), { ...FRESH_CURSOR, done: ['a-001'] }, {}))).toEqual(['b-001'])
    expect(ids(reviewItems(snap(queue), FRESH_CURSOR, {}))).toEqual(['b-001', 'a-001'])
  })

  test('pages of three: the focused card picks the page; without one, the stored page (clamped) and its first card', () => {
    const items = many(12)
    expect(listView(items, '', 0)).toMatchObject({ page: 0, pages: 4, index: 0 })
    expect(ids(listView(items, '', 3).entries)).toEqual(['t-010', 't-011', 't-012'])
    expect(listView(items, '', 9)).toMatchObject({ page: 3, index: 9 })
    expect(listView(items, 't-007', 0)).toMatchObject({ page: 2, index: 6 })
    expect(listView(items, 'gone', 1).focused?.id).toBe('t-004')
    expect(listView([], '', 3)).toMatchObject({ page: 0, pages: 1, index: -1, focused: null })
  })

  test('j / k step across pages and stop at both ends', () => {
    const items = many(7)
    expect(neighbour(items, listView(items, 't-003', 0), 1)).toEqual({ id: 't-004', page: 1 })
    expect(neighbour(items, listView(items, 't-004', 0), -1)).toEqual({ id: 't-003', page: 0 })
    expect(neighbour(items, listView(items, '', 0), -1)).toBeNull()
    expect(neighbour(items, listView(items, 't-007', 0), 1)).toBeNull()
    expect(neighbour([], listView([], '', 0), 1)).toBeNull()
  })

  test('the kinds row counts and the newest dated card', () => {
    const items = [task('a', 'critical'), task('b', 'high', '2026-10-02T10:00'), task('c', 'high', 'junk'), task('d', 'low', '2026-10-04T10:00', '')]
    expect(kindCounts(items)).toEqual({ blocked: 1, action: 2, review: 1 })
    expect(newestStamp(items)).toBe('2026-10-04T10:00')
    expect(newestStamp([task('a', 'high')])).toBe('')
  })

  test('a bulk action touches only selected tasks still listed, in list order; select toggles', () => {
    const items = [task('a', 'high'), { kind: 'issue' as const, id: 'ISS-1', title: '', severity: 'P1', timestamp: '' }, task('b', 'high')]
    expect(ids(selectedTasks(items, ['b', 'gone', 'ISS-1', 'a']))).toEqual(['a', 'b'])
    expect(toggled(['a'], 'b')).toEqual(['a', 'b'])
    expect(toggled(['a', 'b'], 'a')).toEqual(['b'])
  })

  test('the done question counts what is unchecked, else names the task; it shortens to fit', () => {
    expect(doneQuestion(2, 3, 'tm-audit-031', 60)).toBe('2 of 3 unchecked — done anyway?')
    expect(doneQuestion(2, 3, 'tm-audit-031', 22)).toBe('2/3 unchecked — done?')
    expect(doneQuestion(0, 3, 'tm-audit-031', 60)).toBe('done tm-audit-031?')
    expect(doneQuestion(0, 0, 'tm-audit-031', 14)).toBe('tm-audit-031?')
    expect(doneQuestion(0, 0, 'tm-audit-031', 6)).toBe('tm-au…')
  })

  test('one toast for a bulk run names every refusal in the server words', () => {
    expect(bulkSummary('done', [{ id: 'a', refusal: null }, { id: 'b', refusal: null }])).toBe('2 done')
    expect(bulkSummary('done', [{ id: 'a', refusal: null }, { id: 'b', refusal: 'Cannot complete `b` — outstanding gates' }])).toBe(
      '1 done · 1 refused: b (Cannot complete `b` — outstanding gates)',
    )
    expect(bulkSummary('back to agent', [{ id: 'a', refusal: 'x' }, { id: 'b', refusal: 'y' }])).toBe('0 back to agent · 2 refused: a (x); b (y)')
    expect(bulkSummary('done', [{ id: 'a', refusal: null }, { id: 'b', refusal: 'boom' }, { id: 'c', refusal: 'stopped', notRun: true }])).toBe(
      '1 done · 1 refused: b (boom) · not run: c',
    )
  })
})
