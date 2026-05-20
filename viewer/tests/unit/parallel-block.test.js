import test from 'node:test';
import assert from 'node:assert/strict';
import { clusterParallelSessions } from '../../js/components/timeline.js';

test('non-overlapping sessions: each is its own cluster (DESC order across clusters)', () => {
  const sessions = [
    { id: 'SES-1', start: '2026-04-26T10:00:00Z', end: '2026-04-26T11:00:00Z' },
    { id: 'SES-2', start: '2026-04-26T12:00:00Z', end: '2026-04-26T13:00:00Z' },
  ];
  const groups = clusterParallelSessions(sessions);
  // Cluster order is newest-first across clusters (SES-2 starts later).
  assert.deepEqual(groups.map(g => g.map(s => s.id)), [['SES-2'], ['SES-1']]);
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
  // Cluster order is newest-first across clusters (C is newer than the A+B
  // cluster). Within the A+B cluster, member order stays ASC by start.
  assert.deepEqual(groups[0].map(s => s.id), ['C']);
  assert.deepEqual(groups[1].map(s => s.id), ['A', 'B']);
});

test('preserves server DESC order: newest cluster first', () => {
  // Server returns sessions sorted DESC by start (taskmaster_v3.py:3884).
  // The viewer must NOT silently re-sort them ASC.
  const sessions = [
    { id: 'NEW', start: '2026-05-19T15:00:00Z', end: '2026-05-19T16:00:00Z' },
    { id: 'MID', start: '2026-05-19T12:00:00Z', end: '2026-05-19T13:00:00Z' },
    { id: 'OLD', start: '2026-05-18T09:00:00Z', end: '2026-05-18T10:00:00Z' },
  ];
  const groups = clusterParallelSessions(sessions);
  assert.deepEqual(
    groups.map(g => g.map(s => s.id)),
    [['NEW'], ['MID'], ['OLD']],
    'clusters must appear in DESC-by-start order (newest first)',
  );
});

test('within an overlapping cluster, members render in ascending start order', () => {
  // Two sessions overlap; whichever started first should be the first
  // grid column inside the parallel block so the visual reading order
  // (left-to-right) matches chronology. Cluster order (top-to-bottom
  // on screen) is still newest-first across clusters.
  const sessions = [
    // Newest cluster (overlapping pair) - server order: B then A.
    { id: 'B', start: '2026-05-19T15:30:00Z', end: '2026-05-19T16:30:00Z' },
    { id: 'A', start: '2026-05-19T15:00:00Z', end: '2026-05-19T16:00:00Z' },
    // Older standalone session.
    { id: 'OLD', start: '2026-05-18T09:00:00Z', end: '2026-05-18T10:00:00Z' },
  ];
  const groups = clusterParallelSessions(sessions);
  assert.equal(groups.length, 2, 'two clusters expected');
  assert.deepEqual(
    groups[0].map(s => s.id),
    ['A', 'B'],
    'within a cluster, members are ascending by start',
  );
  assert.deepEqual(groups[1].map(s => s.id), ['OLD']);
});
