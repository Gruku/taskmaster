// User intent: the handovers pane's rules without drawing (spec §9) — a handover counts under each of its linked tasks'
// phases or under no task, closed ones only on demand, j / k cross pages, a handover opened from elsewhere lands on its
// phase and page, and a close leaves one toast naming every refusal.
import { describe, expect, test } from 'claude-code/testing'

import { DEMO_TASKS, demoHandoverList, demoSnapshot } from '../hooks/demo'
import {
  allCount,
  arrival,
  closableSelection,
  closeToast,
  listedHandovers,
  markClosed,
  moveTarget,
  phaseHandovers,
  phasesOf,
  tallyLine,
  withoutHandovers,
} from '../hooks/handover-list'
import { ALL, NO_TASK } from '../hooks/phases'
import type { TmHandover, TmPhase } from '../types'

const HOUR = 3_600_000
const NOW = 200 * HOUR
const LIST = demoHandoverList(NOW)
const SNAP = demoSnapshot(NOW)
const ids = (hs: readonly TmHandover[]) => hs.map(h => h.id)
const phase = (id: string): TmPhase => ({ id, status: 'planned', order: 1, count: 0, name: id })
const many = (n: number): TmHandover[] =>
  Array.from({ length: n }, (_, i) => ({ ...SNAP.handovers[0]!, id: `h-${i + 1}`, created: new Date(NOW - i * HOUR).toISOString(), taskIds: [] }))

describe('handover phases', () => {
  test('a handover counts under each linked task phase once, no task when it links none, nowhere when its tasks are unread', () => {
    const [shipped, audit, viewer, hang] = SNAP.handovers as readonly TmHandover[]
    expect(phasesOf(shipped!, DEMO_TASKS)).toEqual(['patch-1-9'])
    expect(phasesOf(audit!, DEMO_TASKS)).toEqual(['patch-1-9'])
    expect(phasesOf(viewer!, DEMO_TASKS)).toEqual([NO_TASK])
    expect(phasesOf(hang!, DEMO_TASKS)).toEqual(['release-2'])
    expect(phasesOf(hang!, {})).toEqual([])
    const { taskIds: _gone, ...old } = shipped!
    expect(phasesOf(old as TmHandover, DEMO_TASKS)).toEqual([NO_TASK])
  })

  test('the pane lists its own read, else the snapshot window; closed ones only while shown; a phase narrows it', () => {
    expect(ids(listedHandovers(LIST, SNAP, false))).toEqual(ids(SNAP.handovers))
    expect(listedHandovers(LIST, SNAP, true)).toHaveLength(7)
    expect(ids(listedHandovers(null, SNAP, true))).toEqual(ids(SNAP.handovers))
    expect(listedHandovers(null, null, false)).toEqual([])
    const all = listedHandovers(LIST, SNAP, true)
    expect(ids(phaseHandovers(all, 'patch-1-9', LIST.tasks))).toEqual(['2026-10-05-shipped-unified-chat-022', '2026-10-04-audit-fixes-landed'])
    expect(ids(phaseHandovers(all, NO_TASK, LIST.tasks))).toEqual(['2026-10-03-viewer-reskin-2a', '2026-10-01-release-7-0-0', '2026-09-29-store-spike-notes'])
    expect(phaseHandovers(all, ALL, LIST.tasks)).toBe(all)
  })

  test('All counts the open total, plus the closed ones read while shown', () => {
    expect(allCount(LIST, SNAP, false)).toBe(SNAP.handoversTotal)
    expect(allCount(LIST, SNAP, true)).toBe(SNAP.handoversTotal + 2)
    expect(allCount(null, SNAP, true)).toBe(SNAP.handoversTotal)
    expect(allCount(null, null, false)).toBe(0)
  })

  test('the tally line: open, closed while listed, newest age; empty for nothing', () => {
    expect(tallyLine(listedHandovers(LIST, SNAP, false), NOW)).toBe('5 open · newest 2h ago')
    expect(tallyLine(listedHandovers(LIST, SNAP, true), NOW)).toBe('5 open · 2 closed · newest 2h ago')
    expect(tallyLine([], NOW)).toBe('')
  })
})

describe('moving and arriving', () => {
  const list = many(12)
  test('j and k cross pages and stop at the ends', () => {
    expect(moveTarget(list, 0, 'h-1', 1)).toEqual({ page: 0, id: 'h-2' })
    expect(moveTarget(list, 0, 'h-5', 1)).toEqual({ page: 1, id: 'h-6' })
    expect(moveTarget(list, 1, 'h-6', -1)).toEqual({ page: 0, id: 'h-5' })
    expect(moveTarget(list, 0, '', 1)).toEqual({ page: 0, id: 'h-2' })
    expect(moveTarget(list, 0, 'h-1', -1)).toBeNull()
    expect(moveTarget(list, 2, 'h-12', 1)).toBeNull()
    expect(moveTarget([], 0, '', 1)).toBeNull()
  })

  test('arriving keeps a phase that holds the handover, else picks its first known phase, else All; with its page', () => {
    const all = listedHandovers(LIST, SNAP, false)
    const phases = ['patch-1-9', 'release-2', NO_TASK].map(phase)
    expect(arrival(all, '2026-10-02-store-read-hang', LIST.tasks, phases, ALL)).toEqual({ choice: ALL, page: 0 })
    expect(arrival(all, '2026-10-02-store-read-hang', LIST.tasks, phases, 'patch-1-9')).toEqual({ choice: 'release-2', page: 0 })
    expect(arrival(all, '2026-10-04-audit-fixes-landed', LIST.tasks, phases, 'patch-1-9')).toEqual({ choice: 'patch-1-9', page: 0 })
    expect(arrival(all, '2026-10-03-viewer-reskin-2a', LIST.tasks, phases, 'release-2')).toEqual({ choice: NO_TASK, page: 0 })
    expect(arrival(all, '2026-10-02-store-read-hang', LIST.tasks, [], 'patch-1-9')).toEqual({ choice: ALL, page: 0 })
    expect(arrival(many(12), 'h-11', {}, [], ALL)).toEqual({ choice: ALL, page: 2 })
    expect(arrival(all, 'not-listed', LIST.tasks, phases, ALL)).toBeNull()
  })
})

describe('closing', () => {
  test('Close N counts selected handovers that are open and shown', () => {
    const shown = listedHandovers(LIST, SNAP, true)
    expect(closableSelection(['2026-10-01-release-7-0-0', '2026-09-30-row-counter-ported', 'gone'], shown)).toEqual(['2026-10-01-release-7-0-0'])
  })

  test('one toast: the closed count, then every refusal with its server wording', () => {
    expect(closeToast(['a', 'b'], [])).toBe('2 closed')
    expect(closeToast(['a'], [{ id: 'b', refusal: 'Handover not found: b' }])).toBe('1 closed · 1 refused: b (Handover not found: b)')
    expect(closeToast([], [{ id: 'b', refusal: 'x' }, { id: 'c', refusal: 'y' }])).toBe('0 closed · 2 refused: b (x), c (y)')
  })

  test('closed handovers turn closed in the list and leave the snapshot window and its total', () => {
    const id = '2026-10-01-release-7-0-0'
    const list = markClosed(LIST, [id])
    expect(list.handovers.find(h => h.id === id)?.status).toBe('closed')
    expect(list.total).toBe(LIST.total - 1)
    const s = withoutHandovers(SNAP, [id])
    expect(ids(s.handovers)).not.toContain(id)
    expect(s.handoversTotal).toBe(SNAP.handoversTotal - 1)
  })
})
