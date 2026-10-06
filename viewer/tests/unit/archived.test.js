// User intent: pin how Archived groups its tasks — listed epics first, unknown epics alphabetically, then "No epic" —
// and that the search matches id or title, so the screen's headings never reorder or lose a task silently.
import test from 'node:test';
import assert from 'node:assert/strict';
import { archivedGroups } from '../../js/screens/archived.js';

const t = (id, title, epic, status = 'archived') => ({ id, title, epic, status });
const BACKLOG = {
  epics: [{ id: 'b', name: 'Bee name', title: 'Bee title' }, { id: 'a', title: 'Ay title' }, { id: 'c' }],
  tasks: [
    t('T-1', 'Zeta one', 'zz'), t('T-2', 'No epic task', undefined), t('T-3', 'Alpha in a', 'a'),
    t('T-4', 'Beta in b', 'b'), t('T-5', 'Unknown mm', 'mm'), t('T-6', 'Live task', 'a', 'todo'),
    t('T-7', 'Gamma in c', 'c'), t('T-8', 'Second b', 'b', 'Archived'),
  ],
};

test('groups follow listed epics, then unknown ids alphabetically, then No epic', () => {
  const groups = archivedGroups(BACKLOG);
  assert.deepEqual(groups.map((g) => g.key), ['b', 'a', 'c', 'mm', 'zz', '__none__']);
  assert.deepEqual(groups.find((g) => g.key === 'b').tasks.map((x) => x.id), ['T-4', 'T-8']);
});

test('labels come from name, then title, then id; the no-epic group is "No epic"', () => {
  const label = Object.fromEntries(archivedGroups(BACKLOG).map((g) => [g.key, g.label]));
  assert.deepEqual(label, { b: 'Bee name', a: 'Ay title', c: 'c', mm: 'mm', zz: 'zz', __none__: 'No epic' });
});

test('the query matches id and title, case-insensitively', () => {
  assert.deepEqual(archivedGroups(BACKLOG, 't-7').flatMap((g) => g.tasks.map((x) => x.id)), ['T-7']);
  assert.deepEqual(archivedGroups(BACKLOG, 'ALPHA').flatMap((g) => g.tasks.map((x) => x.id)), ['T-3']);
  assert.deepEqual(archivedGroups(BACKLOG, 'nothing like it'), []);
});

test('non-archived tasks never appear', () => {
  const ids = archivedGroups(BACKLOG).flatMap((g) => g.tasks.map((x) => x.id));
  assert.ok(!ids.includes('T-6'));
  assert.equal(ids.length, 7);
});

test('a missing or malformed backlog gives no groups', () => {
  assert.deepEqual(archivedGroups(null), []);
  assert.deepEqual(archivedGroups(undefined), []);
  assert.deepEqual(archivedGroups({ tasks: 'x' }), []);
  assert.deepEqual(archivedGroups('x'), []);
});
