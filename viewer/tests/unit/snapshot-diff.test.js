import test from 'node:test';
import assert from 'node:assert/strict';
import { snapshotDiff } from '../../js/components/snapshot-diff.js';

test('detects added/removed/changed tasks', () => {
  const a = { tasks: { 'T-1': { status: 'todo' }, 'T-2': { status: 'in-progress' } } };
  const b = { tasks: { 'T-2': { status: 'done' }, 'T-3': { status: 'todo' } } };
  const d = snapshotDiff(a, b);
  assert.deepEqual(d.tasks_added.map(t => t.id), ['T-3']);
  assert.deepEqual(d.tasks_removed.map(t => t.id), ['T-1']);
  assert.equal(d.tasks_changed[0].id, 'T-2');
  assert.equal(d.tasks_changed[0].from.status, 'in-progress');
  assert.equal(d.tasks_changed[0].to.status, 'done');
});

test('detects issue transitions', () => {
  const a = { tasks: {}, issues: { 'ISS-1': { status: 'open' } } };
  const b = { tasks: {}, issues: { 'ISS-1': { status: 'fixed' } } };
  const d = snapshotDiff(a, b);
  assert.equal(d.issues_transitioned[0].id, 'ISS-1');
  assert.equal(d.issues_transitioned[0].from, 'open');
  assert.equal(d.issues_transitioned[0].to, 'fixed');
});

test('passes through files_touched', () => {
  const a = { tasks: {} };
  const b = { tasks: {}, files_touched: ['x.py'] };
  const d = snapshotDiff(a, b);
  assert.deepEqual(d.files_touched, ['x.py']);
});
