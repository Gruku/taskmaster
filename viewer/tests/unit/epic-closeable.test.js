// plugins/taskmaster/viewer/tests/unit/epic-closeable.test.js
// Epic B task 5: the closeable rule (isCloseable) + area filter/group axis.
import test from 'node:test';
import assert from 'node:assert/strict';
import * as epicFormat from '../../js/lib/epic-format.js';
import { applyFilters, groupTasks } from '../../js/lib/filters.js';

const { isCloseable } = epicFormat;

test('isCloseable — true when total>0 and done+archived===total', () => {
  assert.equal(isCloseable({ total: 3, done: 2, archived: 1 }), true);
});

test('isCloseable — false when not closeable', () => {
  assert.equal(isCloseable({ total: 3, done: 1, archived: 0 }), false);
});

test('isCloseable — false when total is 0', () => {
  assert.equal(isCloseable({ total: 0, done: 0, archived: 0 }), false);
  assert.equal(isCloseable(undefined), false);
});

test('the HTML-string closeableBadge is gone: screens draw "Closeable" as a node from isCloseable', () => {
  assert.equal('closeableBadge' in epicFormat, false);
});

const AREA_TASKS = [
  { id: 'a-1', area: 'desktop-app' },
  { id: 'a-2', area: 'desktop-app' },
  { id: 'a-3', area: 'backend' },
  { id: 'a-4', area: null },
];

test('applyFilters — by area (multi)', () => {
  const out = applyFilters(AREA_TASKS, { areas: ['desktop-app'] });
  assert.deepEqual(out.map(t => t.id), ['a-1', 'a-2']);
});

test('applyFilters — area filter empty returns all tasks', () => {
  const out = applyFilters(AREA_TASKS, { areas: [] });
  assert.equal(out.length, 4);
});

test('groupTasks — by area with "No area" bucket for missing area', () => {
  const groups = groupTasks(AREA_TASKS, 'area');
  const ids = groups.map(g => g.key);
  assert.ok(ids.includes('desktop-app'));
  assert.ok(ids.includes('backend'));
  assert.ok(ids.includes('__none__'));
  const none = groups.find(g => g.key === '__none__');
  assert.equal(none.label, 'No area');
  assert.deepEqual(none.tasks.map(t => t.id), ['a-4']);
  const desktop = groups.find(g => g.key === 'desktop-app');
  assert.deepEqual(desktop.tasks.map(t => t.id), ['a-1', 'a-2']);
});

test('isCloseable — stale counters (done+archived > total) still count as closeable', () => {
  assert.equal(isCloseable({ total: 3, done: 3, archived: 2 }), true);
});
