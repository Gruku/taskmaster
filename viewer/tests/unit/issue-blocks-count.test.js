import { test } from 'node:test';
import assert from 'node:assert/strict';
import { computeBlocksCount } from '../../js/util/issue-blocks.js';

const tasks = {
  'T-1': { id: 'T-1', status: 'in-progress' },
  'T-2': { id: 'T-2', status: 'done' },
  'T-3': { id: 'T-3', status: 'todo' },
};

test('issue blocks 2 non-done tasks via related_tasks', () => {
  const issue = { id: 'I-1', related_tasks: ['T-1', 'T-2', 'T-3'] };
  assert.equal(computeBlocksCount(issue, tasks), 2);
});

test('issue blocks via discovered_in_task too', () => {
  const issue = { id: 'I-2', related_tasks: ['T-2'], discovered_in_task: 'T-1' };
  assert.equal(computeBlocksCount(issue, tasks), 1);
});

test('zero blocks when all referenced tasks are done', () => {
  const issue = { id: 'I-3', related_tasks: ['T-2'] };
  assert.equal(computeBlocksCount(issue, tasks), 0);
});

test('zero blocks when no task refs', () => {
  assert.equal(computeBlocksCount({ id: 'I-4' }, tasks), 0);
});

test('unknown task IDs are ignored', () => {
  const issue = { id: 'I-5', related_tasks: ['T-1', 'T-NOPE'] };
  assert.equal(computeBlocksCount(issue, tasks), 1);
});
