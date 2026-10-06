// User intent: the task page's Graph view reads like the Document view's page — one h1, its markers — and every node,
// tab and control in it is a real link or button the keyboard reaches, saying its status in a shape and a word.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body><div id="topbar-count"></div><div id="topbar-primary"></div><div id="topbar-actions"></div></body></html>', {
  url: 'http://localhost/',
});
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.Event = dom.window.Event;
globalThis.history = dom.window.history;
Object.defineProperty(globalThis, 'navigator', { value: { clipboard: { writeText: async () => {} } }, configurable: true });

const { mountTaskDetailGraph } = await import('../../js/components/task-detail-graph.js');

const SVG_NS = 'http://www.w3.org/2000/svg';
const TASK = {
  id: 'T-102', title: 'Re-skin the Kanban cards and columns', status: 'in-progress', priority: 'critical', epic: 'viewer', phase: 'P1',
  estimate: 'M', created: '2026-09-28T09:00:00Z',
  specification: '## Requirements\n\n1. Cards are the raised ground.', plan: '1. Tokens\n2. Cards', notes: 'Light theme: check the card edge.',
  activity: ['2026-09-30 16:20 checkpoint: cards done'], anchors: ['viewer/js/components/card.js'],
};
const LONG_TITLE = 'An incremental related-index rebuild under the writer mutex!';
const RELATED = {
  dependencies: [
    { id: 'T-101', title: 'Token foundation and theme switch', status: 'done' },
    { id: 'T-105', title: LONG_TITLE, status: 'todo' },
  ],
  unblocks: [{ id: 'T-104', title: 'Sessions timeline: replace the legacy palette', status: 'todo' }],
  handovers: [{ id: '2026-09-30-kanban-reskin' }],
  issues: [{ id: 'ISS-012', title: 'Card edge vanishes on the light page ground' }],
};

function mount({ task = TASK, related = RELATED, viewState } = {}) {
  const root = document.createElement('div');
  document.body.appendChild(root);
  const ctx = {
    task: structuredClone(task), related: structuredClone(related), onToggleVariant: () => {}, viewState,
    store: { getBacklog: () => ({ tasks: [], epics: [] }), setEtag: () => {}, refreshBoard: async () => {} },
    api: { patchTask: async () => ({}) },
  };
  const dispose = mountTaskDetailGraph(root, ctx);
  return { root, dispose, done() { dispose(); root.remove(); } };
}
const key = (el, k) => el.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));
const titleOf = (el) => [...el.children].find((c) => c.localName === 'title')?.textContent ?? null;

test('1 head: the page head and its one h1, with the status and priority markers', () => {
  const { root, done } = mount();
  assert.equal(root.querySelectorAll('h1').length, 1);
  assert.equal(root.querySelector('h1').textContent, TASK.title);
  assert.ok(root.querySelector('header.td-head [data-test="meta"]'), 'the meta line heads the page');
  const markers = root.querySelector('header.td-head div.td-markers[data-test="chips"]');
  assert.ok(markers);
  assert.equal(markers.querySelectorAll('.marker').length, 2);
  assert.deepEqual([...markers.querySelectorAll('.marker__word')].map((w) => w.textContent), ['In progress', 'Critical']);
  assert.equal(root.querySelector('.td-head-block, .td-head-title'), null);
  done();
});

test('2 nodes: each neighbour is an SVG link with its full title; the centre is a named image', () => {
  const { root, done } = mount();
  const links = [...root.querySelectorAll('a.node[href]')];
  assert.equal(links.length, 3);
  assert.deepEqual(links.map((a) => a.getAttribute('href')).sort(), ['#/task/T-101', '#/task/T-104', '#/task/T-105']);
  for (const a of links) {
    assert.equal(a.namespaceURI, SVG_NS);
    assert.ok(a.classList.contains('node--link'));
  }
  const byHref = (h) => links.find((a) => a.getAttribute('href') === h);
  assert.equal(titleOf(byHref('#/task/T-101')), 'T-101 · Token foundation and theme switch · Done');
  assert.ok(titleOf(byHref('#/task/T-105')).includes(LONG_TITLE));
  const centre = root.querySelector('g.node--center[role="img"]');
  assert.ok(centre);
  assert.equal(centre.getAttribute('aria-label'), `This task: T-102 · ${TASK.title}`);
  done();
});

test('3 status: every node draws its status shape in its tone and says the word', () => {
  const { root, done } = mount();
  const done1 = root.querySelector('a.node[href="#/task/T-101"]');
  assert.ok(done1.querySelector('.node-shape.node-shape--success'));
  assert.match(done1.textContent, /Done/);
  const todo = root.querySelector('a.node[href="#/task/T-104"]');
  assert.ok(todo.querySelector('.node-shape.node-shape--neutral'));
  assert.match(todo.textContent, /Todo/);
  const centre = root.querySelector('g.node--center');
  assert.ok(centre.querySelector('.node-shape.node-shape--accent'));
  assert.match(centre.textContent, /In progress · Critical · M|In progress/);
  assert.equal(root.querySelector('.status-dot, circle.status-dot'), null);
  done();
});

test('4 cut text keeps its words: a long title ends in … and the node title holds all of it', () => {
  const { root, done } = mount();
  const a = root.querySelector('a.node[href="#/task/T-105"]');
  const shown = a.querySelector('text.node-title').textContent;
  assert.ok(shown.endsWith('…'), shown);
  assert.ok(shown.length < LONG_TITLE.length);
  assert.ok(titleOf(a).includes(LONG_TITLE));
  assert.equal(LONG_TITLE.length, 60);
  done();
});

test('5 no legend and no axis rail', () => {
  const { root, done } = mount();
  assert.equal(root.querySelector('.td-graph-rail, .legend, .axis'), null);
  done();
});

test('6 controls: no dead Depth or Show all; every control is a shared button; Hide context toggles the band', () => {
  const { root, done } = mount();
  assert.equal(root.querySelector('[data-id="depth"]'), null);
  assert.equal(root.querySelector('[data-id="show-all"]'), null);
  assert.doesNotMatch(root.textContent, /Depth|Show all/);
  const controls = [...root.querySelectorAll('[data-test="graph-controls"] button')];
  assert.ok(controls.length >= 1);
  for (const b of controls) assert.ok(b.matches('.btn.btn--ghost.btn--sm'), b.outerHTML);
  // jsdom has no fullscreen: the control is not offered rather than offered dead.
  assert.equal(controls.some((b) => /Fullscreen/.test(b.textContent)), false);
  const hide = controls.find((b) => b.textContent === 'Hide context');
  const band = root.querySelector('[data-test="context-band"]');
  assert.equal(hide.getAttribute('aria-pressed'), 'false');
  assert.equal(band.hidden, false);
  hide.click();
  assert.equal(hide.getAttribute('aria-pressed'), 'true');
  assert.equal(band.hidden, true);
  hide.click();
  assert.equal(band.hidden, false);
  done();
});

test('6 controls: no context, no Hide context button', () => {
  const { root, done } = mount({ related: { dependencies: RELATED.dependencies } });
  assert.equal([...root.querySelectorAll('button')].some((b) => b.textContent === 'Hide context'), false);
  done();
});

test('7 context band: an issue is a link to it, a handover its id', () => {
  const { root, done } = mount();
  const issue = root.querySelector('a.ctx-pill.issue[href="#/issue/ISS-012"]');
  assert.ok(issue);
  assert.equal(issue.textContent, 'ISS-012');
  const ho = root.querySelector('span.ctx-pill.handover');
  assert.equal(ho.textContent, '2026-09-30-kanban-reskin');
  done();
});

test('8 tabs: ARIA tabs that arrow keys, Home and End move through, wrapping; Raw JSON is the task as JSON', () => {
  const { root, done } = mount();
  const list = root.querySelector('div.td-tabs[role="tablist"][aria-label="Task documents"]');
  assert.ok(list);
  const tabs = [...list.querySelectorAll('button.td-tab[role="tab"]')];
  assert.deepEqual(tabs.map((t) => t.textContent), ['Spec', 'Plan', 'Notes', 'Activity', 'Anchors', 'Raw JSON']);
  const panelOf = (t) => root.querySelector(`#${t.getAttribute('aria-controls')}`);
  for (const [i, t] of tabs.entries()) {
    assert.ok(t.id);
    const p = panelOf(t);
    assert.ok(p.matches('div.td-tab-panel[role="tabpanel"][tabindex="0"]'));
    assert.equal(p.getAttribute('aria-labelledby'), t.id);
    assert.equal(t.getAttribute('aria-selected'), String(i === 0));
    assert.equal(t.getAttribute('tabindex'), i === 0 ? '0' : '-1');
    assert.equal(p.hidden, i !== 0);
  }
  tabs[0].focus();
  key(tabs[0], 'ArrowRight');
  assert.equal(tabs[1].getAttribute('aria-selected'), 'true');
  assert.equal(document.activeElement, tabs[1]);
  assert.equal(panelOf(tabs[1]).hidden, false);
  assert.equal(panelOf(tabs[0]).hidden, true);
  assert.equal(tabs[0].getAttribute('tabindex'), '-1');

  key(tabs[1], 'End');
  const raw = tabs.at(-1);
  assert.equal(raw.getAttribute('aria-selected'), 'true');
  assert.equal(document.activeElement, raw);
  const pre = panelOf(raw).querySelector('pre');
  assert.ok(pre);
  assert.equal(JSON.parse(pre.textContent).id, 'T-102');

  key(raw, 'ArrowRight');
  assert.equal(tabs[0].getAttribute('aria-selected'), 'true', 'ArrowRight wraps from the last tab to the first');
  assert.equal(document.activeElement, tabs[0]);
  key(tabs[0], 'ArrowLeft');
  assert.equal(document.activeElement, raw, 'ArrowLeft wraps from the first tab to the last');
  key(raw, 'Home');
  assert.equal(document.activeElement, tabs[0]);
  done();
});

test('8 tabs: an empty panel is one .td-empty line', () => {
  const { root, done } = mount({ task: { ...TASK, plan: '', activity: [], anchors: [] } });
  const panel = (label) => {
    const tab = [...root.querySelectorAll('.td-tab')].find((t) => t.textContent === label);
    return root.querySelector(`#${tab.getAttribute('aria-controls')}`);
  };
  for (const label of ['Plan', 'Activity', 'Anchors']) {
    const p = panel(label);
    assert.equal(p.children.length, 1, label);
    assert.ok(p.firstElementChild.classList.contains('td-empty'), label);
    assert.equal(p.querySelectorAll('.td-empty').length, 1, label);
  }
  done();
});

test('9 empty graph: the state block inside the recess', () => {
  const { root, done } = mount({ related: {} });
  const frame = root.querySelector('[data-test="graph-frame"]');
  assert.ok(frame.querySelector('.tm-empty, [data-state="empty"]'));
  assert.match(frame.textContent, /No dependencies to draw/);
  assert.equal(frame.querySelector('svg'), null);
  done();
});

test('10 accessible frame: the svg is a named group; guides, edges and column labels are hidden', () => {
  const { root, done } = mount();
  const svg = root.querySelector('svg.td-graph-svg');
  assert.equal(svg.getAttribute('role'), 'group');
  assert.equal(svg.getAttribute('aria-label'), 'Dependencies and unblocks of T-102');
  const deco = [...svg.querySelectorAll('.col-guide, .edge-path, .col-label')];
  assert.ok(deco.length > 0);
  for (const el of deco) assert.equal(el.getAttribute('aria-hidden'), 'true', el.getAttribute('class'));
  done();
});

test('forty dependencies: every node lies inside the drawn canvas, none clipped out of sight', () => {
  const many = Array.from({ length: 40 }, (_, i) => ({ id: `T-${300 + i}`, title: `Dependency ${i + 1}`, status: i % 3 ? 'todo' : 'done' }));
  const { root, done } = mount({ related: { dependencies: many } });
  const svg = root.querySelector('svg.td-graph-svg');
  const [x, y, w, hgt] = svg.getAttribute('viewBox').split(' ').map(Number);
  assert.equal(Number(svg.getAttribute('width')), w, 'drawn at its own size');
  assert.equal(Number(svg.getAttribute('height')), hgt);
  const rects = [...svg.querySelectorAll('.node-rect')];
  assert.equal(rects.length, 41);
  for (const r of rects) {
    const [rx, ry, rw, rh] = ['x', 'y', 'width', 'height'].map((a) => Number(r.getAttribute(a)));
    assert.ok(rx >= x && ry >= y && rx + rw <= x + w && ry + rh <= y + hgt, `${r.parentNode.getAttribute('data-id')} inside the viewBox`);
  }
  assert.ok(svg.parentNode.classList.contains('td-graph-canvas'));
  done();
});

test('a node line that does not fit drops whole parts, never the status word', () => {
  const { root, done } = mount();
  const line = root.querySelector('g.node--center text.node-status').textContent;
  assert.ok(line.startsWith('In progress'), line);
  assert.ok(line === 'In progress · Critical · M' || line.endsWith(' …'), line);
  assert.match(titleOf(root.querySelector('g.node--center')), /In progress · Critical · M/);
  done();
});

test('a neighbour in progress with a priority keeps its status word whole and marks what was dropped', () => {
  const { root, done } = mount({ related: { dependencies: [{ id: 'T-201', title: 'Busy', status: 'in-progress', priority: 'high', estimate: 'M' }] } });
  const a = root.querySelector('a.node[href="#/task/T-201"]');
  const line = a.querySelector('text.node-status').textContent;
  assert.ok(line.startsWith('In progress'), line);
  assert.ok(line.endsWith('…'), line);
  assert.doesNotMatch(line, /progres…/);
  assert.match(titleOf(a), /In progress · High · M$/);
  done();
});

test('a status word the table does not know is shown whole, even when longer than the line', () => {
  const status = 'waiting-on-vendor-signoff';
  const { root, done } = mount({ related: { dependencies: [
    { id: 'T-202', title: 'Alone', status },
    { id: 'T-203', title: 'With more', status, priority: 'low' },
  ] } });
  assert.equal(root.querySelector('a.node[href="#/task/T-202"] text.node-status').textContent, status);
  assert.equal(root.querySelector('a.node[href="#/task/T-203"] text.node-status').textContent, `${status}…`);
  done();
});

test('Fullscreen keeps its name and is pressed while the graph fills the screen; leaving the page leaves it', () => {
  let fsEl = null;
  const fire = () => document.dispatchEvent(new dom.window.Event('fullscreenchange'));
  Object.defineProperty(document, 'fullscreenEnabled', { value: true, configurable: true });
  Object.defineProperty(document, 'fullscreenElement', { get: () => fsEl, configurable: true });
  dom.window.Element.prototype.requestFullscreen = function () { fsEl = this; fire(); return Promise.resolve(); };
  document.exitFullscreen = () => { fsEl = null; fire(); return Promise.resolve(); };
  try {
    const { root, dispose } = mount();
    const full = root.querySelector('[data-test="graph-controls"] [data-focus="graph:fullscreen"]');
    assert.ok(full.matches('.btn.btn--ghost.btn--sm'));
    assert.equal(full.textContent, 'Fullscreen');
    assert.equal(full.getAttribute('aria-pressed'), 'false');
    full.click();
    assert.equal(fsEl, root.querySelector('.td-graph-frame'));
    assert.equal(full.textContent, 'Fullscreen', 'the name stays; the pressed state says it');
    assert.equal(full.getAttribute('aria-pressed'), 'true');
    full.click();
    assert.equal(fsEl, null);
    assert.equal(full.textContent, 'Fullscreen');
    assert.equal(full.getAttribute('aria-pressed'), 'false');
    full.click();
    dispose();
    assert.equal(fsEl, null, 'unmounting the graph ends its fullscreen');
    root.remove();
  } finally {
    delete document.fullscreenEnabled;
    delete document.fullscreenElement;
    delete dom.window.Element.prototype.requestFullscreen;
    delete document.exitFullscreen;
  }
});

test('a repaint keeps the open tab and the hidden context band; tabs and graph buttons carry focus keys', () => {
  const first = mount();
  const raw = [...first.root.querySelectorAll('.td-tab')].find((t) => t.textContent === 'Raw JSON');
  raw.click();
  first.root.querySelector('[data-focus="graph:hide-context"]').click();
  assert.equal(raw.dataset.focus, 'tab:raw');
  assert.equal(first.root.querySelector('.td-tab-panel[data-tab-panel="raw"]').dataset.focus, 'panel:raw');
  const viewState = first.dispose.viewState();
  first.done();
  assert.equal(viewState.tab, 'raw');
  assert.equal(viewState.contextHidden, true);

  const { root, done } = mount({ viewState });
  const selected = root.querySelector('.td-tab[aria-selected="true"]');
  assert.equal(selected.textContent, 'Raw JSON');
  assert.equal(selected.getAttribute('tabindex'), '0');
  assert.equal(root.querySelector('.td-tab-panel[data-tab-panel="raw"]').hidden, false);
  assert.equal(root.querySelector('.td-tab-panel[data-tab-panel="spec"]').hidden, true);
  assert.equal(root.querySelector('[data-test="context-band"]').hidden, true);
  assert.equal(root.querySelector('[data-focus="graph:hide-context"]').getAttribute('aria-pressed'), 'true');
  done();
});

test('unmount clears the page and its classes', () => {
  const { root, done } = mount();
  assert.ok(root.classList.contains('td-page-B'));
  done();
  assert.equal(root.children.length, 0);
  assert.equal(root.classList.contains('td-page-B'), false);
  assert.equal(root.dataset.detailLinks, undefined);
});
