// viewer/tests/unit/detail-page.test.js
// User intent: pin the shared detail template (meta line, title, sections, dates, grid, rail panels) that the task,
// issue and bug pages are built from, so the three pages keep reading as one.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

// The real vendored parser, so markdownBody is checked through the sanitiser that guards it.
const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/', runScripts: 'outside-only' });
dom.window.eval(readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../../vendor/marked.min.js'), 'utf8'));
globalThis.document = dom.window.document;
globalThis.window = dom.window;
globalThis.HTMLElement = dom.window.HTMLElement;
const copied = [];
Object.defineProperty(globalThis, 'navigator', {
  value: { clipboard: { writeText: async (text) => { copied.push(text); } } },
  configurable: true,
});

const { h } = await import('../../js/util/h.js');
const {
  detailMeta, stampEl, copyId, detailTitle, detailSection, markdownBody, datesList, detailGrid, railPanel, railGroup,
} = await import('../../js/components/detail-page.js');

const tick = () => new Promise((resolve) => setTimeout(resolve, 0));
const ISO = '2026-09-28T09:00:00Z';

test('detailMeta drops empty parts and puts a hidden separator between the rest', () => {
  const line = detailMeta(['A', null, '', false, h('a', { href: '#/x' }, 'B')]);
  assert.equal(line.className, 'td-meta');
  assert.equal(line.dataset.test, 'meta');
  const kids = [...line.children];
  assert.deepEqual(kids.map((k) => k.tagName.toLowerCase()), ['span', 'span', 'a']);
  assert.ok(kids[1].classList.contains('td-sep'));
  assert.equal(kids[1].getAttribute('aria-hidden'), 'true');
  assert.equal(line.textContent, 'A·B');
});

test('stampEl is a time with the exact instant in its title, or null for a non-date', () => {
  const time = stampEl(ISO);
  assert.equal(time.tagName, 'TIME');
  assert.equal(time.getAttribute('datetime'), ISO);
  assert.ok(time.getAttribute('title'));
  assert.equal(stampEl('nope'), null);
  assert.equal(stampEl(null), null);
  const created = stampEl(ISO, { prefix: 'created' });
  assert.equal(created.tagName, 'SPAN');
  assert.ok(created.classList.contains('td-meta__created'));
  assert.ok(created.textContent.startsWith('created '));
  assert.ok(created.querySelector('time'));
});

test('copyId copies the id and says so in its live region', async () => {
  const timers = new Set();
  const btn = copyId({ id: 'B-031', noun: 'bug', timers });
  assert.equal(btn.getAttribute('aria-label'), 'Copy bug id');
  assert.equal(btn.dataset.test, 'bug-id');
  assert.equal(btn.dataset.focus, 'copy:id');
  assert.ok(btn.classList.contains('td-copy') && btn.classList.contains('td-id'));
  btn.click();
  await tick();
  assert.equal(copied.at(-1), 'B-031');
  assert.equal(btn.querySelector('.td-copy__status').textContent, 'Copied');
  assert.equal(timers.size, 1);
  for (const t of timers) clearTimeout(t);
});

test('detailTitle is the page h1 and names an untitled record', () => {
  const title = detailTitle('');
  assert.equal(title.tagName, 'H1');
  assert.ok(title.classList.contains('td-title'));
  assert.equal(title.textContent, '(untitled)');
});

test('detailSection is a labelled section of rendered text', () => {
  const sec = detailSection({ key: 'evidence', label: 'Evidence', body: markdownBody('**x**') });
  assert.equal(sec.tagName, 'SECTION');
  assert.equal(sec.dataset.section, 'evidence');
  assert.equal(sec.dataset.test, 'sec-evidence');
  assert.ok(sec.querySelector('h2.td-section-h'));
  assert.ok(sec.querySelector('strong'));
});

test('markdownBody never lets an event handler through', () => {
  const body = markdownBody('<img src=x onerror=alert(1)>');
  assert.equal(body.querySelector('[onerror]'), null);
});

test('datesList keeps only real dates, and is null without any', () => {
  assert.equal(datesList([['Created', null]]), null);
  const dl = datesList([['Created', ISO], ['Done', '']]);
  assert.equal(dl.tagName, 'DL');
  assert.equal(dl.querySelectorAll('dt').length, 1);
});

test('detailGrid has no rail without panels, and a labelled aside with one', () => {
  const solo = detailGrid({ body: h('div'), panels: [] });
  assert.equal(solo.className, 'td-grid td-grid--solo');
  assert.equal(solo.querySelector('aside'), null);
  const grid = detailGrid({ body: h('div'), panels: [h('section')] });
  assert.equal(grid.className, 'td-grid');
  assert.ok(grid.querySelector('aside.td-rail[aria-label="Related"]'));
});

test('railPanel and railGroup carry the rail markup', () => {
  const panel = railPanel({ name: 'relations', label: 'Relations', level: 2, children: [h('p', {}, 'x')] });
  assert.ok(panel.matches('section.td-panel.td-panel-relations[data-panel="relations"]'));
  assert.equal(panel.firstElementChild.tagName, 'H2');
  assert.ok(panel.firstElementChild.classList.contains('td-rail-h'));
  const group = railGroup({ name: 'links', label: 'Links', level: 2, body: h('ul') });
  assert.ok(group.matches('div.td-rail-group[data-sub="links"]'));
  assert.equal(group.firstElementChild.tagName, 'H3');
  assert.ok(group.firstElementChild.classList.contains('td-rail-sub'));
});
