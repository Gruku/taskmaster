import test from 'node:test';
import assert from 'node:assert/strict';
import {
  countActiveTasksByEpic,
  rankEpics,
  splitQuickAndDropdown,
  sortEpicsForDropdown,
  ACTIVE_TASK_STATUSES,
} from '../../js/lib/epic-ranking.js';

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

test('ACTIVE_TASK_STATUSES — todo, in-progress, in-review (not done, not archived)', () => {
  assert.deepEqual([...ACTIVE_TASK_STATUSES].sort(), ['in-progress', 'in-review', 'todo']);
});

test('countActiveTasksByEpic — counts only todo+in-progress+in-review per epic', () => {
  const out = countActiveTasksByEpic(TASKS);
  assert.equal(out.get('a'), 2);
  assert.equal(out.get('b'), 1);
  assert.equal(out.get('c'), 1);
  assert.equal(out.has('d'), false);
});

test('countActiveTasksByEpic — accepts hyphenated status (in-progress)', () => {
  const out = countActiveTasksByEpic([{ epic: 'x', status: 'in-progress' }]);
  assert.equal(out.get('x'), 1);
});

test('rankEpics — sorts by active task count desc, then last_referenced desc, then alpha', () => {
  const counts = countActiveTasksByEpic(TASKS);
  const ranked = rankEpics(EPICS, counts);
  // a (2) > b (1) tied with c (1) — break by last_referenced (c=2026-05-01) vs missing on b → c first
  // d (0) ties with e (0); break by alpha "Delta" < "Echo" → d first
  assert.deepEqual(ranked.map(e => e.id), ['a', 'c', 'b', 'd', 'e']);
});

test('splitQuickAndDropdown — pinned first (in pin order), then top-N by ranking, max 5', () => {
  const counts = countActiveTasksByEpic(TASKS);
  const ranked = rankEpics(EPICS, counts);
  const out = splitQuickAndDropdown(ranked, ['e', 'b'], 5);
  // Quick: pinned e (archived — pinning wins), b first; then ranked filling remaining slots
  // with non-pinned active epics only (c=done and e=archived excluded from auto-fill).
  // Ranked order: a, c, b, d, e → skip c (done), skip b (pinned), add a then d.
  assert.deepEqual(out.quick.map(e => e.id), ['e', 'b', 'a', 'd']);
  assert.deepEqual(out.dropdown.map(e => e.id), ['c']);
});

test('splitQuickAndDropdown — overflow goes to dropdown', () => {
  const epics = [
    { id: '1' }, { id: '2' }, { id: '3' }, { id: '4' }, { id: '5' }, { id: '6' }, { id: '7' },
  ];
  const out = splitQuickAndDropdown(epics, [], 5);
  assert.deepEqual(out.quick.map(e => e.id),    ['1', '2', '3', '4', '5']);
  assert.deepEqual(out.dropdown.map(e => e.id), ['6', '7']);
});

test('splitQuickAndDropdown — pin id not present in input is ignored', () => {
  const out = splitQuickAndDropdown([{ id: 'a' }, { id: 'b' }], ['ghost', 'a'], 5);
  assert.deepEqual(out.quick.map(e => e.id), ['a', 'b']);
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

test('splitQuickAndDropdown — archived and done epics never auto-fill (only via pinning)', () => {
  const epics = [
    { id: 'a', status: 'done' },        // would rank first by activeCounts, but excluded from auto-fill
    { id: 'b', status: 'archived' },    // ditto
    { id: 'c', status: 'active' },
    { id: 'd', status: 'active' },
  ];
  const out = splitQuickAndDropdown(epics, [], 5);
  // a, b excluded from auto-fill; only c, d enter quick. a, b still surface in dropdown.
  assert.deepEqual(out.quick.map(e => e.id),    ['c', 'd']);
  assert.deepEqual(out.dropdown.map(e => e.id), ['a', 'b']);
});

test('splitQuickAndDropdown — pinned archived/done epic still appears in quick', () => {
  const epics = [
    { id: 'a', status: 'archived' },
    { id: 'b', status: 'active' },
  ];
  const out = splitQuickAndDropdown(epics, ['a'], 5);
  // Pinning explicitly opts in — pinned 'a' (archived) is in quick along with active 'b'.
  assert.deepEqual(out.quick.map(e => e.id), ['a', 'b']);
});

test('splitQuickAndDropdown — capacity 0 yields empty quick, all in dropdown', () => {
  const epics = [{ id: 'a', status: 'active' }, { id: 'b', status: 'active' }];
  const out = splitQuickAndDropdown(epics, ['a'], 0);
  assert.deepEqual(out.quick, []);
  assert.deepEqual(out.dropdown.map(e => e.id), ['a', 'b']);
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
