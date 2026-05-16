import { test } from 'node:test';
import assert from 'node:assert/strict';
import { groupByAction, pickHero } from '../../js/lib/continuity.js';

const FIXTURE = [
  { id: 'DEC-001', type: 'decision', title: 'a', action_class: 'decide', age_days: 1 },
  { id: 'DEC-003', type: 'decision', title: 'b', action_class: 'decide', age_days: 4 },
  { id: 'h1',  type: 'handover', title: 'resume', action_class: 'resume', age_days: 1 },
  { id: 't1',  type: 'task',     title: 'review', action_class: 'review', age_days: 0 },
  { id: 'br1', type: 'branch',   title: 'cold',   action_class: 'clean-up', age_days: 9 },
  { id: 'i1',  type: 'idea',     title: 'amb',    action_class: 'ambient', age_days: 30 },
];

test('groupByAction yields 4 rails in the right order', () => {
  const g = groupByAction(FIXTURE);
  assert.deepEqual(Object.keys(g), ['decide', 'resume', 'review', 'clean-up']);
  assert.equal(g['decide'].length, 2);
  assert.equal(g['resume'].length, 1);
  assert.equal(g['review'].length, 1);
  assert.equal(g['clean-up'].length, 1);
});

test('pickHero returns oldest open decision when present', () => {
  const hero = pickHero(FIXTURE);
  assert.equal(hero?.id, 'DEC-003');
});

test('pickHero falls back to newest resume handover when no decisions', () => {
  const filtered = FIXTURE.filter(i => i.action_class !== 'decide');
  const hero = pickHero(filtered);
  assert.equal(hero?.id, 'h1');
});

test('pickHero returns null when nothing to surface', () => {
  assert.equal(pickHero([]), null);
});
