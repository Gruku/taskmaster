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

test('bucketPhases — non-array / nullish input returns empty buckets without throwing', () => {
  for (const input of [null, undefined, 'not-an-array', 42, {}]) {
    const out = bucketPhases(input);
    assert.deepEqual(out.past, []);
    assert.deepEqual(out.future, []);
    assert.deepEqual(out.archived, []);
    assert.equal(out.active, null);
  }
});

test('bucketPhases — slice-based split: phases after active land in future regardless of status', () => {
  // Documented contract: with an active phase present, the function preserves
  // input order via slice. A 'done' phase positioned after 'active' is still
  // bucketed into future. Real backlogs follow a linear phase progression so
  // this case is rare, but the contract is explicit.
  const phases = [
    { id: 'p1', status: 'done' },
    { id: 'p2', status: 'active' },
    { id: 'p3', status: 'done' },        // out-of-order; lands in future
    { id: 'p4', status: 'planned' },
  ];
  const out = bucketPhases(phases);
  assert.deepEqual(out.past.map(p => p.id),   ['p1']);
  assert.equal(out.active.id, 'p2');
  assert.deepEqual(out.future.map(p => p.id), ['p3', 'p4']);
  assert.deepEqual(out.archived, []);
});
