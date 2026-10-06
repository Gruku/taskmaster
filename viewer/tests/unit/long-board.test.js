// User intent: the long-data board fixture stays the size the phone-width checks rely on (27 epics, 230 tasks,
// 120-character titles), so a 390px measurement is made against real data volume.
import test from 'node:test';
import assert from 'node:assert/strict';
import { longBoard } from '../mock-fixtures.js';

test('longBoard — deterministic', () => {
  assert.deepEqual(longBoard(), longBoard());
});

test('longBoard — 27 epics, 230 tasks, 8 phases; ids run to T-1234', () => {
  const b = longBoard();
  assert.equal(b.epics.length, 27);
  assert.equal(b.tasks.length, 230);
  assert.equal(b.phases.length, 8);
  assert.equal(b.tasks[0].id, 'T-1005');
  assert.equal(b.tasks.at(-1).id, 'T-1234');
  assert.equal(b.revision, 'long-r1');
});

test('longBoard — every title is 120 characters', () => {
  for (const t of longBoard().tasks) assert.equal(t.title.length, 120, t.id);
});

test('longBoard — status counts, unphased tasks, one archived phase', () => {
  const b = longBoard();
  const counts = {};
  for (const t of b.tasks) counts[t.status] = (counts[t.status] || 0) + 1;
  assert.deepEqual(counts, { todo: 92, 'in-progress': 46, done: 46, 'in-review': 23, blocked: 23 });
  assert.equal(b.tasks.filter((t) => !t.phase).length, 10);
  assert.equal(b.phases.filter((p) => p.status === 'archived').length, 1);
});
