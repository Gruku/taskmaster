import { test } from 'node:test';
import assert from 'node:assert/strict';
import { computeShelfPlacement } from '../../js/util/lesson-shelf.js';

const T = { core_count: 7, core_window_days: 60, core_recency_days: 14, retired_after_days: 30 };

function ev(daysAgo, now) {
  return { at: new Date(now.getTime() - daysAgo * 86_400_000).toISOString(), source: 'user', note: '' };
}

test('core: ≥7 in 60d AND fire in 14d', () => {
  const now = new Date('2026-04-26T00:00:00Z');
  const events = [1, 3, 5, 10, 18, 25, 40].map(d => ev(d, now));
  assert.equal(computeShelfPlacement({ reinforce_events: events }, T, now), 'core');
});

test('active: high volume but no recent fire → active, not core', () => {
  const now = new Date('2026-04-26T00:00:00Z');
  const events = [16, 18, 20, 22, 24, 26, 28, 29].map(d => ev(d, now));
  assert.equal(computeShelfPlacement({ reinforce_events: events }, T, now), 'active');
});

test('active: any fire within retired_after_days but below core volume', () => {
  const now = new Date('2026-04-26T00:00:00Z');
  const events = [ev(2, now)];
  assert.equal(computeShelfPlacement({ reinforce_events: events }, T, now), 'active');
});

test('retired: no fire in retired_after_days', () => {
  const now = new Date('2026-04-26T00:00:00Z');
  const events = [ev(45, now)];
  assert.equal(computeShelfPlacement({ reinforce_events: events }, T, now), 'retired');
});

test('retired: empty events list', () => {
  const now = new Date('2026-04-26T00:00:00Z');
  assert.equal(computeShelfPlacement({ reinforce_events: [] }, T, now), 'retired');
});
