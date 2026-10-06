// User intent: the list screens' filters sit in one named group with a "Clear filters" that appears only when there is
// something to clear, and every toolbar control carries a real, programmatic label.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const { filterRail, labelled } = await import('../../js/components/list-toolbar.js');

const click = (el) => el.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }));

test('filterRail: a named group whose clear button sits last, hidden', () => {
  const rail = filterRail({ onClear() {} });
  assert.equal(rail.el.tagName, 'DIV');
  assert.equal(rail.el.className, 'list-filters');
  assert.equal(rail.el.getAttribute('role'), 'group');
  assert.equal(rail.el.getAttribute('aria-label'), 'Filters');
  const clear = rail.el.lastElementChild;
  assert.equal(clear.tagName, 'BUTTON');
  assert.equal(clear.type, 'button');
  assert.equal(clear.className, 'btn btn--ghost btn--sm list-filters__clear');
  assert.equal(clear.hidden, true);
  assert.equal(clear.textContent.trim(), 'Clear filters');
  assert.equal(clear.querySelector('svg.icon').getAttribute('width'), '14');
});

test('filterRail: a custom label names the group', () => {
  assert.equal(filterRail({ label: 'Bug filters' }).el.getAttribute('aria-label'), 'Bug filters');
});

test('filterRail: add() puts nodes before the clear button, in order', () => {
  const rail = filterRail({});
  const a = document.createElement('span');
  const b = document.createElement('span');
  rail.add(a, b);
  const c = document.createElement('span');
  rail.add(c);
  assert.deepEqual([...rail.el.children].slice(0, 3), [a, b, c]);
  assert.ok(rail.el.lastElementChild.classList.contains('list-filters__clear'));
});

test('filterRail: setClearable shows and hides; a click calls onClear once', () => {
  let n = 0;
  const rail = filterRail({ onClear: () => n++ });
  const clear = rail.el.querySelector('.list-filters__clear');
  rail.setClearable(true);
  assert.equal(clear.hidden, false);
  click(clear);
  assert.equal(n, 1);
  rail.setClearable(false);
  assert.equal(clear.hidden, true);
});

test('labelled: a wrapped select gets a <label for> pointing at its generated id', () => {
  const wrap = document.createElement('span');
  wrap.className = 'ef-select';
  const select = document.createElement('select');
  wrap.append(select);
  const el = labelled({ label: 'Sort', control: wrap });
  assert.equal(el.className, 'list-labelled');
  assert.equal(el.hasAttribute('role'), false);
  const lab = el.firstElementChild;
  assert.equal(lab.tagName, 'LABEL');
  assert.equal(lab.className, 'list-labelled__label');
  assert.equal(lab.textContent, 'Sort');
  assert.match(select.id, /^list-ctl-\d+$/);
  assert.equal(lab.htmlFor, select.id);
  assert.equal(el.lastElementChild, wrap);
});

test('labelled: an input keeps its own id; two controls get distinct ids', () => {
  const input = document.createElement('input');
  input.id = 'mine';
  assert.equal(labelled({ label: 'Find', control: input }).querySelector('label').htmlFor, 'mine');
  const s1 = document.createElement('select');
  const s2 = document.createElement('select');
  labelled({ label: 'A', control: s1 });
  labelled({ label: 'B', control: s2 });
  assert.notEqual(s1.id, s2.id);
});

test('labelled: a segmented control becomes a group labelled by a span', () => {
  const seg = document.createElement('div');
  seg.className = 'tm-segmented';
  const el = labelled({ label: 'View', control: seg });
  document.body.append(el);
  assert.equal(el.className, 'list-labelled');
  assert.equal(el.getAttribute('role'), 'group');
  const lab = el.firstElementChild;
  assert.equal(lab.tagName, 'SPAN');
  assert.equal(lab.className, 'list-labelled__label');
  assert.equal(document.getElementById(el.getAttribute('aria-labelledby')).textContent, 'View');
  assert.equal(el.lastElementChild, seg);
  el.remove();
});
