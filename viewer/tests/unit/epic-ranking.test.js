import test from 'node:test';
import assert from 'node:assert/strict';
import {
  rankEpics,
  sortEpicsForDropdown,
} from '../../js/lib/epic-ranking.js';
import { countOpen } from '../../js/lib/filters.js';

const TASKS = [
  { epic: 'a', status: 'todo' },
  { epic: 'a', status: 'in-progress' },
  { epic: 'a', status: 'done' },        // doesn't count
  { epic: 'b', status: 'in-review' },
  { epic: 'b', status: 'archived' },    // doesn't count
  { epic: 'c', status: 'todo' },
  { epic: null, status: 'todo' },       // orphan, ignored
];

const EPICS = [
  { id: 'a', name: 'Alpha',   status: 'active' },
  { id: 'b', name: 'Bravo',   status: 'active' },
  { id: 'c', name: 'Charlie', status: 'done',   last_referenced: '2026-05-01' },
  { id: 'd', name: 'Delta',   status: 'active', last_referenced: '2026-05-08' },
  { id: 'e', name: 'Echo',    status: 'archived' },
];

test('rankEpics — sorts by active task count desc, then last_referenced desc, then alpha', () => {
  const counts = countOpen(TASKS, 'epic');
  const ranked = rankEpics(EPICS, counts);
  // a (2) > b (1) tied with c (1) — break by last_referenced (c=2026-05-01) vs missing on b → c first
  // d (0) ties with e (0); break by alpha "Delta" < "Echo" → d first
  assert.deepEqual(ranked.map(e => e.id), ['a', 'c', 'b', 'd', 'e']);
});

test('sortEpicsForDropdown — count', () => {
  const counts = new Map([['a', 5], ['b', 1], ['c', 3]]);
  const out = sortEpicsForDropdown([{ id: 'a' }, { id: 'b' }, { id: 'c' }], 'count', counts);
  assert.deepEqual(out.map(e => e.id), ['a', 'c', 'b']);
});

test('sortEpicsForDropdown — status: active → done → archived', () => {
  const out = sortEpicsForDropdown(EPICS, 'status', new Map());
  // Active group (a, b, d), then done (c), then archived (e). Stable inside groups.
  assert.deepEqual(out.map(e => e.id), ['a', 'b', 'd', 'c', 'e']);
});

test('sortEpicsForDropdown — recent: last_referenced desc; missing goes last', () => {
  const out = sortEpicsForDropdown(EPICS, 'recent', new Map());
  assert.equal(out[0].id, 'd');     // 2026-05-08
  assert.equal(out[1].id, 'c');     // 2026-05-01
  // a, b, e have no last_referenced — order is stable input order (a, b, e)
  assert.deepEqual(out.slice(2).map(e => e.id), ['a', 'b', 'e']);
});

test('sortEpicsForDropdown — alpha by name (case-insensitive)', () => {
  const out = sortEpicsForDropdown(EPICS, 'alpha', new Map());
  assert.deepEqual(out.map(e => e.id), ['a', 'b', 'c', 'd', 'e']);
});

test('sortEpicsForDropdown — count ties broken alphabetically by name', () => {
  const counts = new Map([['a', 3], ['b', 3], ['c', 1]]);
  const epics = [
    { id: 'b', name: 'Bravo' },
    { id: 'a', name: 'Alpha' },
    { id: 'c', name: 'Charlie' },
  ];
  const out = sortEpicsForDropdown(epics, 'count', counts);
  // a and b both 3; tie broken alpha → a before b. c (1) last.
  assert.deepEqual(out.map(e => e.id), ['a', 'b', 'c']);
});
