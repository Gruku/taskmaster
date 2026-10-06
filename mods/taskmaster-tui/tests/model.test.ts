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
  gateSignal,
  handoverCopyText,
  handoverPath,
  handoverRefs,
  oneLine,
  isStaleTicks,
  orderQueue,
  queueDots,
  reviewQueue,
  splitCheck,
  stageLine,
  ticksOf,
  toggleTick,
  truncate,
  wrapText,
} from '../hooks/model'
import { DEMO_DETAILS } from '../hooks/demo'
import type { TmHandover, TmQueueItem, TmSnapshot, TmTaskDetail } from '../types'

const H1: TmHandover = { id: 'h1', created: '', tldr: 'Shipped it', nextAction: '', path: '/p/h1.md', branch: '', taskIds: [] }

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
    expect(handoverCopyText({ ...H1, nextAction: 'Record merge' })).toBe('Shipped it\n\nNext: Record merge\n\n/p/h1.md')
  })

  test('the summary line of branch and tasks leaves out whichever is empty', () => {
    expect(handoverRefs({ ...H1, branch: 'main', taskIds: ['a-001', 'b-002'] })).toBe('main · a-001, b-002')
    expect(handoverRefs({ ...H1, branch: 'main' })).toBe('main')
    expect(handoverRefs({ ...H1, taskIds: ['a-001'] })).toBe('a-001')
    expect(handoverRefs(H1)).toBe('')
    // A reply or an old snapshot without the fields reads as empty.
    const { branch: _b, taskIds: _t, ...bare } = H1
    expect(handoverRefs(bare as TmHandover)).toBe('')
  })
})

describe('review card', () => {
  test('the demo check: a leading label with its parenthetical, then ; clauses', () => {
    expect(splitCheck(DEMO_DETAILS['unified-chat-022']!.humanAction)).toEqual({
      label: 'Live check on dev',
      detail: 'needs unifiedChatGenerate + unifiedChatBuild granted',
      items: ['first unified message stays on intent/brief and does not build', 'second message builds with the full toolset'],
    })
  })

  test('bullet and numbered lines are the items; an intro label labels them; continuation lines join the item above', () => {
    expect(splitCheck('Check on dev:\n- the band shows\n  the task\n* the pane opens\n• copy works')).toEqual({
      label: 'Check on dev',
      detail: '',
      items: ['the band shows the task', 'the pane opens', 'copy works'],
    })
    expect(splitCheck('Run these first, carefully\n1. build\n2) deploy')).toEqual({ label: '', detail: 'Run these first, carefully', items: ['build', 'deploy'] })
  })

  test('no list and no label: the whole text is one item; ; without a label never splits; a URL is no label', () => {
    expect(splitCheck('Open the viewer and confirm one open handover per thread.')).toEqual({
      label: '',
      detail: '',
      items: ['Open the viewer and confirm one open handover per thread.'],
    })
    expect(splitCheck('do a; then b').items).toEqual(['do a; then b'])
    expect(splitCheck('https://example.com/x should load').items).toEqual(['https://example.com/x should load'])
    expect(splitCheck('Smoke: it boots').items).toEqual(['it boots'])
    expect(splitCheck('Verify at 10:30 that the deploy finished; then say so')).toEqual({
      label: '',
      detail: '',
      items: ['Verify at 10:30 that the deploy finished; then say so'],
    })
    expect(splitCheck('   ')).toEqual({ label: '', detail: '', items: [] })
  })

  test('more than nine items are all kept (only the first nine get digit keys)', () => {
    const text = Array.from({ length: 12 }, (_, i) => `- item ${i + 1}`).join('\n')
    expect(splitCheck(text).items).toHaveLength(12)
  })

  test('wrapping keeps every word and never passes the width', () => {
    const lines = wrapText('first unified message stays on intent/brief and does not build', 20)
    expect(lines.join(' ')).toBe('first unified message stays on intent/brief and does not build')
    for (const line of lines) expect(line.length).toBeLessThanOrEqual(20)
    expect(wrapText('abcdefghij', 4)).toEqual(['abcd', 'efgh', 'ij'])
    expect(wrapText('', 10)).toEqual([])
  })

  test('queue dots: done and current filled, ahead hollow, capped with +N', () => {
    expect(queueDots(1, 5)).toBe('●○○○○')
    expect(queueDots(3, 5)).toBe('●●●○○')
    expect(queueDots(2, 355)).toBe('●●○○○○○○○○+345')
    expect(queueDots(12, 15)).toBe('●●●●●●●●●●+5')
  })

  test('the gate signal reads the gate and its state', () => {
    expect(gateSignal('review-gate:pass')).toEqual({ kind: 'success', word: 'review-gate pass' })
    expect(gateSignal('review-gate:fail')).toEqual({ kind: 'critical', word: 'review-gate fail' })
    expect(gateSignal('spec-review:pending')).toEqual({ kind: 'warning', word: 'spec-review pending' })
    expect(gateSignal('')).toBeNull()
  })

  test('ticks: tolerant read, toggle, and 30-day staleness', () => {
    const now = Date.parse('2026-10-06T12:00:00Z')
    expect(ticksOf({ at: now, items: ['a', 3, 'b'] })).toEqual(['a', 'b'])
    expect(ticksOf('junk')).toEqual([])
    expect(toggleTick(['a'], 'b')).toEqual(['a', 'b'])
    expect(toggleTick(['a', 'b'], 'a')).toEqual(['b'])
    expect(isStaleTicks({ at: now - 31 * 86_400_000, items: [] }, now)).toBe(true)
    expect(isStaleTicks({ at: now - 86_400_000, items: [] }, now)).toBe(false)
    expect(isStaleTicks('junk', now)).toBe(true)
  })
})
