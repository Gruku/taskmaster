// User intent: the Sessions timeline's rows must be real buttons that lead with a readable title (the tldr) and keep
// the slug as a subline, mark the row shown in the rail, and never let row data become markup or a nested control.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><body><div id="root"></div></body>', { url: 'http://localhost/' });
global.window = dom.window;
global.document = dom.window.document;
global.HTMLElement = dom.window.HTMLElement;

const { renderTimeline, kindLabel } = await import('../../js/components/timeline.js');

const SESSIONS = [
  { id: 'team-relayout', start: '2026-07-12T09:00:00Z', end: '2026-07-13T10:00:00Z', duration: 90000,
    tldr: 'M1 shipped', task_ids: ['T-102'], handover_ids: ['2026-07-12-scope', '2026-07-13-m1-shipped'] },
  { id: 'guard-hooks-polish', start: '2026-07-10T10:00:00Z', end: '2026-07-10T10:00:00Z', duration: 0,
    task_ids: [], handover_ids: [] },
];
const HANDOVERS = {
  '2026-07-12-scope': { id: '2026-07-12-scope', viewer_kind: 'mid-task', status: 'closed', tldr: 'Scope the relayout' },
  '2026-07-13-m1-shipped': { id: '2026-07-13-m1-shipped', viewer_kind: 'checkpoint', status: 'open', tldr: 'M1 shipped' },
};

function draw(opts = {}) {
  const root = document.getElementById('root');
  renderTimeline(root, { sessions: SESSIONS, handovers: HANDOVERS, ...opts });
  return root;
}

test('every row is a button that controls the right rail while one is open', () => {
  const open = draw({ selected: { kind: 'session', id: 'team-relayout' } }).querySelectorAll('.ho, .ho-child');
  assert.equal(open.length, 4);
  for (const row of open) {
    assert.equal(row.localName, 'button');
    assert.equal(row.getAttribute('type'), 'button');
    assert.equal(row.getAttribute('aria-controls'), 'right-rail');
  }
  // With no rail open, #right-rail does not exist: no row points at it.
  const root = draw();
  for (const row of root.querySelectorAll('.ho, .ho-child')) {
    assert.equal(row.localName, 'button');
    assert.equal(row.hasAttribute('aria-controls'), false);
  }
  assert.ok(root.querySelector('button.ho[data-session-id="team-relayout"]'));
  assert.ok(root.querySelector('button.ho-child[data-handover-id="2026-07-12-scope"]'));
});

test('a session with a tldr leads with it; its id is the slug', () => {
  const row = draw().querySelector('.ho[data-session-id="team-relayout"]');
  assert.equal(row.querySelector('.ho-title').textContent, 'M1 shipped');
  assert.equal(row.querySelector('.ho-slug').textContent, 'team-relayout');
  assert.equal(row.querySelector('.ho-kind').textContent, 'Thread');
  assert.deepEqual([...row.querySelectorAll('.ho-task')].map((t) => t.textContent), ['T-102']);
});

test('a session without a tldr is titled by its id and has no slug', () => {
  const row = draw().querySelector('.ho[data-session-id="guard-hooks-polish"]');
  assert.equal(row.querySelector('.ho-title').textContent, 'guard-hooks-polish');
  assert.equal(row.querySelector('.ho-slug'), null);
});

test('a handover row says its kind and status in words, then its title and slug', () => {
  const row = draw().querySelector('.ho-child[data-handover-id="2026-07-12-scope"]');
  assert.equal(row.querySelector('.ho-kind').textContent, 'Mid-task');
  assert.equal(row.querySelector('.ho-status .marker__word').textContent, 'Closed');
  assert.equal(row.querySelector('.ho-status .marker__shape').dataset.shape, 'dot');
  assert.equal(row.querySelector('.ho-title').textContent, 'Scope the relayout');
  assert.equal(row.querySelector('.ho-slug').textContent, '2026-07-12-scope');
});

test('every title keeps its words in its title attribute', () => {
  for (const title of draw().querySelectorAll('.ho-title')) assert.equal(title.title, title.textContent);
});

test('selected marks exactly that row aria-current', () => {
  const root = draw({ selected: { kind: 'handover', id: '2026-07-13-m1-shipped' } });
  const marked = root.querySelectorAll('[aria-current]');
  assert.equal(marked.length, 1);
  assert.equal(marked[0].dataset.handoverId, '2026-07-13-m1-shipped');
  assert.equal(marked[0].getAttribute('aria-current'), 'true');
  assert.equal(draw({ selected: null }).querySelectorAll('[aria-current]').length, 0);
});

test('a click calls onSelect with the kind, the id and the button', () => {
  const calls = [];
  const root = draw({ onSelect: (sel, btn) => calls.push([sel, btn]) });
  const session = root.querySelector('.ho[data-session-id="team-relayout"]');
  const handover = root.querySelector('.ho-child[data-handover-id="2026-07-13-m1-shipped"]');
  session.click();
  handover.click();
  assert.deepEqual(calls.map(([sel]) => sel), [
    { kind: 'session', id: 'team-relayout' },
    { kind: 'handover', id: '2026-07-13-m1-shipped' },
  ]);
  assert.equal(calls[0][1], session);
  assert.equal(calls[1][1], handover);
});

test('kindLabel puts a kind in sentence case, and names a missing one "Handover"', () => {
  assert.equal(kindLabel('mid-task'), 'Mid-task');
  assert.equal(kindLabel('standalone'), 'Standalone');
  assert.equal(kindLabel(''), 'Handover');
  assert.equal(kindLabel(undefined), 'Handover');
});

test('row text is text: a tldr that looks like markup makes no element', () => {
  const evil = '<img src=x onerror=alert(1)>';
  const root = document.getElementById('root');
  renderTimeline(root, {
    sessions: [{ ...SESSIONS[1], tldr: evil, handover_ids: ['h-1'] }],
    handovers: { 'h-1': { id: 'h-1', viewer_kind: 'wrap', status: 'open', tldr: evil } },
  });
  assert.equal(root.querySelector('img'), null);
  for (const title of root.querySelectorAll('.ho-title')) assert.equal(title.textContent, evil);
});

test('nothing inside a row takes focus or a click of its own', () => {
  for (const row of draw().querySelectorAll('.ho, .ho-child')) {
    assert.equal(row.querySelector('button, a, [tabindex]'), null);
    for (const el of row.querySelectorAll('*')) assert.equal(el.localName, 'span', `only spans inside a row, got <${el.localName}>`);
  }
});

test('a parallel block lays its columns out in CSS, not inline', () => {
  const root = document.getElementById('root');
  renderTimeline(root, {
    sessions: [
      { id: 'A', start: '2026-07-12T09:00:00Z', end: '2026-07-12T11:00:00Z', handover_ids: [] },
      { id: 'B', start: '2026-07-12T10:00:00Z', end: '2026-07-12T12:00:00Z', handover_ids: [] },
    ],
    handovers: {},
  });
  const grid = root.querySelector('.par-block .par-grid');
  assert.ok(grid);
  assert.equal(grid.getAttribute('style'), null);
  assert.equal(grid.querySelectorAll('.ho').length, 2);
});
