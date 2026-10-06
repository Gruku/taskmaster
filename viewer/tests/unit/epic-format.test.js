// plugins/taskmaster/viewer/tests/unit/epic-format.test.js
import test from 'node:test';
import assert from 'node:assert/strict';
import {
  designBadge, componentGlyph, progressPercent, epicProgress, tasksForComponent,
  epicStats, isCloseable, epicBreakdown, EPIC_STATUS, epicStatusMeta, STATUS_GROUPS,
} from '../../js/lib/epic-format.js';
import { LONG_IDS_BOARD, LONG_CLOSEABLE_EPIC, epicPayload } from '../mock-fixtures.js';

test('designBadge — locked carries a lock flag and label', () => {
  const b = designBadge('locked');
  assert.equal(b.locked, true);
  assert.equal(b.label, 'Locked');
  assert.equal(b.cls, 'locked');
});

test('designBadge — unknown/empty falls back to exploring', () => {
  assert.equal(designBadge('bogus').cls, 'exploring');
  assert.equal(designBadge(undefined).cls, 'exploring');
});

test('componentGlyph — per-status glyph, default for unknown', () => {
  assert.equal(componentGlyph('done'), '●');
  assert.equal(componentGlyph('in-progress'), '◐');
  assert.equal(componentGlyph('blocked'), '✗');
  assert.equal(componentGlyph('todo'), '○');
  assert.equal(componentGlyph(undefined), '○');
});

test('progressPercent — (done+archived)/total, 0 when empty', () => {
  assert.equal(progressPercent({ total: 4, done: 1, archived: 1 }), 50);
  assert.equal(progressPercent({ total: 0 }), 0);
  assert.equal(progressPercent(undefined), 0);
});

test('tasksForComponent — key match, and _unassigned = no component', () => {
  const tasks = [
    { id: 'a', component: 'core' },
    { id: 'b', component: 'ui' },
    { id: 'c' },
  ];
  assert.deepEqual(tasksForComponent(tasks, 'core').map(t => t.id), ['a']);
  assert.deepEqual(tasksForComponent(tasks, '_unassigned').map(t => t.id), ['c']);
});

test('epicProgress — closed = done + archived, label and pct agree', () => {
  const p = epicProgress({ total: 55, done: 25, archived: 10 });
  assert.deepEqual(
    { closed: p.closed, pct: p.pct, label: p.label },
    { closed: 35, pct: 64, label: '35/55 closed · 25 done · 10 archived' },
  );
});
test('epicProgress — no archived part when zero', () => {
  assert.equal(epicProgress({ total: 4, done: 1 }).label, '1/4 closed · 1 done');
});
test('epicProgress — empty and missing stats', () => {
  assert.deepEqual(epicProgress(undefined), { total: 0, done: 0, archived: 0, closed: 0, pct: 0, label: '0/0 closed' });
  assert.equal(epicProgress({ total: 0, done: 3 }).pct, 0);
});
test('epicProgress — clamps stale counters', () => {
  const p = epicProgress({ total: 3, done: 3, archived: 2 });
  assert.equal(p.closed, 3);
  assert.equal(p.pct, 100);
});
test('progressPercent delegates', () => {
  assert.equal(progressPercent({ total: 4, done: 1, archived: 1 }), 50);
});

test('epicStats — counts each status, missing as todo, unknown as other; total is the sum', () => {
  const s = epicStats([
    { status: 'done' }, { status: 'done' }, { status: 'archived' }, { status: 'in-review' },
    { status: 'blocked' }, { status: 'in-progress' }, {}, { status: 'paused' }, null, 'x',
  ]);
  assert.deepEqual(s, { total: 8, todo: 1, 'in-progress': 1, 'in-review': 1, blocked: 1, done: 2, archived: 1, other: 1 });
  assert.deepEqual(epicStats(undefined), { total: 0, todo: 0, 'in-progress': 0, 'in-review': 0, blocked: 0, done: 0, archived: 0, other: 0 });
});

test('epicStats — an empty or null status counts as todo, like a missing one', () => {
  assert.deepEqual(epicStats([{ status: '' }, { status: null }, {}]),
    { total: 3, todo: 3, 'in-progress': 0, 'in-review': 0, blocked: 0, done: 0, archived: 0, other: 0 });
});

test('epicStats feeds epicProgress — the spec example reads "35/55 closed · 25 done · 10 archived"', () => {
  const tasks = [
    ...Array(25).fill({ status: 'done' }), ...Array(10).fill({ status: 'archived' }), ...Array(20).fill({ status: 'todo' }),
  ];
  const p = epicProgress(epicStats(tasks));
  assert.deepEqual({ closed: p.closed, done: p.done, archived: p.archived, total: p.total, pct: p.pct, label: p.label },
    { closed: 35, done: 25, archived: 10, total: 55, pct: 64, label: '35/55 closed · 25 done · 10 archived' });
});

test('isCloseable — every task done or archived, and at least one task', () => {
  assert.equal(isCloseable(epicStats([{ status: 'done' }, { status: 'archived' }])), true);
  assert.equal(isCloseable(epicStats([{ status: 'done' }, { status: 'todo' }])), false);
  assert.equal(isCloseable(epicStats([])), false);
  assert.equal(isCloseable(undefined), false);
});

test('epicBreakdown — group order, non-zero only, percents sum to exactly 100', () => {
  const b = epicBreakdown(epicStats([{ status: 'todo' }, { status: 'done' }, { status: 'in-progress' }]));
  assert.deepEqual(b, [
    { status: 'in-progress', count: 1, pct: 34 }, { status: 'todo', count: 1, pct: 33 }, { status: 'done', count: 1, pct: 33 },
  ]);
  assert.deepEqual(epicBreakdown(epicStats([])), []);
  const odd = epicBreakdown(epicStats([...Array(7).fill({ status: 'done' }), { status: 'paused' }, { status: 'blocked' }]));
  assert.deepEqual(odd.map((x) => x.status), ['blocked', 'done', 'other']);
  assert.equal(odd.reduce((n, x) => n + x.pct, 0), 100);
  assert.deepEqual(STATUS_GROUPS, ['in-progress', 'in-review', 'blocked', 'todo', 'done', 'archived']);
});

test('EPIC_STATUS — lifecycle by meaning; missing is Active, unknown is shown as it is', () => {
  assert.deepEqual(Object.keys(EPIC_STATUS), ['active', 'planned', 'done', 'archived']);
  assert.deepEqual(epicStatusMeta('done'), { label: 'Done', shape: '●', tone: 'success' });
  assert.deepEqual(epicStatusMeta(undefined), { label: 'Active', shape: '◐', tone: 'accent' });
  assert.deepEqual(epicStatusMeta(''), { label: 'Active', shape: '◐', tone: 'accent' });
  assert.deepEqual(epicStatusMeta('paused'), { label: 'paused', shape: '○', tone: 'neutral' });
  assert.deepEqual(epicStatusMeta(7), { label: '—', shape: '○', tone: 'neutral' });
  epicStatusMeta('done').label = 'x';
  assert.equal(EPIC_STATUS.done.label, 'Done');
});

test('LONG_IDS_BOARD — the volume the index review focus names', () => {
  assert.equal(LONG_IDS_BOARD.epics.length, 27);
  assert.equal(LONG_IDS_BOARD.tasks.length, 230);
  assert.equal(new Set(LONG_IDS_BOARD.tasks.map((t) => t.id)).size, 230);
  assert.equal(Math.max(...LONG_IDS_BOARD.tasks.map((t) => t.id.length)), 27);
  assert.ok(LONG_IDS_BOARD.tasks.filter((t) => t.title.length === 120).length >= 70);
  assert.ok(LONG_IDS_BOARD.tasks.some((t) => t.status === 'archived'));
  // every epic has tasks, and the per-epic counts add up to the board
  const ids = LONG_IDS_BOARD.epics.map((e) => e.id);
  const sum = ids.reduce((n, id) => n + epicStats(LONG_IDS_BOARD.tasks.filter((t) => t.epic === id)).total, 0);
  assert.equal(sum, 230);
  assert.ok(ids.every((id) => LONG_IDS_BOARD.tasks.some((t) => t.epic === id)));
});

test('epicPayload — the server shape: stats from the epic\'s tasks, closeable, task rows', () => {
  const p = epicPayload(LONG_IDS_BOARD, 'epic-01', { done_when: 'x' });
  const mine = LONG_IDS_BOARD.tasks.filter((t) => t.epic === 'epic-01');
  assert.equal(p.id, 'epic-01');
  assert.equal(p.stats.total, mine.length);
  assert.equal(p.stats.done, mine.filter((t) => t.status === 'done').length);
  assert.equal(p.closeable, p.stats.closeable);
  assert.deepEqual(p.tasks.map((t) => t.id), mine.map((t) => t.id));
  assert.deepEqual(Object.keys(p.tasks[0]).sort(), ['component', 'design_change', 'id', 'phase', 'priority', 'status', 'title']);
  assert.equal(p.done_when, 'x');
  assert.equal(p.design_status, 'exploring');
});

test('LONG_IDS_BOARD — one active epic is closeable, on the server shape and through isCloseable', () => {
  const ep = LONG_IDS_BOARD.epics.find((e) => e.id === LONG_CLOSEABLE_EPIC);
  assert.equal(ep.status, 'active');
  const mine = LONG_IDS_BOARD.tasks.filter((t) => t.epic === LONG_CLOSEABLE_EPIC);
  assert.ok(mine.some((t) => t.status === 'done') && mine.some((t) => t.status === 'archived'));
  assert.equal(epicPayload(LONG_IDS_BOARD, LONG_CLOSEABLE_EPIC).closeable, true);
  assert.equal(isCloseable(epicStats(mine)), true);
  // The only one: every other epic still has open work.
  const closeable = LONG_IDS_BOARD.epics.filter((e) => epicPayload(LONG_IDS_BOARD, e.id).closeable).map((e) => e.id);
  assert.deepEqual(closeable, [LONG_CLOSEABLE_EPIC]);
});

test('epicPayload — attention lists blocked tasks and tasks with blockers (blocked: false), in task order', () => {
  const board = { epics: [{ id: 'e' }], tasks: [
    { id: 'a', title: 'A', status: 'todo', epic: 'e', blockers: 'waits on review' },
    { id: 'b', title: 'B', status: 'blocked', epic: 'e' },
    { id: 'c', title: 'C', status: 'todo', epic: 'e' },
    { id: 'd', title: 'D', status: 'blocked', epic: 'e', blockers: 'needs T-1' },
  ] };
  assert.deepEqual(epicPayload(board, 'e').attention, [
    { id: 'a', title: 'A', blocked: false, why: 'waits on review' },
    { id: 'b', title: 'B', blocked: true, why: '' },
    { id: 'd', title: 'D', blocked: true, why: 'needs T-1' },
  ]);
});
