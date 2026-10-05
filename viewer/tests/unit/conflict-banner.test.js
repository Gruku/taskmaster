// viewer/tests/unit/conflict-banner.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body><div id="conflict-banner-host"></div></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const { showFieldConflict } = await import('../../js/components/edit/conflict-banner.js');

test('field conflict shows local + server values', () => {
  const close = showFieldConflict({
    entityKind: 'task', entityId: 'e1-001',
    fieldKey: 'title', fieldLabel: 'Title',
    localValue: 'My version', currentValue: 'Server version',
    currentEtag: 'abc', onKeepMine: async () => {}, onUseServer: () => {},
  });
  const banner = document.querySelector('.cb-banner');
  assert.ok(banner);
  assert.match(banner.textContent, /My version/);
  assert.match(banner.textContent, /Server version/);
  close();
  assert.equal(document.querySelector('.cb-banner'), null);
});

test('Keep mine button calls onKeepMine and dismisses', async () => {
  let called = false;
  showFieldConflict({
    entityKind: 'task', entityId: 'e1-001',
    fieldKey: 'title', fieldLabel: 'Title',
    localValue: 'a', currentValue: 'b', currentEtag: 'x',
    onKeepMine: async () => { called = true; },
    onUseServer: () => {},
  });
  document.querySelector('.cb-keep-mine').click();
  await new Promise(r => setTimeout(r, 5));
  assert.equal(called, true);
  assert.equal(document.querySelector('.cb-banner'), null);
});

test('Use server button calls onUseServer and dismisses', () => {
  let called = false;
  showFieldConflict({
    entityKind: 'task', entityId: 'e1-001',
    fieldKey: 'title', fieldLabel: 'Title',
    localValue: 'a', currentValue: 'b', currentEtag: 'x',
    onKeepMine: async () => {},
    onUseServer: () => { called = true; },
  });
  document.querySelector('.cb-use-server').click();
  assert.equal(called, true);
  assert.equal(document.querySelector('.cb-banner'), null);
});

// The banner's buttons are the shared buttons: the accent fill carries its own readable text colour in both themes.
test('the banner buttons are the shared primary and secondary buttons', async () => {
  const { showFullConflict } = await import('../../js/components/edit/conflict-banner.js');
  const classes = (sel) => document.querySelector(sel).className.split(' ').sort();
  const close = showFieldConflict({
    entityKind: 'task', entityId: 'e1-001', fieldKey: 'title', fieldLabel: 'Title',
    localValue: 'a', currentValue: 'b', currentEtag: 'x', onKeepMine: async () => {}, onUseServer: () => {},
  });
  assert.deepEqual(classes('.cb-keep-mine'), ['btn', 'btn--primary', 'cb-keep-mine']);
  assert.deepEqual(classes('.cb-use-server'), ['btn', 'btn--secondary', 'cb-use-server']);
  close();
  const closeFull = showFullConflict({
    entityKind: 'task', entityId: 'e1-001', localDraft: { title: 'a' }, currentValue: { title: 'b' },
    currentEtag: 'x', onResolve: async () => {}, onDismiss: () => {},
  });
  assert.deepEqual(classes('.cb-resolve'), ['btn', 'btn--primary', 'cb-resolve']);
  assert.deepEqual(classes('.cb-dismiss'), ['btn', 'btn--secondary', 'cb-dismiss']);
  closeFull();
});

// The same emptiness the form uses: a field one side left blank and the other never set is not a difference (M-4).
test('the full banner lists only real differences: null, "", [] and {} are one emptiness, maps compare by key', async () => {
  const { showFullConflict } = await import('../../js/components/edit/conflict-banner.js');
  const close = showFullConflict({
    entityKind: 'task', entityId: 'e1-001',
    localDraft: { title: 'mine', notes: '', anchors: [], docs: { a: '1', b: '2' }, branch: null },
    currentValue: { title: 'theirs', notes: null, anchors: null, docs: { b: '2', a: '1' }, branch: {} },
    currentEtag: 'x', onResolve: async () => {}, onDismiss: () => {},
  });
  assert.deepEqual([...document.querySelectorAll('.cb-multi-row .cb-key')].map((e) => e.textContent), ['Title']);
  close();
});

// ── Plan 2b: labels, named choices, a headline that reads in both themes, and room made for it ──
const full = async (extra = {}) => {
  const { showFullConflict } = await import('../../js/components/edit/conflict-banner.js');
  return showFullConflict({
    entityKind: 'task', entityId: 'T-102',
    localDraft: { title: 'mine', depends_on: ['T-1'] }, currentValue: { title: 'theirs', depends_on: ['T-2'] },
    currentEtag: 'x', onResolve: async () => {}, onDismiss: () => {}, ...extra,
  });
};
const keys = () => [...document.querySelectorAll('.cb-multi-row .cb-key')].map((e) => e.textContent);
const byId = (id) => document.getElementById(id);

test('the full banner names each field by its form label; a field without one is sentence-cased', async () => {
  const close = await full({ labels: { title: 'Title' } });
  assert.deepEqual(keys(), ['Title', 'Depends on']);
  close();
});

test('the banner is a region named by its headline; the headline is an alert with a Conflict marker and a plain sentence', async () => {
  const close = await full();
  const banner = document.querySelector('.cb-banner');
  assert.equal(banner.getAttribute('role'), 'region');
  const headline = byId(banner.getAttribute('aria-labelledby'));
  assert.ok(headline.classList.contains('cb-headline'));
  assert.equal(headline.getAttribute('role'), 'alert');
  const m = headline.querySelector('.marker.marker--warning');
  assert.equal(m.querySelector('.marker__shape').textContent, '▲');
  assert.equal(m.querySelector('.marker__word').textContent, 'Conflict');
  assert.match(headline.querySelector('.cb-sentence').textContent,
    /^Task T-102 was changed by someone else — choose what to keep for each field$/);
  close();
  const closeField = showFieldConflict({
    entityKind: 'task', entityId: 'T-102', fieldKey: 'title', fieldLabel: 'Title',
    localValue: 'a', currentValue: 'b', currentEtag: 'x', onKeepMine: async () => {}, onUseServer: () => {},
  });
  const fb = document.querySelector('.cb-banner');
  assert.equal(fb.getAttribute('role'), 'region');
  const fh = byId(fb.getAttribute('aria-labelledby'));
  assert.equal(fh.getAttribute('role'), 'alert');
  assert.equal(fh.querySelector('.marker__word').textContent, 'Conflict');
  assert.equal(fh.querySelector('.cb-sentence').textContent, '"Title" on task T-102 was changed by someone else');
  closeField();
});

test('each row shows Yours and Saved, cut to three lines with the full text kept', async () => {
  const close = await full({ labels: { title: 'Title' } });
  const row = document.querySelector('.cb-multi-row');
  assert.deepEqual([...row.querySelectorAll('.cb-tag')].map((e) => e.textContent), ['Yours', 'Saved']);
  const mine = row.querySelector('.cb-val-mine');
  const server = row.querySelector('.cb-val-server');
  for (const [el, text] of [[mine, 'mine'], [server, 'theirs']]) {
    assert.ok(el.classList.contains('truncate') && el.classList.contains('truncate--3'));
    assert.equal(el.textContent, text);
    assert.equal(el.title, text);
  }
  close();
});

test('each choice is a radiogroup labelled by its field, with Keep mine and Use server radios', async () => {
  const close = await full({ labels: { title: 'Title', depends_on: 'Depends on' } });
  const groups = [...document.querySelectorAll('.cb-multi-row [role="radiogroup"]')];
  assert.equal(groups.length, 2);
  assert.deepEqual(groups.map((g) => byId(g.getAttribute('aria-labelledby'))?.textContent), ['Title', 'Depends on']);
  for (const g of groups) {
    const radios = [...g.querySelectorAll('input[type="radio"]')];
    assert.deepEqual(radios.map((r) => r.closest('label').textContent), ['Keep mine', 'Use server']);
    assert.equal(new Set(radios.map((r) => r.name)).size, 1, 'one group, one name');
    assert.equal(radios[0].checked, true, 'keeping mine is the default');
  }
  assert.notEqual(groups[0].querySelector('input').name, groups[1].querySelector('input').name);
  close();
});

test('two banners shown one after the other never share a radio name or an id', async () => {
  const names = async () => {
    const close = await full();
    const out = { names: [...document.querySelectorAll('.cb-banner input')].map((r) => r.name),
      ids: [...document.querySelectorAll('.cb-banner [id]')].map((e) => e.id) };
    close();
    return out;
  };
  const a = await names();
  const b = await names();
  assert.equal(a.names.filter((n) => b.names.includes(n)).length, 0);
  assert.equal(a.ids.filter((n) => b.ids.includes(n)).length, 0);
  assert.match(a.names[0], /^cb-\d+-title$/);
});

test('conflictValueText: empties are a dash, lists are joined, maps are key: value lines', async () => {
  const { conflictValueText } = await import('../../js/components/edit/conflict-banner.js');
  const table = [
    [null, '—'], [undefined, '—'], ['', '—'], [[], '—'], [{}, '—'],
    [['a', 'b'], 'a, b'],
    [{ spec: 'a.md', plan: 'b.md' }, 'spec: a.md\nplan: b.md'],
    [{ spec: ['x'] }, 'spec: ["x"]'],
    [3, '3'],
  ];
  for (const [v, want] of table) assert.equal(conflictValueText(v), want, JSON.stringify(v));
});

test('while a banner is shown the page knows its height, and forgets it when the banner goes', async () => {
  const style = document.documentElement.style;
  const height = () => style.getPropertyValue('--conflict-banner-height');
  assert.equal(height(), '');
  const close = await full();
  assert.match(height(), /^\d+px$/);
  close();
  assert.equal(height(), '');
  // Dismissed from its own button, and replaced by another banner, the same holds.
  await full();
  await full();
  assert.match(height(), /^\d+px$/);
  document.querySelector('.cb-dismiss').click();
  assert.equal(height(), '');
  showFieldConflict({
    entityKind: 'task', entityId: 'T-102', fieldKey: 'title', fieldLabel: 'Title',
    localValue: 'a', currentValue: 'b', currentEtag: 'x', onKeepMine: async () => {}, onUseServer: () => {},
  });
  assert.match(height(), /^\d+px$/);
  document.querySelector('.cb-use-server').click();
  assert.equal(height(), '');
});
