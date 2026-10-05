import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { RightRail, mountRightRail, railPanels, statusPill, openStatusMenu } from '../../js/components/right-rail.js';

function page(html = '<!doctype html><body><aside></aside></body>') {
  const dom = new JSDOM(html, { url: 'http://localhost/' });
  global.document = dom.window.document;
  global.window = dom.window;
  return dom;
}

const RELATED = {
  dependencies: [{ id: 'T-101', title: 'Token foundation', status: 'done' }, { id: 'T-105', title: 'Index rebuild', status: 'todo' }],
  unblocks: [{ id: 'T-104', title: 'Sessions timeline', status: 'in-progress' }],
  handovers: [{ id: '2026-09-30-kanban', kind: 'checkpoint', status: 'open', created: '2026-09-30T16:20:00Z', quote: '## Run summary\n\nCards done.' }],
  issues: [{ id: 'ISS-012', title: 'Card edge vanishes in light', severity: 'High' }],
};

test('legacy string blockers render without breaking task detail', () => {
  page();
  const aside = document.querySelector('aside');
  mountRightRail(aside, { task: { blockers: 'Waiting for review' }, related: {}, onNavigate() {} });
  assert.equal(aside.querySelector('.td-blocker').textContent, 'Waiting for review');
});

test('open() injects rendered content; close() removes it', () => {
  page('<!doctype html><body></body>');
  const rail = new RightRail({ width: 480 });
  rail.open({ render: () => '<div id="x">hi</div>' });
  assert.ok(document.querySelector('.right-rail'));
  assert.ok(document.querySelector('#x'));
  rail.close();
  assert.equal(document.querySelector('.right-rail'), null);
});

test('open() twice swaps the content', () => {
  page('<!doctype html><body></body>');
  const rail = new RightRail();
  rail.open({ render: () => '<div id="a">first</div>' });
  rail.open({ render: () => '<div id="b">second</div>' });
  assert.equal(document.querySelector('#a'), null);
  assert.ok(document.querySelector('#b'));
  rail.close();
});

test('a task with nothing related has no panels at all', () => {
  page();
  const aside = document.querySelector('aside');
  const empty = { task: { docs: {}, blockers: [], links: [], depends_on: [] }, related: { handovers: [], issues: [], dependencies: [], unblocks: [] } };
  assert.deepEqual(railPanels(empty), []);
  mountRightRail(aside, empty);
  assert.equal(aside.children.length, 0);
  assert.equal(aside.textContent, '');
});

test('null task and null related do not throw and give no panels', () => {
  page();
  const aside = document.querySelector('aside');
  assert.doesNotThrow(() => mountRightRail(aside, { task: null, related: null, onNavigate: () => {} }));
  assert.equal(aside.children.length, 0);
  assert.deepEqual(railPanels({}), []);
});

test('wrong-typed relation data is shown where it can be and skipped where it cannot', () => {
  page();
  const aside = document.querySelector('aside');
  assert.doesNotThrow(() => mountRightRail(aside, {
    task: { docs: ['not', 'a', 'map'], links: 'T-1', blockers: [null, { text: 'Needs a decision' }, { why: 'odd' }, 7], depends_on: null },
    related: { dependencies: 'T-1', unblocks: [null, 'T-9', { id: 'T-2' }], handovers: {}, issues: [{ id: 'ISS-1' }, null] },
  }));
  assert.deepEqual([...aside.querySelectorAll('.td-blocker')].map((b) => b.textContent), ['Needs a decision', '{"why":"odd"}', '7']);
  assert.deepEqual([...aside.querySelectorAll('[data-sub="unblocks"] .td-dep__id')].map((b) => b.textContent), ['T-9', 'T-2']);
  assert.equal(aside.querySelector('[data-panel="docs"]'), null);
  assert.equal(aside.querySelector('[data-panel="handovers"]'), null);
  assert.equal(aside.querySelectorAll('[data-panel="issues"] a').length, 1);
});

test('one Relations panel holds links, dependencies, unblocks and blockers — each only when it has something', () => {
  page();
  const aside = document.querySelector('aside');
  mountRightRail(aside, {
    task: { links: [{ type: 'relates_to', target: 'ISS-012' }], blockers: ['Waiting on the design review'], docs: { spec: 'docs/spec.md' } },
    related: RELATED,
  });
  assert.deepEqual([...aside.querySelectorAll('.td-panel')].map((p) => p.dataset.panel), ['relations', 'docs', 'handovers', 'issues']);
  const relations = aside.querySelector('[data-panel="relations"]');
  assert.equal(relations.querySelector('.td-rail-h').textContent, 'Relations');
  assert.deepEqual([...relations.querySelectorAll('[data-sub]')].map((s) => s.dataset.sub), ['links', 'depends', 'unblocks', 'blockers']);
  assert.deepEqual([...relations.querySelectorAll('.td-rail-sub')].map((s) => s.textContent), ['Links', 'Depends on', 'Unblocks', 'Blockers']);

  mountRightRail(aside, { task: {}, related: { unblocks: RELATED.unblocks } });
  assert.deepEqual([...aside.querySelectorAll('[data-sub]')].map((s) => s.dataset.sub), ['unblocks']);
  assert.deepEqual([...aside.querySelectorAll('.td-panel')].map((p) => p.dataset.panel), ['relations']);
});

test('a related task is a link that shows its id, its title and its status marker', () => {
  page();
  const aside = document.querySelector('aside');
  mountRightRail(aside, { task: {}, related: RELATED });
  const rows = [...aside.querySelectorAll('[data-sub="depends"] a.td-dep')];
  assert.equal(rows.length, 2);
  assert.equal(rows[0].getAttribute('href'), '#/task/T-101');
  assert.equal(rows[0].querySelector('.td-dep__id').textContent, 'T-101');
  assert.equal(rows[0].querySelector('.td-dep__title').textContent, 'Token foundation');
  assert.equal(rows[0].querySelector('.marker__word').textContent, 'Done');
  assert.ok(rows[0].querySelector('.marker').classList.contains('marker--success'));
  assert.equal(rows[1].querySelector('.marker__word').textContent, 'Todo');
  // Nothing clickable that is not a link or a button.
  assert.equal(aside.querySelector('li[onclick], li.td-dep'), null);
});

test('dependencies listed as relations are not repeated as link pills', () => {
  page();
  const aside = document.querySelector('aside');
  mountRightRail(aside, { task: { depends_on: ['T-101', 'T-105'], related_issues: ['ISS-012'] }, related: RELATED });
  assert.deepEqual([...aside.querySelectorAll('a.link-pill')].map((a) => a.textContent), ['Related ISS-012']);
  // Without the resolved list the ids are still reachable, as pills.
  mountRightRail(aside, { task: { depends_on: ['T-101'] }, related: {} });
  assert.deepEqual([...aside.querySelectorAll('a.link-pill')].map((a) => a.textContent), ['Depends on T-101']);
});

test('a handover shows its rendered summary without quote marks, and a status button', () => {
  page();
  const aside = document.querySelector('aside');
  mountRightRail(aside, { task: {}, related: { handovers: RELATED.handovers } });
  const ho = aside.querySelector('.td-handover');
  const quote = ho.querySelector('.td-handover-quote');
  assert.ok(quote.classList.contains('md-body'));
  assert.equal(quote.tagName, 'DIV');
  assert.ok(!quote.classList.contains('serif'));
  assert.ok(!/^["“]/.test(quote.textContent.trim()), 'no added quote marks');
  assert.match(quote.textContent, /Run summary/);
  const pill = ho.querySelector('.ho-status-pill');
  assert.equal(pill.tagName, 'BUTTON');
  assert.equal(pill.getAttribute('type'), 'button');
  assert.equal(pill.getAttribute('aria-haspopup'), 'menu');
  assert.equal(pill.getAttribute('aria-expanded'), 'false');
  assert.match(pill.textContent, /open/i);
  // The time is relative, with the absolute form in the tooltip — not a raw ISO string.
  assert.ok(!ho.textContent.includes('2026-09-30T16:20'));
  assert.ok(ho.querySelector('time[title]'));
});

test('an issue is a link with its severity as a shape and a word', () => {
  page();
  const aside = document.querySelector('aside');
  mountRightRail(aside, { task: {}, related: { issues: RELATED.issues } });
  const row = aside.querySelector('[data-panel="issues"] a.td-issue');
  assert.equal(row.getAttribute('href'), '#/issue/ISS-012');
  assert.equal(row.querySelector('.marker__word').textContent, 'High');
  assert.ok(row.querySelector('.marker__shape'));
});

test('docs list each document once, as a link named by its type', () => {
  page();
  const aside = document.querySelector('aside');
  mountRightRail(aside, { task: { docs: { spec: 'docs/specs/a.md', review: 'https://example.com/r/1' } }, related: {} });
  const links = [...aside.querySelectorAll('[data-panel="docs"] a.td-doc-link')];
  assert.deepEqual(links.map((a) => a.querySelector('.td-doc-type').textContent), ['spec', 'review']);
  assert.equal(links[0].getAttribute('href'), '/file/docs/specs/a.md');
  assert.equal(links[1].getAttribute('href'), 'https://example.com/r/1');
  assert.equal(links[1].getAttribute('rel'), 'noopener noreferrer');
});

// ── Handover status menu ──
function menuPage() {
  const dom = page('<!doctype html><body><div id="host"></div><button id="elsewhere">x</button></body>');
  const pill = statusPill('HO-1', 'open');
  document.getElementById('host').appendChild(pill);
  return { dom, pill };
}
const key = (dom, target, k) => target.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true }));

test('the status menu opens from the button, marks the current status, and takes focus', () => {
  const { pill } = menuPage();
  pill.click();
  const menu = document.querySelector('.ho-status-menu');
  assert.ok(menu, 'menu is open');
  assert.equal(menu.getAttribute('role'), 'menu');
  assert.equal(pill.getAttribute('aria-expanded'), 'true');
  const items = [...menu.querySelectorAll('[role="menuitemradio"]')];
  assert.deepEqual(items.map((i) => i.textContent.trim()), ['open', 'closed', 'superseded']);
  assert.deepEqual(items.map((i) => i.getAttribute('aria-checked')), ['true', 'false', 'false']);
  assert.equal(document.activeElement, items[0]);
});

test('Escape closes the menu, hands focus back to the button, and keeps the key from a modal around it', () => {
  const { dom, pill } = menuPage();
  pill.click();
  const item = document.querySelector('.ho-status-menu [role="menuitemradio"]');
  const ev = new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
  item.dispatchEvent(ev);
  assert.equal(document.querySelector('.ho-status-menu'), null);
  assert.equal(pill.getAttribute('aria-expanded'), 'false');
  assert.equal(document.activeElement, pill);
  assert.equal(ev.defaultPrevented, true);
});

test('a press outside closes the menu; a second click on the button closes it too', () => {
  const { dom, pill } = menuPage();
  pill.click();
  document.getElementById('elsewhere').dispatchEvent(new dom.window.MouseEvent('pointerdown', { bubbles: true }));
  assert.equal(document.querySelector('.ho-status-menu'), null);
  assert.equal(pill.getAttribute('aria-expanded'), 'false');
  pill.click();
  assert.ok(document.querySelector('.ho-status-menu'));
  pill.click();
  assert.equal(document.querySelector('.ho-status-menu'), null);
});

test('arrow keys move through the menu and wrap', () => {
  const { dom, pill } = menuPage();
  pill.click();
  const items = [...document.querySelectorAll('.ho-status-menu [role="menuitemradio"]')];
  key(dom, items[0], 'ArrowDown');
  assert.equal(document.activeElement, items[1]);
  key(dom, items[1], 'ArrowUp');
  key(dom, items[0], 'ArrowUp');
  assert.equal(document.activeElement, items[2]);
});

test('choosing a status posts it, updates every pill for that handover, and returns focus', async () => {
  const { pill } = menuPage();
  const twin = statusPill('HO-1', 'open');
  document.body.appendChild(twin);
  const calls = [];
  global.fetch = async (url, init) => { calls.push({ url, init }); return { ok: true }; };
  global.CSS = { escape: (s) => s };
  const heard = [];
  window.addEventListener('viewer:handover-status-changed', (e) => heard.push(e.detail));
  openStatusMenu(pill, 'HO-1', 'open');
  document.querySelectorAll('.ho-status-menu [role="menuitemradio"]')[1].click();
  await new Promise((r) => setTimeout(r, 0));
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, '/api/handover/HO-1/status');
  assert.deepEqual(JSON.parse(calls[0].init.body), { status: 'closed', reason: 'viewer-override' });
  for (const p of [pill, twin]) {
    assert.equal(p.dataset.status, 'closed');
    assert.ok(p.classList.contains('ho-status-pill-closed'));
    assert.ok(!p.classList.contains('ho-status-pill-open'));
    assert.match(p.textContent, /closed/);
  }
  assert.deepEqual(heard, [{ id: 'HO-1', status: 'closed' }]);
  assert.equal(document.querySelector('.ho-status-menu'), null);
  assert.equal(document.activeElement, pill);
});
