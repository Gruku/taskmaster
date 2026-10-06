// User intent: the Dashboard's summary strip counts what needs the person — in progress, waiting on them, open issues,
// open bugs — each a link to where they are, and a count it could not read says so instead of showing a wrong zero.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { BOARD } from '../mock-fixtures.js';

const dom = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const { summaryCounts, SUMMARY_LINKS, createSummaryStrip } = await import('../../js/components/desk/summary-strip.js');

test('tasks are counted by status: in progress and in review', () => {
  const c = summaryCounts({ tasks: BOARD.tasks, issues: [], bugs: [] });
  assert.equal(c.inProgress, 2);
  assert.equal(c.waiting, 1);
});

test('archived tasks are not counted', () => {
  const tasks = [...BOARD.tasks, { id: 'T-900', status: 'archived' }, { id: 'T-901', status: 'archived' }];
  const c = summaryCounts({ tasks, issues: [], bugs: [] });
  assert.equal(c.inProgress, 2);
  assert.equal(c.waiting, 1);
});

test('task counts are 0 when there is no task list', () => {
  const c = summaryCounts({ tasks: undefined, issues: [], bugs: [] });
  assert.equal(c.inProgress, 0);
  assert.equal(c.waiting, 0);
});

test('open issues are open or investigating; open bugs are open', () => {
  const c = summaryCounts({
    tasks: [],
    issues: [{ status: 'open' }, { status: 'investigating' }, { status: 'fixed' }],
    bugs: [{ status: 'open' }, { status: 'fixed' }],
  });
  assert.equal(c.issues, 2);
  assert.equal(c.bugs, 1);
});

test('an issue or bug list that is not an array counts as null', () => {
  const c = summaryCounts({ tasks: [], issues: null, bugs: undefined });
  assert.equal(c.issues, null);
  assert.equal(c.bugs, null);
});

test('the strip is a named nav of four links to the filtered screens', () => {
  const { root } = createSummaryStrip();
  assert.equal(root.localName, 'nav');
  assert.ok(root.classList.contains('dk-summary'));
  assert.equal(root.getAttribute('aria-label'), 'Project summary');
  const links = [...root.querySelectorAll('ul > li > a.dk-stat')];
  assert.deepEqual(links.map((a) => a.getAttribute('href')), SUMMARY_LINKS.map((l) => l.href));
  assert.deepEqual(SUMMARY_LINKS.map((l) => l.href),
    ['#/table?status=in-progress', '#/table?status=in-review', '#/issues', '#/bugs']);
  for (const a of links) {
    assert.ok(a.querySelector(':scope > span.dk-stat__n'));
    assert.ok(a.querySelector(':scope > span.dk-stat__label'));
  }
});

test('each link reads its count then its label', () => {
  const strip = createSummaryStrip();
  strip.update({ inProgress: 2, waiting: 1, issues: 3, bugs: 0 });
  const text = [...strip.root.querySelectorAll('a')].map((a) => a.textContent);
  assert.deepEqual(text, ['2 In progress', '1 Waiting on you', '3 Open issues', '0 Open bugs']);
});

test('update() rewrites the numbers in place and keeps the same four anchors', () => {
  const strip = createSummaryStrip();
  strip.update({ inProgress: 2, waiting: 1, issues: 3, bugs: 2 });
  const before = [...strip.root.querySelectorAll('a')];
  strip.update({ inProgress: 3, waiting: 0, issues: 1, bugs: 5 });
  const after = [...strip.root.querySelectorAll('a')];
  assert.equal(after.length, 4);
  after.forEach((a, i) => assert.equal(a, before[i]));
  assert.equal(after[0].textContent, '3 In progress');
  assert.equal(after[3].textContent, '5 Open bugs');
});

test('a null count shows a dash and says it was not loaded; a later count clears that', () => {
  const strip = createSummaryStrip();
  strip.update({ inProgress: 2, waiting: 1, issues: null, bugs: 2 });
  const issues = strip.root.querySelector('a[href="#/issues"]');
  assert.equal(issues.querySelector('.dk-stat__n').textContent, '—');
  assert.equal(issues.getAttribute('title'), 'Not loaded');
  assert.equal(strip.root.querySelector('a[href="#/bugs"]').hasAttribute('title'), false);
  strip.update({ inProgress: 2, waiting: 1, issues: 4, bugs: 2 });
  assert.equal(issues.querySelector('.dk-stat__n').textContent, '4');
  assert.equal(issues.hasAttribute('title'), false);
});
