// User intent: pin a bug in the list as a link row — id, severity marker (nothing when unset), a two-line title, status
// marker, the archived tag, components, age, and "found in" beside the link — with no markup ever read from bug data.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const { bugRow } = await import('../../js/components/bug-card.js');
const { formatStamp } = await import('../../js/lib/time.js');

const now = Date.parse('2026-10-06T00:00:00Z');
const bug = (extra = {}) => ({
  id: 'B-031', title: 'Card edge vanishes on the light ground', status: 'open', severity: 'P1',
  found_in: 'T-102', components: ['viewer', 'store'], discovered: '2026-10-04T00:00:00Z', ...extra,
});
const parts = (el) => ({
  link: el.querySelector(':scope > .link-row__link'),
  content: el.querySelector(':scope > .link-row__content'),
  controls: el.querySelector(':scope > .link-row__controls'),
});

test('1: an li link row to the bug, archived ones marked by class', () => {
  const el = bugRow(bug(), { now });
  assert.equal(el.tagName, 'LI');
  assert.ok(el.classList.contains('link-row'));
  assert.ok(el.classList.contains('bug-row'));
  assert.ok(!el.classList.contains('bug-row--archived'));
  assert.equal(parts(el).link.getAttribute('href'), '#/bug/B-031');
  assert.ok(bugRow(bug({ archived: true }), { now }).classList.contains('bug-row--archived'));
  assert.ok(bugRow(bug({ status: 'archived' }), { now }).classList.contains('bug-row--archived'));
});

test('2: the name is id, severity marker, two-line title', () => {
  const { link } = parts(bugRow(bug(), { now }));
  const name = link.querySelector(':scope > .bug-row__name');
  assert.ok(name);
  const [id, sev, title] = name.children;
  assert.ok(id.classList.contains('bug-row__id'));
  assert.equal(id.textContent, 'B-031');
  assert.ok(sev.classList.contains('bug-row__severity'));
  assert.equal(sev.querySelector('.marker .marker__word').textContent, 'High');
  assert.ok(!sev.hasAttribute('aria-hidden'));
  assert.ok(title.classList.contains('bug-row__title'));
  assert.ok(title.classList.contains('truncate--2'));
  assert.equal(title.textContent, 'Card edge vanishes on the light ground');
  assert.equal(title.title, 'Card edge vanishes on the light ground');
  assert.equal(bugRow(bug({ title: '' }), { now }).querySelector('.bug-row__title').textContent, 'Untitled');
});

test('3: content is status marker, archived tag, components, age', () => {
  const { content } = parts(bugRow(bug({ archived: true }), { now }));
  const [status, tag, comps, age] = content.children;
  assert.ok(status.classList.contains('marker'));
  assert.equal(status.querySelector('.marker__word').textContent, 'Open');
  assert.ok(tag.classList.contains('list-tag'));
  assert.equal(tag.textContent, 'Archived');
  assert.ok(comps.classList.contains('bug-row__components'));
  assert.equal(comps.textContent, 'viewer, store');
  assert.equal(comps.title, 'viewer, store');
  assert.equal(age.tagName, 'TIME');
  assert.ok(age.classList.contains('bug-row__age'));
  assert.equal(age.getAttribute('datetime'), '2026-10-04T00:00:00Z');
  const stamp = formatStamp('2026-10-04T00:00:00Z', now);
  assert.equal(age.textContent, stamp.text);
  assert.equal(age.title, stamp.title);

  const bare = parts(bugRow({ id: 'B-1', title: 'x' }, { now })).content;
  assert.equal(bare.children.length, 1);
  assert.equal(bare.querySelector('.marker__word').textContent, 'Open');
});

test('4: found in is a link to the task beside the row link; none without found_in', () => {
  const { controls, link } = parts(bugRow(bug(), { now }));
  const a = controls.querySelector('a.bug-row__found-in');
  assert.equal(a.getAttribute('href'), '#/task/T-102');
  assert.equal(a.textContent, 'found in T-102');
  assert.ok(!link.contains(a));
  assert.equal(parts(bugRow(bug({ found_in: undefined }), { now })).controls, null);
});

test('an unset severity is an empty, hidden cell; the row has one marker, its status', () => {
  const el = bugRow({ id: 'B-030', title: 'Phase strip clips the current phase name', status: 'open', found_in: 'T-102' }, { now });
  const sev = el.querySelector('.bug-row__severity');
  assert.equal(sev.getAttribute('aria-hidden'), 'true');
  assert.equal(sev.childNodes.length, 0);
  assert.equal(el.querySelectorAll('.marker').length, 1);
});

test('no markup from bug data', () => {
  const el = bugRow(bug({ title: '<img src=x onerror=alert(1)>', components: ['<img src=y>'], severity: '<img src=z>' }), { now });
  assert.equal(el.querySelectorAll('img').length, 0);
  assert.equal(el.querySelector('.bug-row__title').textContent, '<img src=x onerror=alert(1)>');
});

test('the id is encoded in the href', () => {
  assert.equal(parts(bugRow(bug({ id: 'B 1' }), { now })).link.getAttribute('href'), '#/bug/B%201');
});
