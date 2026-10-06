import { test } from 'node:test';
import assert from 'node:assert/strict';
import { groupByStatus, groupBySeverity } from '../../js/util/issues-grouping.js';

const issues = [
  { id: 'A', status: 'open',          severity_label: 'Critical' },
  { id: 'B', status: 'open',          severity_label: 'High' },
  { id: 'C', status: 'investigating', severity_label: 'High' },
  { id: 'D', status: 'fixed',         severity_label: 'Medium' },
  { id: 'E', status: 'wontfix',       severity_label: 'Low' },
  { id: 'F', status: 'open' },
  { id: 'G', status: 'duplicate',     severity: 'P2' },
];

test('groupByStatus: partitions by status field, duplicate included', () => {
  const g = groupByStatus(issues);
  assert.deepEqual(Object.keys(g), ['open', 'investigating', 'fixed', 'wontfix', 'duplicate']);
  assert.deepEqual(g.open.map(i => i.id),          ['A', 'B', 'F']);
  assert.deepEqual(g.investigating.map(i => i.id), ['C']);
  assert.deepEqual(g.fixed.map(i => i.id),         ['D']);
  assert.deepEqual(g.wontfix.map(i => i.id),       ['E']);
  assert.deepEqual(g.duplicate.map(i => i.id),     ['G']);
});

test('groupByStatus: unknown status is dropped, not crashed', () => {
  const g = groupByStatus([{ id: 'X', status: 'weird' }, { id: 'Y', status: 'constructor' }]);
  for (const list of Object.values(g)) assert.deepEqual(list, []);
});

test('groupBySeverity: partitions by severity into lower-case keys', () => {
  const g = groupBySeverity(issues);
  assert.deepEqual(Object.keys(g), ['critical', 'high', 'medium', 'low']);
  assert.deepEqual(g.critical.map(i => i.id), ['A']);
  assert.deepEqual(g.high.map(i => i.id),     ['B', 'C']);
  assert.deepEqual(g.medium.map(i => i.id),   ['D', 'G']);
  assert.deepEqual(g.low.map(i => i.id),      ['E']);
});

test('groupBySeverity: an issue with only a severity code lands under its word', () => {
  assert.deepEqual(groupBySeverity([{ id: 'P', severity: 'P2' }]).medium.map(i => i.id), ['P']);
});

test('groupBySeverity: missing or unknown severity is dropped', () => {
  const g = groupBySeverity([{ id: 'F', status: 'open' }, { id: 'U', severity: 'someday' }]);
  assert.equal(g.critical.length + g.high.length + g.medium.length + g.low.length, 0);
});
