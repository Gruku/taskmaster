// User intent: pin the Taskmaster semantics the surfaces rely on — queue order, card position, what the band shows, ages,
// truncation, Telegram copy text — without drawing anything.
import { describe, expect, test } from 'claude-code/testing'

import {
  ageLabel,
  bandModel,
  bandRows,
  cardMode,
  cardPosition,
  FRESH_CURSOR,
  handoverCopyText,
  handoverPath,
  oneLine,
  orderQueue,
  reviewQueue,
  stageLine,
  truncate,
} from '../hooks/model'
import type { TmQueueItem, TmSnapshot, TmTaskDetail } from '../types'

const task = (id: string, priority: 'critical' | 'high' | 'medium' | 'low'): TmQueueItem => ({
  kind: 'task',
  id,
  title: id,
  priority,
  humanAction: 'check',
  timestamp: '',
})
const snap = (over: Partial<TmSnapshot>): TmSnapshot => ({
  reachable: true,
  reason: '',
  fetchedAt: 0,
  queue: [],
  queueTotal: 0,
  handovers: [],
  handoversTotal: 0,
  bound: null,
  ...over,
})
const DETAIL: TmTaskDetail = {
  id: 'tm-audit-030',
  title: 'Audit fixes',
  status: 'in-progress',
  priority: 'high',
  lane: 'full',
  gateState: 'review-gate:pass',
  branch: 'feat/x',
  humanAction: '',
}
const PIPELINE = { laneless: false, lane: 'full', gateState: 'review-gate:pass', outstanding: ['merge'] }

describe('queue', () => {
  test('tasks by priority (server order within one), then P0 before P1 issues, then decisions, oldest first', () => {
    const items: TmQueueItem[] = [
      { kind: 'decision', id: 'DEC-2', title: 'later', timestamp: '2026-10-03T10:00' },
      task('low-001', 'low'),
      { kind: 'issue', id: 'ISS-1', title: 'p1', severity: 'P1', timestamp: '2026-10-01T10:00' },
      task('crit-001', 'critical'),
      { kind: 'decision', id: 'DEC-1', title: 'earlier', timestamp: '2026-10-02T10:00' },
      task('high-001', 'high'),
      { kind: 'issue', id: 'ISS-0', title: 'p0', severity: 'P0', timestamp: '2026-10-04T10:00' },
      task('crit-002', 'critical'),
    ]
    expect(orderQueue(items).map(i => i.id)).toEqual(['crit-001', 'crit-002', 'high-001', 'low-001', 'ISS-0', 'ISS-1', 'DEC-1', 'DEC-2'])
  })

  test('the card skips done and skipped items, honours a pinned id and counts the pass', () => {
    const queue = [task('a-001', 'high'), task('b-001', 'high'), task('c-001', 'low')]
    expect(cardPosition(queue, FRESH_CURSOR, 3)).toMatchObject({ n: 1, total: 3 })
    expect(cardPosition(queue, FRESH_CURSOR, 3).item?.id).toBe('a-001')
    const later = { ...FRESH_CURSOR, done: ['a-001'], skipped: ['b-001'] }
    expect(cardPosition(queue, later, 2).item?.id).toBe('c-001')
    expect(cardPosition(queue, later, 2)).toMatchObject({ n: 3, total: 3 })
    expect(cardPosition(queue, { ...FRESH_CURSOR, currentId: 'c-001' }, 3).item?.id).toBe('c-001')
    expect(cardPosition(queue, { ...FRESH_CURSOR, skipped: ['a-001', 'b-001', 'c-001'] }, 3).item).toBeNull()
  })

  test('a window of the first rows still counts everything the server says is waiting', () => {
    const queue = [task('a-001', 'high'), task('b-001', 'high')]
    expect(cardPosition(queue, FRESH_CURSOR, 355)).toMatchObject({ n: 1, total: 355 })
  })

  test('a pinned task outside the loaded window gets its card from its own detail, never another card', () => {
    const queue = [task('a-001', 'high'), task('b-001', 'high')]
    const pinned = { ...FRESH_CURSOR, currentId: 'tm-audit-030', mode: 'note' as const }
    const known = reviewQueue(snap({ queue, queueTotal: 355 }), pinned, { 'tm-audit-030': { ...DETAIL, status: 'in-review', humanAction: 'Check dev' } })
    expect(cardPosition(known, pinned, 355).item).toMatchObject({ kind: 'task', id: 'tm-audit-030', priority: 'high', humanAction: 'Check dev' })
    const bound = snap({ queue, queueTotal: 355, bound: { taskId: 'tm-audit-030', inferred: false, detail: DETAIL, pipeline: null } })
    expect(cardPosition(reviewQueue(bound, pinned, {}), pinned, 355).item?.id).toBe('tm-audit-030')
    expect(reviewQueue(snap({ queue }), FRESH_CURSOR, {})).toEqual(queue)
  })

  test('confirm and note only ever apply to the card they were asked on', () => {
    const shown = task('a-001', 'high')
    expect(cardMode({ ...FRESH_CURSOR, currentId: 'a-001', mode: 'confirm' }, shown)).toBe('confirm')
    expect(cardMode({ ...FRESH_CURSOR, currentId: 'gone-001', mode: 'confirm' }, shown)).toBe('card')
    expect(cardMode({ ...FRESH_CURSOR, currentId: 'gone-001', mode: 'note' }, shown)).toBe('card')
    expect(cardMode({ ...FRESH_CURSOR, currentId: '', mode: 'note' }, shown)).toBe('card')
  })
})

describe('band', () => {
  test('hidden entirely with no bound task and nothing waiting', () => {
    expect(bandModel(null)).toBeNull()
    expect(bandModel(snap({}))).toBeNull()
  })

  test('a bound in-progress task shows lane, gate state and the next outstanding gate', () => {
    const m = bandModel(snap({ bound: { taskId: 'tm-audit-030', inferred: false, detail: DETAIL, pipeline: PIPELINE } }))
    expect(m?.task).toMatchObject({ id: 'tm-audit-030', review: false, meta: 'FULL · review-gate:pass', stage: 'IN PROGRESS → next: merge' })
    expect(m?.needsYou).toBe(0)
  })

  test('an in-review bound task switches to the review form carrying its human action', () => {
    const detail = { ...DETAIL, status: 'in-review', humanAction: 'Check dev' }
    const m = bandModel(snap({ bound: { taskId: 'tm-audit-030', inferred: false, detail, pipeline: PIPELINE } }))
    expect(m?.task).toMatchObject({ review: true, humanAction: 'Check dev' })
  })

  test('offline keeps the locally bound id but drops stage and needs-you', () => {
    const m = bandModel(
      snap({ reachable: false, reason: 'down', queue: [task('a-001', 'high')], bound: { taskId: 'tm-audit-030', inferred: false, detail: null, pipeline: null } }),
    )
    expect(m).toEqual({
      task: { id: 'tm-audit-030', title: '', inferred: false, review: false, humanAction: '', meta: '', stage: '' },
      needsYou: 0,
    })
  })

  test('rows give way to maxRows: the task first, then needs-you, then the stage line', () => {
    const m = { task: { id: 'x-001', title: 't', inferred: false, review: false, humanAction: '', meta: '', stage: 'IN PROGRESS' }, needsYou: 3 }
    expect(bandRows(m, 3)).toEqual(['task', 'stage', 'needs'])
    expect(bandRows(m, 2)).toEqual(['task', 'needs'])
    expect(bandRows(m, 1)).toEqual(['task'])
    expect(bandRows({ task: null, needsYou: 3 }, 1)).toEqual(['needs'])
  })

  test('stage line wording', () => {
    expect(stageLine(DETAIL, { ...PIPELINE, outstanding: [] })).toBe('IN PROGRESS → ready for done')
    expect(stageLine(DETAIL, { laneless: true, lane: '', gateState: '', outstanding: [] })).toBe('IN PROGRESS → no pipeline')
    expect(stageLine(DETAIL, null)).toBe('IN PROGRESS')
  })
})

describe('text', () => {
  test('ages read as minutes, hours, then days; unparseable is empty', () => {
    const now = Date.parse('2026-10-05T12:00:00Z')
    expect(ageLabel('2026-10-05T11:55:00Z', now)).toBe('5m')
    expect(ageLabel('2026-10-05T09:00:00Z', now)).toBe('3h')
    expect(ageLabel('2026-10-01T12:00:00Z', now)).toBe('4d')
    expect(ageLabel('not a date', now)).toBe('')
  })

  test('one line and truncation never exceed the width', () => {
    expect(oneLine('a\n  b\n\nc')).toBe('a b c')
    expect(truncate('abcdef', 4)).toBe('abc…')
    expect(truncate('abc', 4)).toBe('abc')
    expect(truncate('abc', 1)).toBe('…')
    expect(truncate('abc', 0)).toBe('')
    expect(truncate('abc', -5)).toBe('')
  })

  test('Telegram copy text is tldr, next action and the absolute path; the path follows the root separator', () => {
    expect(handoverPath('C:\\Users\\gruku\\Files\\Claude\\claude-tools\\', '2026-10-05-x')).toBe(
      'C:\\Users\\gruku\\Files\\Claude\\claude-tools\\.taskmaster\\handovers\\2026-10-05-x.md',
    )
    expect(handoverPath('/home/me/proj', 'h1')).toBe('/home/me/proj/.taskmaster/handovers/h1.md')
    expect(handoverCopyText({ id: 'h1', created: '', tldr: 'Shipped it', nextAction: 'Record merge', path: '/p/h1.md' })).toBe(
      'Shipped it\n\nNext: Record merge\n\n/p/h1.md',
    )
  })
})
