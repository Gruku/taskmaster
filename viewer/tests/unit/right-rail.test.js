import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { RightRail, mountRightRail, railPanels, statusPill, openStatusMenu, HO_STATUS_LABEL } from '../../js/components/right-rail.js';
import { openPopoverCount } from '../../js/components/popover.js';

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

// ── The generic rail: a labelled panel in its host, built from nodes, that takes and returns focus ──
const MODAL_PAGE = '<div class="shell"><button id="opener">o</button><section id="screen-mount"><div id="host"></div><a href="#x" id="fallback">f</a></section></div><div id="modal-host"></div>';
function railPage(markup = '<div id="host"></div>') {
  const dom = page(`<!doctype html><body>${markup}</body>`);
  global.HTMLElement = dom.window.HTMLElement;
  return { dom, host: document.getElementById('host') };
}
const para = (text) => { const el = document.createElement('p'); el.textContent = text; return el; };
const tick = () => new Promise((r) => setTimeout(r, 0));

test('open() puts a labelled panel in its host and focuses its title', () => {
  const { host } = railPage();
  const rail = new RightRail({ host });
  const body = para('Cards are done.');
  rail.open({ title: 'M1 shipped', body: [body] });
  const aside = host.querySelector('aside#right-rail');
  assert.ok(aside, 'the rail is in its host');
  assert.ok(aside.classList.contains('right-rail'));
  assert.ok(aside.classList.contains('right-rail--plain'));
  assert.equal(aside.getAttribute('aria-label'), 'Details');
  assert.equal(document.getElementById(aside.getAttribute('aria-labelledby')).textContent, 'M1 shipped');
  assert.equal(document.activeElement, aside.querySelector('.rr-title'));
  assert.equal(document.activeElement.tagName, 'H2');
  assert.equal(document.activeElement.getAttribute('tabindex'), '-1');
  const close = aside.querySelector('.rr-h > button.rr-close');
  assert.equal(close.getAttribute('aria-label'), 'Close details');
  assert.ok(close.querySelector('.icon'));
  assert.ok(aside.contains(body));
  assert.equal(document.body.classList.contains('rail-open'), false);
  assert.equal(rail.isOpen(), true);
  close.click();
  assert.equal(host.querySelector('#right-rail'), null);
  assert.equal(rail.isOpen(), false);
});

test('open() twice swaps the content', () => {
  const { host } = railPage('<div id="host"><button id="row">row</button></div>');
  const opener = document.getElementById('row');
  let openerFocused = 0;
  opener.addEventListener('focus', () => { openerFocused += 1; });
  const rail = new RightRail({ host, label: 'Session details' });
  const closed = [];
  rail.open({ title: 'first', body: [para('a')], opener, onClose: () => closed.push('first') });
  rail.open({ kind: 'session', title: 'second', body: [para('b')] });
  // Focus goes from the old title straight to the new one: the old opener is not focused (and scrolled to) between.
  assert.equal(openerFocused, 0);
  assert.equal(document.activeElement, host.querySelector('.rr-title'));
  assert.equal(host.querySelectorAll('aside').length, 1);
  assert.deepEqual(closed, ['first']);
  const aside = host.querySelector('aside#right-rail');
  assert.ok(aside.classList.contains('right-rail--session'));
  assert.equal(aside.getAttribute('aria-label'), 'Session details');
  assert.equal(document.getElementById(aside.getAttribute('aria-labelledby')).textContent, 'second');
  assert.ok(!aside.textContent.includes('first'));
  rail.close();
});

test('close() hands focus back to the opener and runs onClose once', () => {
  const { host } = railPage('<div id="host"><button id="row">row</button></div>');
  const opener = document.getElementById('row');
  const rail = new RightRail({ host });
  let calls = 0;
  rail.open({ title: 'x', opener, onClose: () => { calls += 1; } });
  assert.ok(host.querySelector('#right-rail').contains(document.activeElement));
  rail.close();
  rail.close();
  assert.equal(calls, 1);
  assert.equal(document.activeElement, opener);
});

test('Escape closes the rail unless a menu or a modal took it', async () => {
  const { dom, host } = railPage();
  const rail = new RightRail({ host });
  const esc = (target) => target.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));

  rail.open({ title: 'x' });
  esc(document.activeElement);
  assert.equal(rail.isOpen(), false, 'a plain Escape closes it');

  rail.open({ title: 'x' });
  const taken = (e) => e.preventDefault();
  document.addEventListener('keydown', taken, true);
  esc(document.activeElement);
  document.removeEventListener('keydown', taken, true);
  assert.equal(rail.isOpen(), true, 'an Escape already used stays with whoever used it');
  rail.close();

  // A modal over the screen takes the key: Escape typed on the page behind it closes the modal, not the rail.
  const under = railPage(MODAL_PAGE);
  const { openModal, openModalCount } = await import('../../js/components/modal.js');
  const rail2 = new RightRail({ host: under.host });
  rail2.open({ title: 'under a modal' });
  openModal({ title: 'x' });
  await tick();
  assert.equal(openModalCount(), 1);
  document.body.dispatchEvent(new under.dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  await tick();
  assert.equal(openModalCount(), 0, 'the modal closes');
  assert.equal(rail2.isOpen(), true, 'the rail stays open');
  rail2.close();
});

test('a host taken out of the page gets no rail and no key listener', () => {
  const { host } = railPage();
  const rail = new RightRail({ host });
  host.remove();
  const types = [];
  const add = document.addEventListener;
  document.addEventListener = function (type, ...rest) { types.push(type); return add.call(this, type, ...rest); };
  try {
    assert.equal(rail.open({ title: 'late' }), null);
  } finally {
    document.addEventListener = add;
  }
  assert.deepEqual(types, []);
  assert.equal(rail.isOpen(), false);
  assert.equal(host.querySelector('#right-rail'), null);
});

test('a rail without a host refuses', () => {
  railPage();
  assert.throws(() => new RightRail({}), TypeError);
  assert.throws(() => new RightRail(), TypeError);
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
    assert.match(p.textContent, /Closed/);
  }
  assert.deepEqual(heard, [{ id: 'HO-1', status: 'closed' }]);
  assert.equal(document.querySelector('.ho-status-menu'), null);
  assert.equal(document.activeElement, pill);
});

// ── A refused status change is said in words beside the pill it was chosen from ──
const reply = (status, body = '') => async () => ({ ok: status >= 200 && status < 300, status, text: async () => body });
async function choose(pill, word) {
  pill.click();
  [...document.querySelectorAll('.ho-status-menu [role="menuitemradio"]')].find((i) => i.textContent.trim() === word).click();
  await tick();
  await tick();
}

test('a failed status change is said beside the pill and the pill keeps its status', async () => {
  const { pill } = menuPage();
  const twin = statusPill('HO-1', 'open');
  document.getElementById('elsewhere').after(twin);
  global.CSS = { escape: (s) => s };

  global.fetch = reply(500, '{"error":"sqlite3.OperationalError: database is locked"}');
  await choose(pill, 'closed');
  let alert = pill.nextElementSibling;
  assert.ok(alert.matches('span.ho-status-error[role="alert"][id]'));
  assert.equal(alert.textContent, 'The server could not save this change. Try again in a moment.');
  assert.equal(pill.getAttribute('aria-describedby'), alert.id);
  for (const pp of [pill, twin]) {
    assert.equal(pp.dataset.status, 'open');
    assert.ok(pp.classList.contains('ho-status-pill-open'));
    assert.match(pp.textContent, /Open/);
  }
  assert.ok(!twin.nextElementSibling?.classList.contains('ho-status-error'), 'the twin pill has no alert');
  assert.equal(twin.hasAttribute('aria-describedby'), false);

  global.fetch = reply(409, '{"error":"Handover is already superseded by 2026-10-02-wrap"}');
  await choose(pill, 'closed');
  assert.equal(document.querySelectorAll('.ho-status-error').length, 1, 'one message replaces the other');
  alert = pill.nextElementSibling;
  assert.equal(alert.textContent, 'Handover is already superseded by 2026-10-02-wrap');
  assert.equal(pill.getAttribute('aria-describedby'), alert.id);

  global.fetch = async () => { throw new TypeError('Failed to fetch'); };
  await choose(pill, 'closed');
  assert.equal(document.querySelectorAll('.ho-status-error').length, 1);
  assert.equal(pill.nextElementSibling.textContent, 'Could not reach the server, so nothing was saved. Check that the viewer is still running.');
  assert.equal(pill.dataset.status, 'open');

  global.fetch = async () => ({ ok: true });
  await choose(pill, 'closed');
  assert.equal(document.querySelector('.ho-status-error'), null);
  assert.equal(pill.hasAttribute('aria-describedby'), false);
  assert.equal(pill.dataset.status, 'closed');
  assert.equal(twin.dataset.status, 'closed');
});

test('a closed menu does not stay remembered', () => {
  const { dom, pill: a } = menuPage();
  const b = statusPill('HO-2', 'closed');
  document.body.appendChild(b);
  a.click();
  assert.ok(document.querySelector('.ho-status-menu'));
  document.querySelector('.ho-status-menu [role="menuitemradio"]')
    .dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  assert.equal(document.querySelector('.ho-status-menu'), null);
  // The same pill again: its first click opens its menu (a remembered menu would read as "close it").
  a.click();
  assert.ok(document.querySelector('.ho-status-menu'), 'the first click on the same pill opens its menu again');
  assert.equal(a.getAttribute('aria-expanded'), 'true');
  assert.equal(openPopoverCount(), 1);
  document.querySelector('.ho-status-menu [role="menuitemradio"]')
    .dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true }));
  assert.equal(openPopoverCount(), 0);
  b.click();
  const menu = document.querySelector('.ho-status-menu');
  assert.ok(menu, 'the first click on the next pill opens its menu');
  assert.equal(b.getAttribute('aria-expanded'), 'true');
  assert.equal(b.getAttribute('aria-controls'), menu.id);
  assert.equal(openPopoverCount(), 1);
  b.click();
  assert.equal(openPopoverCount(), 0);
});

test('the status words are named once', () => {
  assert.equal(HO_STATUS_LABEL.superseded, 'Superseded');
  assert.deepEqual(Object.keys(HO_STATUS_LABEL), ['open', 'closed', 'superseded']);
  assert.ok(Object.isFrozen(HO_STATUS_LABEL));
});
