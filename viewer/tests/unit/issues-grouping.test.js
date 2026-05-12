import { test } from 'node:test';
import assert from 'node:assert/strict';
import { groupByStatus, groupBySeverity } from '../../js/util/issues-grouping.js';

const issues = [
  { id: 'A', status: 'open',          severity_label: 'Critical' },
  { id: 'B', status: 'open',          severity_label: 'High' },
  { id: 'C', status: 'investigating', severity_label: 'High' },
  { id: 'D', status: 'fixed',         severity_label: 'Medium' },
  { id: 'E', status: 'wontfix',       severity_label: 'Low' },
  { id: 'F', status: 'open' }, // missing severity_label → Medium default? no — falls into none
];

test('groupByStatus: partitions by status field', () => {
  const g = groupByStatus(issues);
  assert.deepEqual(g.open.map(i => i.id),          ['A', 'B', 'F']);
  assert.deepEqual(g.investigating.map(i => i.id), ['C']);
  assert.deepEqual(g.fixed.map(i => i.id),         ['D']);
  assert.deepEqual(g.wontfix.map(i => i.id),       ['E']);
});

test('groupByStatus: unknown status is dropped, not crashed', () => {
  const g = groupByStatus([{ id: 'X', status: 'weird' }]);
  assert.deepEqual(g.open, []);
  assert.deepEqual(g.investigating, []);
  assert.deepEqual(g.fixed, []);
  assert.deepEqual(g.wontfix, []);
});

test('groupBySeverity: partitions by severity_label', () => {
  const g = groupBySeverity(issues);
  assert.deepEqual(g.Critical.map(i => i.id), ['A']);
  assert.deepEqual(g.High.map(i => i.id),     ['B', 'C']);
  assert.deepEqual(g.Medium.map(i => i.id),   ['D']);
  assert.deepEqual(g.Low.map(i => i.id),      ['E']);
});

test('groupBySeverity: missing severity_label is dropped', () => {
  const g = groupBySeverity([{ id: 'F', status: 'open' }]);
  assert.equal(g.Critical.length + g.High.length + g.Medium.length + g.Low.length, 0);
});
