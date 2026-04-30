import test from 'node:test';
import assert from 'node:assert/strict';
import { clusterParallelSessions } from '../../js/components/timeline.js';

test('non-overlapping sessions: each is its own cluster', () => {
  const sessions = [
    { id: 'SES-1', start: '2026-04-26T10:00:00Z', end: '2026-04-26T11:00:00Z' },
    { id: 'SES-2', start: '2026-04-26T12:00:00Z', end: '2026-04-26T13:00:00Z' },
  ];
  const groups = clusterParallelSessions(sessions);
  assert.deepEqual(groups.map(g => g.map(s => s.id)), [['SES-1'], ['SES-2']]);
});

test('overlapping pair: clustered together with parallel: true', () => {
  const sessions = [
    { id: 'SES-1', start: '2026-04-26T14:08:00Z', end: '2026-04-26T16:00:00Z' },
    { id: 'SES-2', start: '2026-04-26T15:00:00Z', end: '2026-04-26T16:42:00Z' },
  ];
  const groups = clusterParallelSessions(sessions);
  assert.equal(groups.length, 1);
  assert.equal(groups[0].length, 2);
});

test('three sessions, middle overlaps both: all in one cluster (transitive)', () => {
  const sessions = [
    { id: 'A', start: '2026-04-26T10:00:00Z', end: '2026-04-26T11:30:00Z' },
    { id: 'B', start: '2026-04-26T11:00:00Z', end: '2026-04-26T12:30:00Z' },
    { id: 'C', start: '2026-04-26T12:00:00Z', end: '2026-04-26T13:00:00Z' },
  ];
  const groups = clusterParallelSessions(sessions);
  assert.equal(groups.length, 1);
  assert.deepEqual(groups[0].map(s => s.id), ['A', 'B', 'C']);
});

test('cluster boundary: session that starts after all previous end gets its own group', () => {
  const sessions = [
    { id: 'A', start: '2026-04-26T10:00:00Z', end: '2026-04-26T11:00:00Z' },
    { id: 'B', start: '2026-04-26T10:30:00Z', end: '2026-04-26T11:15:00Z' },
    { id: 'C', start: '2026-04-26T13:00:00Z', end: '2026-04-26T14:00:00Z' },
  ];
  const groups = clusterParallelSessions(sessions);
  assert.equal(groups.length, 2);
  assert.deepEqual(groups[0].map(s => s.id), ['A', 'B']);
  assert.deepEqual(groups[1].map(s => s.id), ['C']);
});
