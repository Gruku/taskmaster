// User intent: an issue shows "stale Nd" only while it is still open and past the aging window for its severity — a
// resolved issue, a fresh one or one with no discovery date never carries the tag.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const { staleDays, staleTag } = await import('../../js/components/stale-tag.js');

const now = Date.parse('2026-10-06T00:00:00Z');
const issue = (extra = {}) => ({ id: 'ISS-1', status: 'open', severity: 'P1', discovered: '2026-08-22', ...extra });

test('an open issue the server marks Stale carries "stale Nd" as a warning marker', () => {
  const el = staleTag(issue({ aging: { tier: 'Stale' } }), {}, now);
  assert.ok(el);
  assert.equal(el.tagName, 'SPAN');
  assert.ok(el.classList.contains('marker'));
  assert.ok(el.classList.contains('marker--warning'));
  assert.ok(el.classList.contains('stale-tag'));
  assert.equal(el.querySelector('.marker__word').textContent, 'stale 45d');
  assert.equal(el.querySelector('.marker__shape').dataset.shape, 'triangle');
  assert.equal(el.querySelector('.marker__shape').getAttribute('aria-hidden'), 'true');
  assert.ok(el.title.includes('45 days'), el.title);
  assert.equal(el.title, 'Open 45 days — past the aging window for its severity');
});

test('an investigating issue that is Stale carries the tag too', () => {
  assert.ok(staleTag(issue({ status: 'investigating', aging: { tier: 'Stale' } }), {}, now));
});

test('the server tier wins: Fresh or Aging means no tag, whatever the local window says', () => {
  assert.equal(staleTag(issue({ aging: { tier: 'Fresh' } }), { High: 1 }, now), null);
  assert.equal(staleTag(issue({ aging: { tier: 'Aging' } }), { High: 1 }, now), null);
});

test('a resolved issue never carries the tag', () => {
  for (const status of ['fixed', 'wontfix', 'duplicate', undefined]) {
    assert.equal(staleTag(issue({ status, aging: { tier: 'Stale' } }), {}, now), null, String(status));
  }
});

test('without a server tier the aging window for its severity decides', () => {
  const el = staleTag(issue(), { High: 30 }, now);
  assert.ok(el);
  assert.equal(el.querySelector('.marker__word').textContent, 'stale 45d');
  // 45 days is well inside a 365-day High window.
  assert.equal(staleTag(issue(), { High: 365 }, now), null);
  // A severity the viewer does not know is aged as Medium (60 days by default): 45 of 60 is Stale.
  assert.ok(staleTag(issue({ severity: 'blocker' }), {}, now));
  assert.equal(staleTag(issue({ severity: 'blocker' }), { Medium: 365 }, now), null);
});

test('no discovery date means no tag', () => {
  assert.equal(staleTag(issue({ discovered: undefined, aging: { tier: 'Stale' } }), {}, now), null);
  assert.equal(staleTag(issue({ discovered: undefined }), { High: 1 }, now), null);
  assert.equal(staleTag(issue({ discovered: 'not a date', aging: { tier: 'Stale' } }), {}, now), null);
});

test('staleDays counts whole days since discovery, falling back to created', () => {
  assert.equal(staleDays({ discovered: '2026-10-01' }, now), 5);
  assert.equal(staleDays({ created: '2026-10-01' }, now), 5);
  assert.equal(staleDays({ discovered: '2026-10-05T12:00:00Z' }, now), 0);
  assert.equal(staleDays({}, now), null);
  assert.equal(staleDays(null, now), null);
  assert.equal(staleDays({ discovered: 'garbage' }, now), null);
});
