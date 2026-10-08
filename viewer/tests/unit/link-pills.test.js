// User intent: a link between entities reads as "what it is, then which one" and goes somewhere real — and an id that
// came from task data can never become markup.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { linkPillsEl, linkRoute, renderLinkPills } from '../../js/components/link-pills.js';

const dom = new JSDOM('<!doctype html><body></body>');
global.document = dom.window.document;

test('a pill is a link holding a label, a space and the id; known types come in one fixed order', () => {
  const el = linkPillsEl([{ type: 'relates_to', target: 'ISS-012' }, { type: 'fixes', target: 'B-7' }]);
  const pills = [...el.querySelectorAll('a.link-pill')];
  assert.deepEqual(pills.map((a) => a.textContent), ['Fixes B-7', 'Related ISS-012']);
  assert.equal(pills[1].querySelector('.link-pill__label').textContent, 'Related');
  assert.equal(pills[1].querySelector('.link-pill__id').textContent, 'ISS-012');
  assert.equal(pills[1].getAttribute('href'), '#/issue/ISS-012');
  assert.equal(pills[0].getAttribute('href'), '#/bug/B-7');
});

test('an id routes by what it names; anything else is a task', () => {
  assert.equal(linkRoute('T-101'), '#/task/T-101');
  assert.equal(linkRoute('viewer-rr-004'), '#/task/viewer-rr-004');
  assert.equal(linkRoute('ISS-3'), '#/issue/ISS-3');
  assert.equal(linkRoute('B-082'), '#/bug/B-082');
  assert.equal(linkRoute('IDEA-9'), '#/ideas/IDEA-9');
  assert.equal(linkRoute('a b/c'), '#/task/a%20b%2Fc');
});

test('no links gives nothing to mount', () => {
  assert.equal(linkPillsEl([]), null);
  assert.equal(linkPillsEl(null), null);
  assert.equal(renderLinkPills({ links: [] }), '');
  assert.equal(renderLinkPills({}), '');
});

test('entries that are not links are skipped, and an unknown type is shown by its name', () => {
  const el = linkPillsEl([null, 'T-1', { type: 'mirrors', target: 'T-5' }, { type: 'fixes' }, { type: 'fixes', target: 42 }, { type: 'fixes', target: { id: 'T-9' } }]);
  const pills = [...el.querySelectorAll('a.link-pill')];
  assert.deepEqual(pills.map((a) => a.textContent), ['Fixes 42', 'mirrors T-5']);
});

test('the string form escapes ids and labels', () => {
  const html = renderLinkPills({ links: [{ type: 'relates_to', target: '"><img src=x onerror=alert(1)>' }] });
  const host = document.createElement('div');
  host.innerHTML = html;
  assert.equal(host.querySelector('img'), null);
  assert.equal(host.querySelectorAll('a.link-pill').length, 1);
  assert.equal(host.querySelector('.link-pill__id').textContent, '"><img src=x onerror=alert(1)>');
  // The screens that still mount the string rewrite `#<id>` themselves.
  assert.equal(host.querySelector('a').getAttribute('href'), '#"><img src=x onerror=alert(1)>');
});
