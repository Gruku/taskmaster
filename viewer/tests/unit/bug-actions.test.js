// User intent: the Bug page's actions are in-app forms — a task is checked against the board before anything is sent, a
// fix needs its commit, a promotion starts from the bug — and no browser dialog is ever used.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';

const PAGE = '<div class="shell"><section id="screen-mount"></section></div><div id="modal-host"></div>';
const dom = new JSDOM(`<!doctype html><html><body>${PAGE}</body></html>`);
globalThis.document = dom.window.document;
globalThis.window = dom.window;
for (const k of ['HTMLElement', 'Node', 'Event', 'KeyboardEvent', 'MutationObserver', 'getComputedStyle']) {
  if (!(k in globalThis)) globalThis[k] = dom.window[k];
}
const native = [];
for (const name of ['confirm', 'alert', 'prompt']) dom.window[name] = (...a) => { native.push([name, ...a]); return true; };

const { openModalCount } = await import('../../js/components/modal.js');
const { openMarkFixed, openAdopt, openPromote } = await import('../../js/components/edit/bug-actions.js');

const BUG = { id: 'B-031', title: 'Board drops a card', severity: 'P2', status: 'open' };
const BACKLOG = { tasks: [{ id: 'T-101', title: 'Fix the board' }] };

const seen = [];
globalThis.fetch = async (path, init) => {
  seen.push({ path, init });
  return new Response(JSON.stringify({ ok: true }), { status: 200, headers: { 'Content-Type': 'application/json' } });
};

const tick = (ms = 0) => new Promise((ok) => setTimeout(ok, ms));
const form = () => document.querySelector('.modal--form');
const field = (key) => form().querySelector(`[data-key="${key}"]`);
const control = (key) => document.getElementById(field(key).querySelector('label').getAttribute('for'));
const saveBtn = () => form().querySelector('[data-save]');
const type = (key, text) => {
  const c = control(key);
  c.value = text;
  c.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
};

test.beforeEach(() => {
  assert.equal(openModalCount(), 0, 'the previous test left a modal open');
  document.body.innerHTML = PAGE;
  native.length = 0;
  seen.length = 0;
});
test.afterEach(() => { assert.deepEqual(native, [], 'no native dialog was used'); });

test('Adopt refuses a task that is not on the board and sends nothing, then adopts one that is', async () => {
  let done = 0;
  openAdopt({ bug: BUG, getBacklog: () => BACKLOG, onDone: () => { done += 1; } });
  assert.equal(form().querySelector('.modal-title').textContent, 'Adopt into a task');
  type('adopted_into', 'T-999');
  saveBtn().click();
  await tick(10);
  assert.match(field('adopted_into').textContent, /No task T-999 on the board/);
  assert.equal(seen.length, 0);
  type('adopted_into', 'T-101');
  saveBtn().click();
  await tick(10);
  assert.equal(seen.length, 1);
  assert.equal(seen[0].path, '/api/bugs/B-031');
  assert.deepEqual(JSON.parse(seen[0].init.body), { status: 'adopted', adopted_into: 'T-101' });
  assert.equal(done, 1);
  assert.equal(openModalCount(), 0);
});

test('Mark fixed keeps Save disabled until a commit is typed, then sends it', async () => {
  openMarkFixed({ bug: BUG, onDone: () => {} });
  assert.equal(form().querySelector('.modal-eyebrow').textContent, 'B-031');
  assert.equal(saveBtn().disabled, true);
  type('fix_commit', 'abc1234');
  assert.equal(saveBtn().disabled, false);
  assert.equal(saveBtn().textContent, 'Mark fixed');
  saveBtn().click();
  await tick(10);
  assert.deepEqual(JSON.parse(seen[0].init.body), { status: 'fixed', fix_commit: 'abc1234' });
  assert.equal(openModalCount(), 0);
});

test('Promote starts from the bug: its title and severity are prefilled, labelled by severity name', async () => {
  let issue;
  openPromote({ bug: BUG, onDone: (id) => { issue = id; } });
  assert.equal(control('title').value, BUG.title);
  assert.equal(control('severity').value, 'P2');
  assert.deepEqual([...control('severity').options].map((o) => o.textContent).filter(Boolean).slice(-4),
    ['Critical', 'High', 'Medium', 'Low']);
  globalThis.fetch = async (path, init) => {
    seen.push({ path, init });
    return new Response(JSON.stringify({ ok: true, issue_id: 'ISS-030' }), { status: 201, headers: { 'Content-Type': 'application/json' } });
  };
  type('evidence_text', 'Recurring: 3 bugs');
  saveBtn().click();
  await tick(10);
  assert.equal(seen[0].path, '/api/bugs/promote');
  const sent = JSON.parse(seen[0].init.body);
  assert.deepEqual([sent.bug_ids, sent.title, sent.severity, sent.evidence_text], [['B-031'], BUG.title, 'P2', 'Recurring: 3 bugs']);
  assert.equal(issue, 'ISS-030');
  assert.equal(openModalCount(), 0);
});

test('no browser dialog anywhere in the bug actions or the Bug page', () => {
  for (const rel of ['../../js/components/edit/bug-actions.js', '../../js/screens/bug-detail.js']) {
    const src = readFileSync(new URL(rel, import.meta.url), 'utf8');
    assert.doesNotMatch(src, /\b(prompt|confirm|alert)\(/, rel);
  }
});
