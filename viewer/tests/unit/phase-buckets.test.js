import test from 'node:test';
import assert from 'node:assert/strict';
import { bucketPhases } from '../../js/lib/phase-buckets.js';

test('bucketPhases — splits done/active/future/archived correctly', () => {
  const phases = [
    { id: 'p1', status: 'done' },
    { id: 'p2', status: 'archived' },
    { id: 'p3', status: 'active' },
    { id: 'p4', status: 'planned' },
    { id: 'p5', status: 'future' },
  ];
  const out = bucketPhases(phases);
  assert.deepEqual(out.past.map(p => p.id),     ['p1']);
  assert.deepEqual(out.active?.id,              'p3');
  assert.deepEqual(out.future.map(p => p.id),   ['p4', 'p5']);
  assert.deepEqual(out.archived.map(p => p.id), ['p2']);
});

test('bucketPhases — archived between done and active is still archived (not past)', () => {
  const phases = [
    { id: 'p1', status: 'done' },
    { id: 'p2', status: 'archived' },
    { id: 'p3', status: 'done' },
    { id: 'p4', status: 'active' },
  ];
  const out = bucketPhases(phases);
  assert.deepEqual(out.past.map(p => p.id),     ['p1', 'p3']);
  assert.deepEqual(out.archived.map(p => p.id), ['p2']);
  assert.equal(out.active.id, 'p4');
});

test('bucketPhases — no active phase: past = done, future = planned/future, archived stays separate', () => {
  const phases = [
    { id: 'p1', status: 'done' },
    { id: 'p2', status: 'archived' },
    { id: 'p3', status: 'planned' },
  ];
  const out = bucketPhases(phases);
  assert.deepEqual(out.past.map(p => p.id),     ['p1']);
  assert.deepEqual(out.future.map(p => p.id),   ['p3']);
  assert.deepEqual(out.archived.map(p => p.id), ['p2']);
  assert.equal(out.active, null);
});

test('bucketPhases — case-insensitive status', () => {
  const phases = [
    { id: 'p1', status: 'ARCHIVED' },
    { id: 'p2', status: 'Active' },
  ];
  const out = bucketPhases(phases);
  assert.deepEqual(out.archived.map(p => p.id), ['p1']);
  assert.equal(out.active.id, 'p2');
});

test('bucketPhases — empty input returns empty buckets', () => {
  const out = bucketPhases([]);
  assert.deepEqual(out.past, []);
  assert.deepEqual(out.future, []);
  assert.deepEqual(out.archived, []);
  assert.equal(out.active, null);
});
