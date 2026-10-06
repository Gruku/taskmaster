// User intent: a Dashboard continuity row is exactly one kind of control — a link for what has a page, a disclosure
// for what opens in place, nothing for the rest — and a title is never read as markup.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const { window } = new JSDOM('<!DOCTYPE html><div id="r"></div>');
globalThis.document = window.document;

const { createItemRow, ITEM_ROUTE } = await import('../../js/components/continuity/item-row.js');

const base = { title: 'A row', timestamp: new Date(Date.now() - 86_400_000).toISOString(), next: 'Next step', where: 'feat/x' };
const row = (item) => {
  const r = createItemRow({ item: { ...base, ...item } });
  document.body.appendChild(r.root);
  return r;
};

test('a task item is a link row to its task, holding no other control', () => {
  const { root } = row({ type: 'task', id: 'T-102' });
  assert.ok(root.classList.contains('link-row'));
  assert.ok(root.classList.contains('co-row'));
  const links = root.querySelectorAll('a');
  assert.equal(links.length, 1);
  assert.equal(links[0].getAttribute('href'), '#/task/T-102');
  assert.equal(root.querySelectorAll('button, [tabindex], input, select, textarea').length, 0);
  assert.equal(root.querySelector('.co-chip').textContent, 'Task');
});

test('an issue links to its page and an idea to the ideas list', () => {
  assert.equal(row({ type: 'issue', id: 'ISS-012' }).root.querySelector('a').getAttribute('href'), '#/issue/ISS-012');
  assert.equal(row({ type: 'idea', id: 'IDEA-7' }).root.querySelector('a').getAttribute('href'), '#/ideas');
  assert.equal(ITEM_ROUTE.task('T 1'), '#/task/T%201');
});

test('a branch item is a plain row with no control', () => {
  const { root } = row({ type: 'branch', id: 'feat/x' });
  assert.ok(root.classList.contains('co-row'));
  assert.equal(root.querySelectorAll('a, button, [tabindex]').length, 0);
  assert.equal(root.querySelector('.co-chip').textContent, 'Branch');
});

test('a handover row is a disclosure whose state follows the controller', () => {
  const r = row({ type: 'handover', id: '2026-10-05-r1' });
  const toggle = r.root.querySelector('button.co-row__toggle');
  assert.ok(toggle);
  assert.equal(toggle.getAttribute('type'), 'button');
  assert.equal(toggle.getAttribute('aria-expanded'), 'false');
  assert.equal(r.root.querySelectorAll('a, button').length, 1);

  r.setLoading();
  assert.equal(toggle.getAttribute('aria-expanded'), 'true');
  assert.equal(r.isExpanded(), true);
  const region = r.root.querySelector('.co-row__expanded');
  assert.ok(region.id);
  assert.equal(toggle.getAttribute('aria-controls'), region.id);
  assert.equal(region.getAttribute('role'), 'region');
  assert.equal(region.getAttribute('aria-label'), 'Handover 2026-10-05-r1');

  const body = document.createElement('p');
  body.textContent = 'Cards done';
  r.setExpanded(body);
  assert.equal(toggle.getAttribute('aria-expanded'), 'true');
  const opened = r.root.querySelector('.co-row__expanded');
  assert.equal(opened.textContent, 'Cards done');
  assert.equal(toggle.getAttribute('aria-controls'), opened.id);

  r.clearExpanded();
  assert.equal(toggle.getAttribute('aria-expanded'), 'false');
  assert.equal(r.isExpanded(), false);
  assert.equal(r.root.querySelector('.co-row__expanded'), null);
});

test('a click on the toggle hands the item and the controller to onToggle', () => {
  let got = null;
  const item = { ...base, type: 'decision', id: 'DEC-001' };
  const r = createItemRow({ item, onToggle: (i, c) => { got = [i, c]; } });
  r.root.querySelector('.co-row__toggle').click();
  assert.equal(got[0], item);
  assert.equal(typeof got[1].setExpanded, 'function');
  assert.equal(got[1].root, r.root);
});

test('a title that looks like markup is text, and a plain title keeps its words', () => {
  const evil = '<img src=x onerror=alert(1)>';
  for (const type of ['task', 'handover', 'branch']) {
    const { root } = row({ type, id: 'X-1', title: evil });
    assert.equal(root.querySelector('img'), null);
    const title = root.querySelector('.co-row__title');
    assert.equal(title.textContent, evil);
    assert.equal(title.getAttribute('title'), evil);
  }
});

test('a tagged title keeps its rendered tag and its full text in title', () => {
  const text = 'Pick a store <decision>sqlite or yaml</decision>';
  const { root } = row({ type: 'handover', id: 'h1', title: text });
  const title = root.querySelector('.co-row__title');
  assert.ok(title.querySelector('.co-xtag'));
  assert.equal(title.getAttribute('title'), text);
});

test('the type tag is the word, with no per-type class', () => {
  const words = { decision: 'Decision', handover: 'Handover', task: 'Task', branch: 'Branch', idea: 'Idea', issue: 'Issue' };
  for (const [type, word] of Object.entries(words)) {
    const chip = row({ type, id: 'X-2' }).root.querySelector('.co-chip');
    assert.equal(chip.tagName, 'SPAN');
    assert.equal(chip.textContent, word);
    assert.equal(chip.className, 'co-chip');
  }
});
