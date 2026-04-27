import test from 'node:test';
import assert from 'node:assert/strict';
import { formatTimeInStatus, formatElapsed, classifyTimeInStatus, isoToMs } from '../../js/lib/time.js';

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
