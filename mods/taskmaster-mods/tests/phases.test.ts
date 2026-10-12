// User intent: pin the phase strip's model (spec §5) without drawing — order, both-way steps that stop at the ends, digits,
// what fits, the done toggle, the fallback when a remembered phase is gone, and the store key that moved per pane.
import { describe, expect, test } from 'claude-code/testing'

import { parsePhases } from '../hooks/parse'
import {
  ALL,
  atEnd,
  emptyLine,
  FRESH_PHASE_VIEW,
  fallback,
  isDoneShown,
  legacyPhaseKey,
  loadChoice,
  NO_TASK,
  orderPhases,
  pickerName,
  type PhaseStore,
  phaseKey,
  saveChoice,
  shownTabs,
  step,
  storedPhase,
  withClosed,
  withCounts,
} from '../hooks/phases'
import type { TmPhase } from '../types'
import { PHASES_WAITING } from './fixtures/phases'

const phase = (id: string, status: string, order: number, count: number, extra: Partial<TmPhase> = {}): TmPhase => ({
  id,
  status,
  order,
  count,
  name: `${id.toUpperCase()} — scope`,
  ...extra,
})
const REAL = ((): TmPhase[] => {
  const parsed = parsePhases(PHASES_WAITING)
  if (!parsed.ok) throw new Error(parsed.reason)
  return parsed.value
})()
const ids = (tabs: readonly { id: string }[]): string[] => tabs.map(t => t.id)

function memoryStore(seed: Record<string, unknown> = {}): PhaseStore & { map: Map<string, unknown> } {
  const map = new Map(Object.entries(seed))
  return {
    map,
    get: async key => map.get(key),
    set: async (key, value) => {
      map.set(key, value)
    },
    delete: async key => {
      map.delete(key)
    },
  }
}

describe('order', () => {
  test('All, the active phase, planned by order, done newest first, then no task; names are the short names', () => {
    const tabs = orderPhases(REAL, 31)
    expect(tabs[0]).toEqual({ id: ALL, name: 'All', kind: 'all', count: 31, date: '', dim: false })
    expect(ids(tabs).slice(0, 12)).toEqual([
      ALL,
      '2-0-4',
      'patch-1-9-5',
      'alpha-followup',
      'engine-polish-post',
      'p2',
      'patch-1-9-8',
      'p3',
      'studio',
      'research',
      '2-0-6',
      '2-1-0',
    ])
    // done and archived phases, no dates in this table: highest order first; ties (patch-1-9-4 and engine-polish, both 2) fall back to id
    expect(ids(tabs).slice(12)).toEqual(['2-0-3', '2-0-2', '2-0-1', 'patch-1-9-3', 'patch-1-9-6', 'patch-1-9-7', 'engine-polish', 'patch-1-9-4', 'p1'])
    expect(pickerName(tabs[1]!)).toBe('Patch 2.0.4 · active')
    expect(tabs[1]?.count).toBe(249)
    expect(pickerName(tabs[2]!)).toBe('Patch 1.9.5')
    expect(tabs[2]?.count).toBe(47)
    expect(tabs.find(t => t.id === 'alpha-followup')).toMatchObject({ name: 'Alpha Patches', dim: true })
    expect(pickerName(tabs.find(t => t.id === '2-0-3')!)).toBe('Patch 2.0.3 ✓')
    expect(tabs.find(t => t.id === '2-0-3')?.count).toBe(1)
  })

  test('done phases with dates sort newest first; no task comes last; a choice the list lacks shows as its bare id after All', () => {
    const tabs = orderPhases(
      [
        phase('old', 'done', 5, 0, { date: '2026-09-01' }),
        phase('new', 'done', 1, 2, { date: '2026-10-01' }),
        { id: NO_TASK, status: 'none', order: 0, count: 3, name: 'no task' },
        phase('a', 'active', 3, 1),
      ],
      6,
      'gone',
    )
    expect(ids(tabs)).toEqual([ALL, 'gone', 'a', 'new', 'old', NO_TASK])
    expect(tabs[1]).toMatchObject({ name: 'gone', dim: true, count: 0 })
    expect(pickerName(tabs[5]!)).toBe('no task')
    expect(tabs[5]?.count).toBe(3)
    expect(ids(orderPhases([], 0, NO_TASK))).toEqual([ALL, NO_TASK])
    expect(orderPhases([], 0, NO_TASK)[1]?.name).toBe('no task')
  })

  test('phases sharing an order come out in id order whatever the input order (planned and done)', () => {
    const planned = [phase('c', 'planned', 2, 1), phase('a', 'planned', 2, 1), phase('b', 'planned', 2, 1)]
    expect(ids(orderPhases(planned, 3)).slice(1)).toEqual(['a', 'b', 'c'])
    expect(ids(orderPhases([...planned].reverse(), 3)).slice(1)).toEqual(['a', 'b', 'c'])
    const done = [phase('z', 'done', 4, 0, { date: '2026-10-01' }), phase('y', 'done', 4, 0, { date: '2026-10-01' })]
    expect(ids(orderPhases(done, 0)).slice(1)).toEqual(['y', 'z'])
    expect(ids(orderPhases([...done].reverse(), 0)).slice(1)).toEqual(['y', 'z'])
  })
})

describe('done toggle and visibility', () => {
  test('done shows by default up to four done phases; the toggle wins; a chosen done phase stays shown', () => {
    const real = orderPhases(REAL, 31)
    expect(isDoneShown(real, null)).toBe(false)
    expect(isDoneShown(real, true)).toBe(true)
    const few = orderPhases([phase('d1', 'done', 1, 0), phase('a', 'active', 2, 1)], 1)
    expect(isDoneShown(few, null)).toBe(true)
    expect(isDoneShown(few, false)).toBe(false)
    expect(ids(shownTabs(real, ALL, false))).not.toContain('2-0-3')
    expect(ids(shownTabs(real, '2-0-3', false))).toContain('2-0-3')
    expect(shownTabs(real, ALL, true)).toHaveLength(real.length)
  })
})

describe('stepping', () => {
  test('‹ and › walk the shown tabs both ways and stop at the ends; an unknown choice steps to All', () => {
    const shown = shownTabs(orderPhases(REAL, 31), ALL, false)
    expect(step(shown, ALL, 1)).toBe('2-0-4')
    expect(step(shown, '2-0-4', 1)).toBe('patch-1-9-5')
    expect(step(shown, 'patch-1-9-5', -1)).toBe('2-0-4')
    expect(step(shown, ALL, -1)).toBe(ALL)
    expect(atEnd(shown, ALL, -1)).toBe(true)
    expect(atEnd(shown, ALL, 1)).toBe(false)
    expect(step(shown, '2-1-0', 1)).toBe('2-1-0')
    expect(atEnd(shown, '2-1-0', 1)).toBe(true)
    expect(step(shown, 'nowhere', 1)).toBe(ALL)
    expect(atEnd(shown, 'nowhere', -1)).toBe(true)
  })
})

describe('fallback and words', () => {
  test('a remembered phase the read list lacks falls back to All, naming it; before the list is read nothing falls back', () => {
    const loaded = { ...FRESH_PHASE_VIEW, loaded: true, phases: REAL }
    expect(fallback({ ...loaded, choice: '2-0-6' })).toEqual({ choice: '2-0-6', lost: '' })
    expect(fallback({ ...loaded, choice: 'gone' })).toEqual({ choice: ALL, lost: 'gone' })
    expect(fallback({ ...loaded, choice: ALL })).toEqual({ choice: ALL, lost: '' })
    expect(fallback({ ...FRESH_PHASE_VIEW, choice: 'gone' })).toEqual({ choice: 'gone', lost: '' })
  })

  test('the empty line names the phase and, for a done one, its date', () => {
    const [done] = orderPhases([phase('p18', 'done', 1, 0, { date: '2026-10-05', name: 'Patch 1.8 — Demo scope' })], 0).slice(1)
    expect(emptyLine('Nothing waiting', done)).toBe('Nothing waiting in Patch 1.8 · done 2026-10-05')
    expect(emptyLine('Nothing waiting', orderPhases([], 4)[0])).toBe('Nothing waiting')
    expect(emptyLine('No sessions', undefined)).toBe('No sessions')
  })

  test('restore recounts the phases from its own counts and adds no task; handovers add closed when ◐ closed is on', () => {
    const phases = [phase('a', 'active', 2, 0), phase('b', 'planned', 3, 0)]
    expect(withCounts(phases, new Map([['a', 4], [NO_TASK, 2]]))).toEqual([
      { ...phases[0]!, count: 4 },
      { ...phases[1]!, count: 0 },
      { id: NO_TASK, status: 'none', order: 0, count: 2, name: 'no task' },
    ])
    expect(withCounts(phases, new Map()).map(p => p.id)).toEqual(['a', 'b'])
    expect(withClosed([phase('a', 'active', 1, 2, { closed: 3 }), phase('b', 'planned', 2, 1)]).map(p => p.count)).toEqual([5, 1])
  })
})

describe('the stored choice', () => {
  test('a stored choice is a string, empty included; anything else is no choice', () => {
    expect(storedPhase({ phase: 'r15' })).toBe('r15')
    expect(storedPhase({ phase: '' })).toBe('')
    expect(storedPhase(undefined)).toBeNull()
    expect(storedPhase({ phase: 3 })).toBeNull()
    expect(storedPhase('r15')).toBeNull()
  })

  test('each pane keeps its own key; review moves the legacy phase:<root> to phase:review:<root> on first read', async () => {
    const root = 'C:\\work\\proj'
    expect(phaseKey('handovers', root)).toBe('phase:handovers:C:\\work\\proj')
    const store = memoryStore({ [legacyPhaseKey(root)]: { phase: '2-0-6' } })
    expect(await loadChoice(store, 'handovers', root)).toBeNull()
    expect(store.map.has(legacyPhaseKey(root))).toBe(true)
    expect(await loadChoice(store, 'review', root)).toBe('2-0-6')
    expect(store.map.get(phaseKey('review', root))).toEqual({ phase: '2-0-6' })
    expect(store.map.has(legacyPhaseKey(root))).toBe(false)
    await saveChoice(store, 'restore', root, NO_TASK)
    expect(await loadChoice(store, 'restore', root)).toBe(NO_TASK)
    const both = memoryStore({ [legacyPhaseKey(root)]: { phase: 'old' }, [phaseKey('review', root)]: { phase: '' } })
    expect(await loadChoice(both, 'review', root)).toBe('')
    expect(both.map.has(legacyPhaseKey(root))).toBe(true)
  })
})
