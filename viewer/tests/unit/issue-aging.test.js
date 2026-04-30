import { test } from 'node:test';
import assert from 'node:assert/strict';
import { computeAgingTier } from '../../js/components/aging-bar.js';

const cfg = { Critical: 14, High: 30, Medium: 60, Low: 120 };

test('computeAgingTier: Fresh band 0-25%', () => {
  const now = new Date('2026-04-26T00:00:00Z');
  const discovered = new Date('2026-04-23T00:00:00Z').toISOString();
  const out = computeAgingTier({ discovered, severity_label: 'High' }, cfg, now);
  assert.equal(out.tier, 'Fresh');
  assert.ok(out.percent >= 0 && out.percent < 25, `percent=${out.percent}`);
});

test('computeAgingTier: Aging band 25-60%', () => {
  const now = new Date('2026-04-26T00:00:00Z');
  const discovered = new Date('2026-04-14T00:00:00Z').toISOString();
  const out = computeAgingTier({ discovered, severity_label: 'High' }, cfg, now);
  assert.equal(out.tier, 'Aging');
  assert.ok(out.percent >= 25 && out.percent < 60, `percent=${out.percent}`);
});

test('computeAgingTier: Stale band 60+%', () => {
  const now = new Date('2026-04-26T00:00:00Z');
  const discovered = new Date('2026-04-01T00:00:00Z').toISOString();
  const out = computeAgingTier({ discovered, severity_label: 'High' }, cfg, now);
  assert.equal(out.tier, 'Stale');
  assert.ok(out.percent >= 60, `percent=${out.percent}`);
});

test('computeAgingTier: Critical decays faster than Low at same age', () => {
  const now = new Date('2026-04-26T00:00:00Z');
  const discovered = new Date('2026-04-16T00:00:00Z').toISOString();
  const crit = computeAgingTier({ discovered, severity_label: 'Critical' }, cfg, now);
  const low  = computeAgingTier({ discovered, severity_label: 'Low' }, cfg, now);
  assert.ok(crit.percent > low.percent, `crit=${crit.percent} low=${low.percent}`);
});
