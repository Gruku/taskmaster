import { test } from 'node:test';
import assert from 'node:assert/strict';
import { buildRails, sortNotes, tiltFor, firstLine, RAIL_CAP, MAX_AGE_DAYS } from '../../js/lib/desk.js';

const item = (over = {}) => ({
  id: 'X', type: 'task', title: 't', action_class: 'resume',
  age_days: 1, timestamp: '2026-06-09T00:00:00Z', ...over,
});

test('buildRails groups by action_class and caps at RAIL_CAP', () => {
  const items = Array.from({ length: 9 }, (_, i) =>
    item({ id: `T-${i}`, age_days: i }));
  const rails = buildRails(items);
  assert.equal(rails.resume.items.length, RAIL_CAP);
  assert.equal(rails.resume.older, 9 - RAIL_CAP);
  // freshest first
  assert.equal(rails.resume.items[0].id, 'T-0');
});

test('buildRails excludes items older than MAX_AGE_DAYS from cards', () => {
  const rails = buildRails([
    item({ id: 'fresh', age_days: 2 }),
    item({ id: 'stale', age_days: MAX_AGE_DAYS + 5 }),
  ]);
  assert.deepEqual(rails.resume.items.map(i => i.id), ['fresh']);
  assert.equal(rails.resume.older, 1);
});

test('buildRails routes decide/review/clean-up and ignores ambient', () => {
  const rails = buildRails([
    item({ id: 'd', action_class: 'decide', type: 'decision' }),
    item({ id: 'r', action_class: 'review' }),
    item({ id: 'c', action_class: 'clean-up' }),
    item({ id: 'a', action_class: 'ambient' }),
  ]);
  assert.equal(rails.decide.items.length, 1);
  assert.equal(rails.review.items.length, 1);
  assert.equal(rails.cleanup.items.length, 1);
  assert.equal(rails.resume.items.length, 0);
});

test('sortNotes: pinned first, then created desc', () => {
  const notes = [
    { id: 'NOTE-001', pinned: false, created: '2026-06-01T00:00:00Z' },
    { id: 'NOTE-002', pinned: true,  created: '2026-05-01T00:00:00Z' },
    { id: 'NOTE-003', pinned: false, created: '2026-06-05T00:00:00Z' },
  ];
  assert.deepEqual(sortNotes(notes).map(n => n.id), ['NOTE-002', 'NOTE-003', 'NOTE-001']);
});

test('tiltFor is deterministic and bounded', () => {
  assert.equal(tiltFor('NOTE-001'), tiltFor('NOTE-001'));
  for (const id of ['NOTE-001', 'NOTE-002', 'NOTE-017', 'NOTE-123']) {
    const t = tiltFor(id);
    assert.ok(Math.abs(t) <= 1.2, `${id} tilt ${t} out of range`);
  }
  // not all identical
  const tilts = new Set(['NOTE-001','NOTE-002','NOTE-003','NOTE-004'].map(tiltFor));
  assert.ok(tilts.size > 1);
});

test('tiltFor spreads sequential ids (the common case) across the range', () => {
  // Without avalanche mixing, NOTE-001..NOTE-010 cluster within ~0.05deg and
  // the board loses its scattered-paper look.
  const tilts = Array.from({ length: 10 }, (_, i) =>
    tiltFor(`NOTE-${String(i + 1).padStart(3, '0')}`));
  assert.equal(new Set(tilts).size, 10);
  assert.ok(Math.max(...tilts) - Math.min(...tilts) >= 0.5,
    `sequential ids too clustered: ${tilts.join(', ')}`);
});

test('firstLine returns the first non-empty line, stripped of markdown heading', () => {
  assert.equal(firstLine('# Hello\nworld'), 'Hello');
  assert.equal(firstLine('\n\n  plain text  \nmore'), 'plain text');
  assert.equal(firstLine(''), '');
});
