import test from 'node:test';
import assert from 'node:assert/strict';
import { applyFilters, sortTasks, groupTasks, epicsForPhase, STATUS_ORDER } from '../../js/lib/filters.js';

const TASKS = [
  { id: 'v3-001', title: 'A',          status: 'done',        priority: 'low',      estimate: 'S', phase: 'P-01', epic: 'viewer-redesign',     started: '2026-04-25T10:00:00Z' },
  { id: 'v3-002', title: 'B',          status: 'in_progress', priority: 'critical', estimate: 'L', phase: 'P-03', epic: 'viewer-redesign',     started: '2026-04-26T10:00:00Z' },
  { id: 'v3-003', title: 'Auth thing', status: 'todo',        priority: 'high',     estimate: 'M', phase: 'P-03', epic: 'narrative-continuity',started: null },
  { id: 'v3-004', title: 'Other',      status: 'blocked',     priority: 'medium',   estimate: 'S', phase: null,   epic: null,                  started: null },
];

test('applyFilters — empty filters returns all tasks', () => {
  const out = applyFilters(TASKS, {});
  assert.equal(out.length, 4);
});

test('applyFilters — by priorities (multi)', () => {
  const out = applyFilters(TASKS, { priorities: ['critical', 'high'] });
  assert.deepEqual(out.map(t => t.id), ['v3-002', 'v3-003']);
});

test('applyFilters — by epics (multi, no-epic only matches when "" included)', () => {
  const out = applyFilters(TASKS, { epics: ['viewer-redesign'] });
  assert.deepEqual(out.map(t => t.id), ['v3-001', 'v3-002']);
});

test('applyFilters — by phase (single)', () => {
  const out = applyFilters(TASKS, { phase: 'P-03' });
  assert.deepEqual(out.map(t => t.id), ['v3-002', 'v3-003']);
});

test('applyFilters — phase: orphans selects null/undefined phase', () => {
  const out = applyFilters(TASKS, { phase: '__orphans__' });
  assert.deepEqual(out.map(t => t.id), ['v3-004']);
});

test('applyFilters — search matches id, title, branch (case-insensitive)', () => {
  const out = applyFilters(TASKS, { search: 'auth' });
  assert.deepEqual(out.map(t => t.id), ['v3-003']);
  const out2 = applyFilters(TASKS, { search: 'V3-002' });
  assert.deepEqual(out2.map(t => t.id), ['v3-002']);
});

test('sortTasks — priority desc puts critical first', () => {
  const out = sortTasks(TASKS, { by: 'priority', dir: 'desc' });
  assert.equal(out[0].id, 'v3-002');
  assert.equal(out[3].id, 'v3-001');
});

test('sortTasks — size asc returns S/M/L order', () => {
  const out = sortTasks(TASKS, { by: 'size', dir: 'asc' });
  assert.equal(out[0].estimate, 'S');
  assert.equal(out[out.length - 1].estimate, 'L');
});

test('sortTasks — started desc puts most recent first; null last', () => {
  const out = sortTasks(TASKS, { by: 'started', dir: 'desc' });
  assert.equal(out[0].id, 'v3-002');
  assert.equal(out[out.length - 1].started, null);
});

test('groupTasks — by status uses STATUS_ORDER', () => {
  const groups = groupTasks(TASKS, 'status');
  assert.deepEqual(groups.map(g => g.key), STATUS_ORDER);
  const inProg = groups.find(g => g.key === 'in_progress');
  assert.deepEqual(inProg.tasks.map(t => t.id), ['v3-002']);
});

test('groupTasks — by epic with __none__ bucket for missing epic', () => {
  const groups = groupTasks(TASKS, 'epic');
  const ids = groups.map(g => g.key);
  assert.ok(ids.includes('__none__'));
  const none = groups.find(g => g.key === '__none__');
  assert.deepEqual(none.tasks.map(t => t.id), ['v3-004']);
});

test('groupTasks — by phase keeps phase order from input list', () => {
  const groups = groupTasks(TASKS, 'phase', ['P-01', 'P-02', 'P-03']);
  assert.deepEqual(groups.map(g => g.key), ['P-01', 'P-02', 'P-03', '__orphans__']);
});

const EPICS = [
  { id: 'viewer-redesign',      name: 'Viewer Redesign' },
  { id: 'narrative-continuity', name: 'Narrative Continuity' },
  { id: 'unused-epic',          name: 'Unused' },
];

test('epicsForPhase — phase null/__all__ returns full epic list (copy)', () => {
  const a = epicsForPhase(EPICS, TASKS, null);
  assert.equal(a.length, 3);
  assert.notStrictEqual(a, EPICS); // copy, not same ref
  const b = epicsForPhase(EPICS, TASKS, '__all__');
  assert.equal(b.length, 3);
});

test('epicsForPhase — scopes to epics with tasks in the active phase', () => {
  // P-01 only contains v3-001 → only viewer-redesign has a task there.
  const out = epicsForPhase(EPICS, TASKS, 'P-01');
  assert.deepEqual(out.map(e => e.id), ['viewer-redesign']);
});

test('epicsForPhase — multiple epics in a phase preserve input order', () => {
  // P-03 has v3-002 (viewer-redesign) and v3-003 (narrative-continuity).
  const out = epicsForPhase(EPICS, TASKS, 'P-03');
  assert.deepEqual(out.map(e => e.id), ['viewer-redesign', 'narrative-continuity']);
});

test('epicsForPhase — __orphans__ matches tasks with no phase set', () => {
  // v3-004 has phase null and no epic; no epic should appear.
  const out = epicsForPhase(EPICS, TASKS, '__orphans__');
  assert.deepEqual(out.map(e => e.id), []);

  // Add a phaseless task with an epic.
  const moreTasks = [...TASKS, { id: 'v3-005', phase: null, epic: 'viewer-redesign' }];
  const out2 = epicsForPhase(EPICS, moreTasks, '__orphans__');
  assert.deepEqual(out2.map(e => e.id), ['viewer-redesign']);
});

test('epicsForPhase — phase with no tasks returns empty list', () => {
  const out = epicsForPhase(EPICS, TASKS, 'P-99');
  assert.deepEqual(out, []);
});

test('epicsForPhase — guards against non-array inputs', () => {
  assert.deepEqual(epicsForPhase(null, TASKS, 'P-01'), []);
  assert.deepEqual(epicsForPhase(EPICS, null, 'P-01'), []);
});
