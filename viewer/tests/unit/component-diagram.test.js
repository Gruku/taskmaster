// plugins/taskmaster/viewer/tests/unit/component-diagram.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body><div id="detail-modal-host"></div></body></html>', { url: 'http://localhost/' });
globalThis.document = dom.window.document;
globalThis.window = dom.window;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.Node = dom.window.Node;
globalThis.history  = dom.window.history;
globalThis.location = dom.window.location;

const { mountComponentDiagram, computeEdgePath, blockVisualState } = await import('../../js/components/component-diagram.js');

const COMPONENTS = {
  ingest: { title: 'Ingest', after: [] },
  thumb:  { title: 'Thumbnailer', after: ['ingest'] },
};
const ROLLUP = {
  ingest: { status: 'done', total: 2, done: 2 },
  thumb:  { status: 'blocked', total: 3, done: 0, blocked: 1 },
};
const TASKS = [
  { id: 'ING-1', title: 'Decode', status: 'done', component: 'ingest', priority: 'high' },
  { id: 'ING-2', title: 'Probe',  status: 'done', component: 'ingest', priority: 'medium' },
  { id: 'THM-1', title: 'Resize', status: 'blocked', component: 'thumb', priority: 'high' },
];

function freshHost() { const h = document.createElement('div'); document.body.appendChild(h); return h; }

test('mounts a cd-map with one block per component', () => {
  const host = freshHost();
  mountComponentDiagram(host, { components: COMPONENTS, rollup: ROLLUP, tasks: TASKS });
  assert.ok(host.querySelector('.cd-map'), 'cd-map mounted');
  assert.equal(host.querySelectorAll('.cd-block').length, 2);
});

test('blocks carry rank-ordered columns (dependency left of dependent)', () => {
  const host = freshHost();
  mountComponentDiagram(host, { components: COMPONENTS, rollup: ROLLUP, tasks: TASKS });
  const ingest = host.querySelector('.cd-block[data-id="ingest"]');
  const thumb  = host.querySelector('.cd-block[data-id="thumb"]');
  assert.equal(ingest.dataset.rank, '0');
  assert.equal(thumb.dataset.rank, '1');
});

test('each block embeds the renderMinimalCard cards for its component', () => {
  const host = freshHost();
  mountComponentDiagram(host, { components: COMPONENTS, rollup: ROLLUP, tasks: TASKS });
  const ingest = host.querySelector('.cd-block[data-id="ingest"]');
  const ids = [...ingest.querySelectorAll('.card-task')].map(c => c.dataset.taskId);
  assert.deepEqual(ids.sort(), ['ING-1', 'ING-2']);
});

test('empty component renders a "no tasks yet" stub', () => {
  const host = freshHost();
  mountComponentDiagram(host, {
    components: { cdn: { title: 'CDN', after: [] } },
    rollup: { cdn: { status: 'todo', total: 0, done: 0 } },
    tasks: [],
  });
  const block = host.querySelector('.cd-block[data-id="cdn"]');
  assert.ok(block.querySelector('.cd-block__empty'), 'empty stub present');
  assert.match(block.textContent, /no tasks yet/i);
});

test('block visual-state class derives from rollup status', () => {
  const host = freshHost();
  mountComponentDiagram(host, { components: COMPONENTS, rollup: ROLLUP, tasks: TASKS });
  assert.match(host.querySelector('.cd-block[data-id="ingest"]').className, /cd-block--done/);
  assert.match(host.querySelector('.cd-block[data-id="thumb"]').className, /cd-block--attention/);
});

test('block summary surfaces blocked count for in-progress rollup', () => {
  const host = freshHost();
  mountComponentDiagram(host, {
    components: { a: { title: 'A', after: [] } },
    rollup: { a: { status: 'in-progress', total: 4, done: 1, blocked: 2 } },
    tasks: [],
  });
  assert.match(host.querySelector('.cd-block[data-id="a"] .cd-block__summary').textContent, /in-progress · 2 blocked/);
});

test('connector overlay has one cd-edge path per after-edge', () => {
  const host = freshHost();
  mountComponentDiagram(host, { components: COMPONENTS, rollup: ROLLUP, tasks: TASKS });
  const svg = host.querySelector('svg.cd-connectors');
  assert.ok(svg, 'connector svg present');
  assert.equal(svg.namespaceURI, 'http://www.w3.org/2000/svg');
  assert.equal(host.querySelectorAll('path.cd-edge').length, 1);
});

test('computeEdgePath returns a cubic bezier relative to the host', () => {
  const from = { left: 0,   top: 0,  right: 100, bottom: 50,  width: 100, height: 50 };
  const to   = { left: 200, top: 60, right: 300, bottom: 110, width: 100, height: 50 };
  const host = { left: 0, top: 0 };
  const d = computeEdgePath(from, to, host);
  assert.equal(d, 'M 100.0 25.0 C 160.0 25.0, 140.0 85.0, 200.0 85.0');
});

test('block-header click invokes onComponentNav with the component key', () => {
  const host = freshHost();
  const calls = [];
  mountComponentDiagram(host, { components: COMPONENTS, rollup: ROLLUP, tasks: TASKS, onComponentNav: (k) => calls.push(k) });
  host.querySelector('.cd-block[data-id="thumb"] .cd-block__head').dispatchEvent(new dom.window.Event('click', { bubbles: true }));
  assert.deepEqual(calls, ['thumb']);
});

test('clicking a task card does NOT trigger block navigation', () => {
  const host = freshHost();
  const calls = [];
  mountComponentDiagram(host, { components: COMPONENTS, rollup: ROLLUP, tasks: TASKS, onComponentNav: (k) => calls.push(k) });
  host.querySelector('.cd-block[data-id="ingest"] .card-task').dispatchEvent(new dom.window.Event('click', { bubbles: true }));
  assert.deepEqual(calls, []);
});

test('Enter key on a block invokes onComponentNav', () => {
  const host = freshHost();
  const calls = [];
  mountComponentDiagram(host, { components: COMPONENTS, rollup: ROLLUP, tasks: TASKS, onComponentNav: (k) => calls.push(k) });
  const ev = new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true });
  host.querySelector('.cd-block[data-id="ingest"]').dispatchEvent(ev);
  assert.deepEqual(calls, ['ingest']);
});

test('unassigned bucket renders a trailing dashed block', () => {
  const host = freshHost();
  mountComponentDiagram(host, {
    components: { a: { title: 'A', after: [] } },
    rollup: { a: { status: 'todo' }, _unassigned: { status: 'todo', total: 2, done: 0 } },
    tasks: [{ id: 'X-1', title: 'Loose', status: 'todo', priority: 'low' }],
  });
  const u = host.querySelector('.cd-block--unassigned');
  assert.ok(u, 'unassigned block present');
  assert.ok(host.querySelector('.cd-rank--unassigned'), 'trailing unassigned rank');
  assert.equal(u.querySelector('.card-task').dataset.taskId, 'X-1');
});

test('produced markup contains no box-shadow / border-left / transform', () => {
  const host = freshHost();
  mountComponentDiagram(host, { components: COMPONENTS, rollup: ROLLUP, tasks: TASKS });
  const html = host.innerHTML;
  assert.doesNotMatch(html, /box-shadow/i);
  assert.doesNotMatch(html, /border-left/i);
  assert.doesNotMatch(html, /transform|translate|scale/i);
});

test('cleanup empties the container', () => {
  const host = freshHost();
  const cleanup = mountComponentDiagram(host, { components: COMPONENTS, rollup: ROLLUP, tasks: TASKS });
  assert.ok(host.querySelector('.cd-map'));
  cleanup();
  assert.equal(host.querySelector('.cd-map'), null);
});

test('empty components → mounts nothing and returns a no-op cleanup', () => {
  const host = freshHost();
  const cleanup = mountComponentDiagram(host, { components: {}, rollup: {}, tasks: [] });
  assert.equal(host.querySelector('.cd-map'), null);
  assert.doesNotThrow(() => cleanup());
});

test('blockVisualState mapping is total and pinned', () => {
  assert.equal(blockVisualState('done'),        'done');
  assert.equal(blockVisualState('in-progress'), 'progress');
  assert.equal(blockVisualState('blocked'),     'attention');
  assert.equal(blockVisualState('todo'),        'todo');
  assert.equal(blockVisualState(undefined),     'todo');
  assert.equal(blockVisualState('in-review'),   'todo');
  assert.equal(blockVisualState('weird-future'),'todo');
});
