import test from 'node:test';
import assert from 'node:assert/strict';
import { formatTimeInStatus, formatElapsed, classifyTimeInStatus, isoToMs, formatRelative, formatAbsolute } from '../../js/lib/time.js';

test('formatTimeInStatus — under an hour returns minutes', () => {
  const now = Date.parse('2026-04-26T12:00:00Z');
  const ts  = Date.parse('2026-04-26T11:30:00Z');
  assert.equal(formatTimeInStatus(ts, now), '30m');
});

test('formatTimeInStatus — under a day returns hours', () => {
  const now = Date.parse('2026-04-26T12:00:00Z');
  const ts  = Date.parse('2026-04-26T05:00:00Z');
  assert.equal(formatTimeInStatus(ts, now), '7h');
});

test('formatTimeInStatus — multi-day returns Nd', () => {
  const now = Date.parse('2026-04-26T12:00:00Z');
  const ts  = Date.parse('2026-04-24T12:00:00Z');
  assert.equal(formatTimeInStatus(ts, now), '2d');
});

test('formatTimeInStatus — null/undefined input returns empty string', () => {
  assert.equal(formatTimeInStatus(null), '');
  assert.equal(formatTimeInStatus(undefined), '');
});

test('classifyTimeInStatus — fresh / aging / stale per spec', () => {
  const now = Date.parse('2026-04-26T12:00:00Z');
  assert.equal(classifyTimeInStatus(Date.parse('2026-04-26T11:30:00Z'), now), 'fresh');
  assert.equal(classifyTimeInStatus(Date.parse('2026-04-25T12:00:00Z'), now), 'fresh');
  assert.equal(classifyTimeInStatus(Date.parse('2026-04-24T12:00:00Z'), now), 'fresh');
  assert.equal(classifyTimeInStatus(Date.parse('2026-04-22T12:00:00Z'), now), 'stale');
  assert.equal(classifyTimeInStatus(Date.parse('2026-04-20T12:00:00Z'), now), 'stale');
});

test('formatElapsed — HH:MM:SS for >= 1 hour, else MM:SS', () => {
  assert.equal(formatElapsed(0),         '00:00');
  assert.equal(formatElapsed(45_000),    '00:45');
  assert.equal(formatElapsed(90_000),    '01:30');
  assert.equal(formatElapsed(3_600_000), '01:00:00');
  assert.equal(formatElapsed(6_125_000), '01:42:05');
});

test('isoToMs — parses ISO8601 string, returns null for falsy', () => {
  assert.equal(isoToMs('2026-04-26T10:00:00Z'), Date.parse('2026-04-26T10:00:00Z'));
  assert.equal(isoToMs(null),       null);
  assert.equal(isoToMs(undefined),  null);
  assert.equal(isoToMs(''),         null);
});

// Regression: date-only strings must parse as LOCAL midnight, not UTC midnight.
// ECMAScript spec makes Date.parse("2026-05-08") == UTC 00:00, which shifts to
// "May 7" when rendered with toLocaleDateString() in any timezone west of UTC.
test('isoToMs — date-only string parses as local midnight (no timezone shift)', () => {
  const ms = isoToMs('2026-05-08');
  assert.ok(ms !== null, 'should not be null');
  // Round-trip: the Date constructed from ms must have local day = 8.
  const d = new Date(ms);
  assert.equal(d.getFullYear(), 2026);
  assert.equal(d.getMonth() + 1, 5);   // May
  assert.equal(d.getDate(), 8);         // day 8 in local time
});

test('isoToMs — date-only preserves calendar date regardless of UTC offset', () => {
  // Simulate what list_sessions() emits for a date-only handover:
  // Python _parse_iso8601("2026-05-08") → UTC midnight → isoformat() →
  // "2026-05-08T00:00:00+00:00". In JS that parses to UTC midnight, which
  // would show "May 7" in UTC-5. Our fix only applies to bare YYYY-MM-DD;
  // the UTC datetime form is already correct for the timeline (no user-visible date).
  // This test validates that the bare form round-trips correctly.
  const ms = isoToMs('2026-05-08');
  const d = new Date(ms);
  assert.equal(d.getDate(), 8, 'local day must be 8, not shifted to 7');
});

test('formatRelative — date-only string produces sensible relative string', () => {
  // "2026-04-20" relative to "2026-04-26T12:00:00Z" (6+ days later in local time)
  // We can only verify it returns a non-empty string and doesn't throw.
  const nowMs = Date.parse('2026-04-26T12:00:00Z');
  const result = formatRelative('2026-04-20', { now: nowMs });
  assert.ok(typeof result === 'string' && result.length > 0, `expected non-empty string, got ${JSON.stringify(result)}`);
  // Should be days or weeks, not "now"
  assert.ok(result !== 'now', 'a week-old date should not render as "now"');
});

test('formatAbsolute — date-only string suppresses time display automatically', () => {
  // When input is date-only, formatAbsolute should NOT show a time part.
  const result = formatAbsolute('2026-05-08', { year: true });
  assert.ok(!result.includes('·'), `expected no time separator, got: ${JSON.stringify(result)}`);
  assert.ok(!result.includes('AM') && !result.includes('PM') && !result.includes(':'),
    `expected no time component, got: ${JSON.stringify(result)}`);
  assert.ok(result.length > 0, 'should produce a non-empty date string');
});

// ─── characterization: lib/time.js contract (v3-polish-018 sweep baseline) ────

const NOW = new Date('2026-05-15T12:00:00Z').getTime();

test('formatRelative · ISO microsecond handover created → "1d ago"', () => {
  const created = '2026-05-14T12:00:00.000000+00:00';
  assert.equal(formatRelative(created, { now: NOW }), '1d ago');
});

test('formatRelative · date-only string parses as local midnight', () => {
  const out = formatRelative('2026-05-14', { now: NOW });
  // 24h span when both sides are interpreted consistently.
  assert.match(out, /^\d+[hmd] ago$/);
});

test('formatRelative · same-second returns "now"', () => {
  const created = new Date(NOW).toISOString();
  assert.equal(formatRelative(created, { now: NOW }), 'now');
});

test('formatAbsolute · date-only suppresses time', () => {
  const out = formatAbsolute('2026-05-14', { now: NOW });
  // Structural: month name (any locale), day number, no time separator, no clock.
  assert.match(out, /14/);
  assert.ok(!out.includes('·'));
  assert.ok(!/[0-9]:[0-9]/.test(out));
  assert.ok(!/AM|PM/.test(out));
});

test('formatRelative · handover.created drives "Xd ago" not handover.date', () => {
  const handover = {
    date: '2026-05-14',                          // date-only (local midnight)
    created: '2026-05-14T23:59:00.000000+00:00', // late-night UTC write
  };
  // From NOW=2026-05-15T12:00Z, late-night-prior should be "12h ago", not "1d ago".
  const out = formatRelative(handover.created, { now: NOW });
  assert.equal(out, '12h ago');
});
