import test from 'node:test';
import assert from 'node:assert/strict';
import { applyIdeasFilters } from '../../js/screens/ideas.js';

const SAMPLE = [
  { id: 'IDEA-001', title: 'A', status: 'exploring',   tags: ['perf'],         archived: false, created: '2026-05-01T00:00:00Z' },
  { id: 'IDEA-002', title: 'B', status: 'parking-lot',  tags: ['ux'],           archived: false, created: '2026-05-02T00:00:00Z' },
  { id: 'IDEA-003', title: 'C', status: 'candidate',    tags: ['perf', 'ai'],   archived: false, created: '2026-05-03T00:00:00Z' },
  { id: 'IDEA-004', title: 'D', status: 'parking-lot',  tags: [],               archived: true,  created: '2026-05-04T00:00:00Z' },
];

test('applyIdeasFilters — defaults: non-archived, newest first', () => {
  const out = applyIdeasFilters(SAMPLE, { statuses: [], tags: [], includeArchived: false });
  assert.deepEqual(out.map(i => i.id), ['IDEA-003', 'IDEA-002', 'IDEA-001']);
});

test('applyIdeasFilters — single status', () => {
  const out = applyIdeasFilters(SAMPLE, { statuses: ['exploring'], tags: [], includeArchived: false });
  assert.deepEqual(out.map(i => i.id), ['IDEA-001']);
});

test('applyIdeasFilters — multiple statuses (OR)', () => {
  const out = applyIdeasFilters(SAMPLE, { statuses: ['exploring', 'candidate'], tags: [], includeArchived: false });
  assert.deepEqual(out.map(i => i.id), ['IDEA-003', 'IDEA-001']);
});

test('applyIdeasFilters — single tag', () => {
  const out = applyIdeasFilters(SAMPLE, { statuses: [], tags: ['perf'], includeArchived: false });
  assert.deepEqual(out.map(i => i.id), ['IDEA-003', 'IDEA-001']);
});

test('applyIdeasFilters — status + tag AND', () => {
  const out = applyIdeasFilters(SAMPLE, { statuses: ['candidate'], tags: ['perf'], includeArchived: false });
  assert.deepEqual(out.map(i => i.id), ['IDEA-003']);
});

test('applyIdeasFilters — includeArchived returns all', () => {
  const out = applyIdeasFilters(SAMPLE, { statuses: [], tags: [], includeArchived: true });
  assert.deepEqual(out.map(i => i.id), ['IDEA-004', 'IDEA-003', 'IDEA-002', 'IDEA-001']);
});
