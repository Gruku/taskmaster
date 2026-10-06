// User intent: pin the issue card and the resolved row as link rows — severity marker, evidence clamped to three lines
// with a real "Show all", the stale tag, task and bug links beside the link, nothing nested, no markup from issue data.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const { issueCard, issueRow } = await import('../../js/components/issue-card.js');
const { isInteractive } = await import('../../js/components/link-row.js');

const now = Date.parse('2026-10-06T00:00:00Z');
const fixture = (extra = {}) => ({
  id: 'ISS-001',
  title: 'The poll redraw drops focus to the body when a card is open',
  status: 'investigating',
  severity: 'P1',
  severity_label: 'High',
  discovered: '2026-10-01',
  location: ['viewer/js/x.js:12'],
  evidence: 'Focus lands on <body> after every poll tick.',
  related_tasks: ['T-102'],
  promoted_from: ['B-031'],
  ...extra,
});
const parts = (el) => ({
  link: el.querySelector(':scope > .link-row__link'),
  content: el.querySelector(':scope > .link-row__content'),
  controls: el.querySelector(':scope > .link-row__controls'),
});

test('1: the card is an article link row to the issue, carrying its id and status', () => {
  const el = issueCard(fixture());
  assert.equal(el.tagName, 'ARTICLE');
  assert.ok(el.classList.contains('link-row'));
  assert.ok(el.classList.contains('issue-card'));
  assert.equal(el.dataset.issueId, 'ISS-001');
  assert.equal(el.dataset.status, 'investigating');
  assert.equal(parts(el).link.getAttribute('href'), '#/issue/ISS-001');
  assert.equal(issueCard(fixture({ id: 'ISS 1/2' })).querySelector('.link-row__link').getAttribute('href'), '#/issue/ISS%201%2F2');
});

test('2: the name holds the id with the severity marker on one line and the full title below', () => {
  const el = issueCard(fixture());
  const name = parts(el).link.querySelector(':scope > .issue-card__name');
  assert.ok(name);
  assert.equal(name.tagName, 'SPAN');
  const line = name.querySelector(':scope > .issue-card__line');
  assert.equal(line.querySelector('.issue-card__id').textContent, 'ISS-001');
  assert.equal(line.querySelector('.marker .marker__word').textContent, 'High');
  const title = name.querySelector(':scope > .issue-card__title');
  assert.equal(title.textContent, fixture().title);
  assert.ok(!title.classList.contains('truncate'), 'the title is not cut');
  assert.equal(isInteractive(name), false);
  // No severity at all → no marker.
  const bare = issueCard(fixture({ severity: undefined, severity_label: undefined }));
  assert.equal(bare.querySelector('.issue-card__line .marker'), null);
});

test('3: the content holds the meta line, then the evidence clamped to three lines', () => {
  const el = issueCard(fixture(), { tasksIndex: { 'T-102': { status: 'in-progress' } }, showStatus: true, now });
  const { content } = parts(el);
  const [meta, evidence] = content.children;
  assert.ok(meta.classList.contains('issue-card__meta'));
  assert.equal(meta.querySelector('.marker .marker__word').textContent, 'Investigating');
  assert.equal(meta.querySelector('.issue-card__blocks').textContent, 'Blocks 1 task');
  const loc = meta.querySelector('.issue-card__location');
  assert.equal(loc.textContent, 'viewer/js/x.js:12');
  assert.equal(loc.title, 'viewer/js/x.js:12');
  assert.ok(loc.classList.contains('truncate'));
  assert.equal(evidence.tagName, 'P');
  assert.match(evidence.id, /^issue-evidence-ISS-001-\d+$/);
  assert.ok(evidence.classList.contains('issue-card__evidence'));
  assert.ok(evidence.classList.contains('truncate--3'));
  assert.equal(evidence.textContent, fixture().evidence);
  assert.equal(content.children.length, 2);
});

test('3: blocks counts in the plural, and an issue with nothing to show has no meta and no evidence', () => {
  const tasksIndex = { 'T-1': { status: 'todo' }, 'T-2': { status: 'review' } };
  const el = issueCard(fixture({ related_tasks: ['T-1', 'T-2'] }), { tasksIndex, now });
  assert.equal(el.querySelector('.issue-card__blocks').textContent, 'Blocks 2 tasks');
  const quiet = issueCard({ id: 'ISS-9', title: 'Quiet', status: 'open' }, { now });
  assert.equal(quiet.querySelector('.link-row__content').children.length, 0);
});

test('3: the stale tag sits in the meta line when the issue is in the stale range', () => {
  const el = issueCard(fixture({ discovered: '2026-08-22', aging: { tier: 'Stale' } }), { now });
  assert.equal(el.querySelector('.issue-card__meta .stale-tag .marker__word').textContent, 'stale 45d');
  assert.equal(issueCard(fixture({ aging: { tier: 'Fresh' } }), { now }).querySelector('.stale-tag'), null);
});

test('3: the status marker only shows when asked for', () => {
  assert.equal(issueCard(fixture(), { now }).querySelector('.issue-card__meta .marker'), null);
});

test('4: the controls hold the Show all toggle, then the task and bug links, each with its label', () => {
  const el = issueCard(fixture(), { now });
  const { controls } = parts(el);
  const [more, refs] = controls.children;
  assert.equal(more.tagName, 'BUTTON');
  for (const c of ['btn', 'btn--ghost', 'btn--sm', 'issue-card__more']) assert.ok(more.classList.contains(c), c);
  assert.equal(more.getAttribute('type'), 'button');
  assert.equal(more.getAttribute('aria-controls'), el.querySelector('.issue-card__evidence').id);
  assert.equal(more.dataset.focus, 'evidence:ISS-001');
  assert.ok(refs.classList.contains('issue-card__refs'));
  const kids = [...refs.children].map((n) => [n.className, n.textContent, n.getAttribute('href'), n.dataset.focus]);
  assert.deepEqual(kids, [
    ['issue-card__refs-label', 'Tasks', null, undefined],
    ['issue-card__ref', 'T-102', '#/task/T-102', 'ref:ISS-001:T-102'],
    ['issue-card__refs-label', 'From bugs', null, undefined],
    ['issue-card__ref', 'B-031', '#/bug/B-031', 'ref:ISS-001:B-031'],
  ]);
});

test('4: only task links → no "From bugs" label; no refs at all → no refs block', () => {
  const tasks = issueCard(fixture({ promoted_from: [] }), { now });
  assert.deepEqual([...tasks.querySelectorAll('.issue-card__refs-label')].map((n) => n.textContent), ['Tasks']);
  const none = issueCard(fixture({ related_tasks: [], promoted_from: undefined }), { now });
  assert.equal(none.querySelector('.issue-card__refs'), null);
});

test('nothing is nested: every link and button other than the row link sits under the controls', () => {
  const el = issueCard(fixture(), { now });
  const { link, controls } = parts(el);
  assert.equal(isInteractive(link.querySelector('.issue-card__name')), false);
  for (const n of el.querySelectorAll('a, button')) {
    if (n === link) continue;
    assert.ok(controls.contains(n), `${n.outerHTML} is under the controls`);
  }
  assert.equal(el.querySelector('details'), null);
  assert.equal(el.querySelector('.issue-card__impact, .issue-card__repro'), null);
});

test('an expanded card keeps its evidence unclamped and says so', () => {
  const open = issueCard(fixture(), { expanded: true, now });
  const ev = open.querySelector('.issue-card__evidence');
  const more = open.querySelector('.issue-card__more');
  assert.ok(!ev.classList.contains('truncate--3'));
  assert.equal(more.hidden, false);
  assert.equal(more.getAttribute('aria-expanded'), 'true');
  assert.equal(more.textContent, 'Show less');

  const shut = issueCard(fixture(), { expanded: false, now });
  assert.ok(shut.querySelector('.issue-card__evidence').classList.contains('truncate--3'));
  assert.equal(shut.querySelector('.issue-card__more').hidden, true);
  assert.equal(shut.querySelector('.issue-card__more').getAttribute('aria-expanded'), 'false');
  assert.equal(shut.querySelector('.issue-card__more').textContent, 'Show all');
});

test('a click on the toggle hands the issue id to the screen', () => {
  const seen = [];
  const el = issueCard(fixture(), { expanded: true, onToggleEvidence: (id) => seen.push(id), now });
  el.querySelector('.issue-card__more').dispatchEvent(new window.MouseEvent('click', { bubbles: true }));
  assert.deepEqual(seen, ['ISS-001']);
});

test('no evidence → no toggle and no evidence paragraph', () => {
  const el = issueCard(fixture({ evidence: undefined, symptom: undefined }), { now });
  assert.equal(el.querySelector('.issue-card__more'), null);
  assert.equal(el.querySelector('.issue-card__evidence'), null);
});

test('the toggle shows itself on the frame after connection when the clamped evidence overflows', async () => {
  const frames = [];
  globalThis.requestAnimationFrame = (fn) => { frames.push(fn); return frames.length; };
  try {
    const long = issueCard(fixture(), { now });
    const short = issueCard(fixture({ id: 'ISS-002' }), { now });
    for (const [card, scroll] of [[long, 120], [short, 60]]) {
      const ev = card.querySelector('.issue-card__evidence');
      Object.defineProperty(ev, 'scrollHeight', { value: scroll, configurable: true });
      Object.defineProperty(ev, 'clientHeight', { value: 60, configurable: true });
      document.body.append(card);
    }
    assert.equal(long.querySelector('.issue-card__more').hidden, true, 'still hidden before the frame');
    for (const fn of frames.splice(0)) fn();
    assert.equal(long.querySelector('.issue-card__more').hidden, false);
    assert.equal(short.querySelector('.issue-card__more').hidden, true);
    long.remove();
    short.remove();
  } finally {
    delete globalThis.requestAnimationFrame;
  }
});

test('markup in issue data is text: an impact with an img creates no img', () => {
  const evil = '`<img src=x onerror=alert(1)>`';
  const el = issueCard(fixture({ impact: evil, evidence: evil, title: evil, location: [evil] }), { now });
  assert.equal(el.querySelector('img'), null);
  assert.ok(el.querySelector('.issue-card__title').textContent.includes('<img'));
});

test('issueRow: a won\'t-fix issue is a link row to the issue with its status word and when', () => {
  const el = issueRow({
    id: 'ISS-002', title: 'Old thing', status: 'wontfix', severity: 'P2', resolved: '2026-10-05T00:00:00Z',
  }, { now });
  assert.equal(el.tagName, 'DIV');
  assert.ok(el.classList.contains('link-row'));
  assert.ok(el.classList.contains('issue-row'));
  const link = el.querySelector(':scope > .link-row__link');
  assert.equal(link.getAttribute('href'), '#/issue/ISS-002');
  const [id, sev, title] = link.children;
  assert.equal(id.className, 'issue-row__id');
  assert.equal(id.textContent, 'ISS-002');
  assert.equal(sev.querySelector('.marker__word').textContent, 'Medium');
  assert.ok(title.classList.contains('issue-row__title'));
  assert.equal(title.title, 'Old thing');
  const content = el.querySelector(':scope > .link-row__content');
  assert.equal(content.querySelector('.marker .marker__word').textContent, "Won't fix");
  const when = content.querySelector('time.issue-row__when');
  assert.equal(when.textContent, '1d ago');
  assert.ok(when.title);
  assert.ok(link.title.includes('Old thing'), 'the cut title reaches the link');
});

test('issueRow: no severity → no marker; no resolved or updated date → no time', () => {
  const el = issueRow({ id: 'ISS-003', title: 'Plain', status: 'fixed' }, { now });
  assert.equal(el.querySelector('.link-row__link .marker'), null);
  assert.equal(el.querySelector('time'), null);
  const upd = issueRow({ id: 'ISS-004', title: 'U', status: 'fixed', updated: '2026-10-04T00:00:00Z' }, { now });
  assert.equal(upd.querySelector('time.issue-row__when').textContent, '2d ago');
});

// A stub ResizeObserver: the test decides when an observation is delivered, as layout would.
function stubResizeObserver() {
  const live = new Set();
  globalThis.ResizeObserver = class {
    constructor(cb) { this.cb = cb; this.targets = new Set(); }
    observe(el) { this.targets.add(el); live.add(this); }
    unobserve(el) { this.targets.delete(el); }
    disconnect() { this.targets.clear(); live.delete(this); }
  };
  return {
    live,
    deliver() { for (const o of [...live]) o.cb([...o.targets].map((target) => ({ target })), o); },
    restore() { delete globalThis.ResizeObserver; },
  };
}
const sized = (card, scroll, client) => {
  const ev = card.querySelector('.issue-card__evidence');
  Object.defineProperty(ev, 'scrollHeight', { value: scroll, configurable: true });
  Object.defineProperty(ev, 'clientHeight', { value: client, configurable: true });
};

test('a card appended after its first frame still reveals Show all on the first observation that overflows', () => {
  const ro = stubResizeObserver();
  const frames = [];
  globalThis.requestAnimationFrame = (fn) => { frames.push(fn); return frames.length; };
  try {
    const card = issueCard(fixture(), { now });
    sized(card, 120, 60);
    for (const fn of frames.splice(0)) fn(); // the frame passes before the screen appends the card
    assert.equal(card.querySelector('.issue-card__more').hidden, true);
    document.body.append(card);
    ro.deliver();
    assert.equal(card.querySelector('.issue-card__more').hidden, false);
    card.remove();
  } finally {
    ro.restore();
    delete globalThis.requestAnimationFrame;
  }
});

test('a card in a hidden ancestor keeps Show all hidden until an observation reports overflow', () => {
  const ro = stubResizeObserver();
  try {
    const host = document.createElement('div');
    host.hidden = true;
    document.body.append(host);
    const card = issueCard(fixture(), { now });
    host.append(card);
    sized(card, 0, 0); // display: none — nothing laid out
    ro.deliver();
    assert.equal(card.querySelector('.issue-card__more').hidden, true);
    host.hidden = false;
    sized(card, 120, 60);
    ro.deliver();
    assert.equal(card.querySelector('.issue-card__more').hidden, false);
    host.remove();
  } finally {
    ro.restore();
  }
});

test('evidence that fits keeps Show all hidden on every observation', () => {
  const ro = stubResizeObserver();
  try {
    const card = issueCard(fixture(), { now });
    document.body.append(card);
    sized(card, 61, 60);
    ro.deliver();
    ro.deliver();
    assert.equal(card.querySelector('.issue-card__more').hidden, true);
    card.remove();
  } finally {
    ro.restore();
  }
});

test('the observer lets go once it has revealed the toggle or sees the card leave the page, and an expanded card never observes', () => {
  const ro = stubResizeObserver();
  try {
    // An observation before the screen appends the card (a browser's first one) keeps the observer.
    const early = issueCard(fixture({ id: 'ISS-003' }), { now });
    sized(early, 120, 60);
    ro.deliver();
    assert.equal(ro.live.size, 1, 'a card not yet in the page stays watched');
    document.body.append(early);
    ro.deliver();
    assert.equal(early.querySelector('.issue-card__more').hidden, false);
    assert.equal(ro.live.size, 0, 'revealed → let go');

    const gone = issueCard(fixture({ id: 'ISS-002' }), { now });
    document.body.append(gone);
    sized(gone, 60, 60);
    ro.deliver();
    assert.equal(ro.live.size, 1, 'fits and in the page → still watched for a resize');
    gone.remove();
    ro.deliver();
    assert.equal(ro.live.size, 0, 'seen in the page, now out → let go');

    issueCard(fixture(), { expanded: true, now });
    assert.equal(ro.live.size, 0);
    early.remove();
  } finally {
    ro.restore();
  }
});

test('the evidence id is space-free and unique per card, and the toggle controls it', () => {
  const a = issueCard(fixture({ id: 'ISS 7/x' }), { now });
  const b = issueCard(fixture({ id: 'ISS 7/x' }), { now });
  const ids = [a, b].map((c) => c.querySelector('.issue-card__evidence').id);
  for (const id of ids) assert.match(id, /^issue-evidence-ISS-7-x-\d+$/);
  assert.notEqual(ids[0], ids[1]);
  assert.equal(a.querySelector('.issue-card__more').getAttribute('aria-controls'), ids[0]);
});
