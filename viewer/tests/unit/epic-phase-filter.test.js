// v3-polish-047: Epic filter polish tests
// Tests for phase-scoped visibility (a) and phase-scoped ranking (c).
// Item (b) archived hidden is in a DOM component, tested functionally.

import test from 'node:test';
import assert from 'node:assert/strict';
import { rankEpics, countActiveTasksByEpic } from '../../js/lib/epic-ranking.js';
import { epicsForPhase } from '../../js/lib/filters.js';

// Shared test data
const EPICS = [
  { id: 'alpha',   name: 'Alpha',   status: 'active' },
  { id: 'bravo',   name: 'Bravo',   status: 'active' },
  { id: 'charlie', name: 'Charlie', status: 'active' },
  { id: 'delta',   name: 'Delta',   status: 'archived' },
];

const TASKS = [
  { id: 'T-001', epic: 'alpha',   status: 'todo',        phase: 'P-01' },
  { id: 'T-002', epic: 'alpha',   status: 'in-progress', phase: 'P-01' },
  { id: 'T-003', epic: 'bravo',   status: 'todo',        phase: 'P-02' },
  { id: 'T-004', epic: 'bravo',   status: 'done',        phase: 'P-02' },
  { id: 'T-005', epic: 'charlie', status: 'todo',        phase: 'P-01' },
  { id: 'T-006', epic: 'delta',   status: 'todo',        phase: 'P-01' },  // archived epic
  { id: 'T-007', epic: 'charlie', status: 'todo',        phase: 'P-02' },
];

// ── (a) Phase-scoped visibility ──────────────────────────────────────────────

test('epicsForPhase — returns only epics with ≥1 task in the active phase (v3-polish-047)', () => {
  // P-01 has: alpha(T-001,T-002), charlie(T-005), delta(T-006)
  const inPhase = epicsForPhase(EPICS, TASKS, 'P-01');
  const ids = inPhase.map(e => e.id).sort();
  assert.ok(ids.includes('alpha'),   'alpha has tasks in P-01 — must appear');
  assert.ok(ids.includes('charlie'), 'charlie has tasks in P-01 — must appear');
  assert.ok(ids.includes('delta'),   'delta has tasks in P-01 — must appear (archived but has tasks)');
  assert.ok(!ids.includes('bravo'),  'bravo has no tasks in P-01 — must NOT appear');
});

test('epicsForPhase — P-02: bravo and charlie visible, alpha not (v3-polish-047)', () => {
  // P-02 has: bravo(T-003,T-004), charlie(T-007)
  const inPhase = epicsForPhase(EPICS, TASKS, 'P-02');
  const ids = inPhase.map(e => e.id).sort();
  assert.ok(ids.includes('bravo'),   'bravo has tasks in P-02 — must appear');
  assert.ok(ids.includes('charlie'), 'charlie has tasks in P-02 — must appear');
  assert.ok(!ids.includes('alpha'),  'alpha has no tasks in P-02 — must NOT appear');
});

test('epicsForPhase — no phase (__all__) returns all epics (v3-polish-047)', () => {
  const all = epicsForPhase(EPICS, TASKS, '__all__');
  assert.equal(all.length, EPICS.length, 'all epics must be returned when no phase filter');
});

test('epicsForPhase — null phase returns all epics (v3-polish-047)', () => {
  const all = epicsForPhase(EPICS, TASKS, null);
  assert.equal(all.length, EPICS.length);
});

// ── (c) Phase-scoped ranking ─────────────────────────────────────────────────

test('rankEpics — uses phase-scoped counts when provided (v3-polish-047)', () => {
  // Phase P-01: alpha has 2 active tasks (T-001 todo, T-002 in-progress), charlie 1 (T-005), delta 1 (T-006)
  const tasksInPhase = TASKS.filter(t => t.phase === 'P-01');
  const phaseCounts = countActiveTasksByEpic(tasksInPhase);
  const epicsInPhase = epicsForPhase(EPICS, TASKS, 'P-01');
  const ranked = rankEpics(epicsInPhase.map(e => ({
    ...e,
    count: tasksInPhase.filter(t => t.epic === e.id).length,
  })), phaseCounts);

  // alpha has 2 active phase tasks, charlie/delta have 1 each → alpha must rank first
  assert.equal(ranked[0].id, 'alpha', 'alpha (2 active in P-01) must rank first');
});

test('rankEpics — global counts differ from phase-scoped counts (ISS-046 regression check)', () => {
  // Global: alpha=2 active, bravo=1 active (T-003 todo), charlie=2 active (T-005+T-007)
  const globalCounts = countActiveTasksByEpic(TASKS);
  // Phase P-01 counts: alpha=2, charlie=1, delta=1 (bravo=0 — not in phase)
  const tasksInP01 = TASKS.filter(t => t.phase === 'P-01');
  const phaseCounts = countActiveTasksByEpic(tasksInP01);

  // Charlie has 1 in P-01 but 2 globally → counts differ
  assert.equal(globalCounts.get('charlie'), 2, 'charlie has 2 active tasks globally');
  assert.equal(phaseCounts.get('charlie'), 1, 'charlie has 1 active task in P-01');
  // This test documents the data discrepancy that motivates phase-scoped ranking.
});
