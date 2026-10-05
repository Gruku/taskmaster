// User intent: a row that never wraps keeps as many leading items as fit and parks the rest behind "More" — the
// arithmetic of what fits, and the promise that nothing moves where layout cannot be measured, are pinned here.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const { fitCount, overflowRow } = await import('../../js/components/overflow-row.js');

test('fitCount: everything stays when the items and the gaps between them fit', () => {
  assert.equal(fitCount([50, 50, 50], 160, { gap: 5 }), 3);
  // No room is reserved for More when nothing needs it: 160 holds all three, though only two would fit beside a 40px More.
  assert.equal(fitCount([50, 50, 50], 160, { gap: 5, moreWidth: 40 }), 3);
});

test('fitCount: otherwise the leading items that fit beside More, each followed by a gap', () => {
  assert.equal(fitCount([50, 50, 50], 149, { gap: 5, moreWidth: 40 }), 1);
  // An exact fit counts: 50 + 5 + 50 + 5 + 40 = 150.
  assert.equal(fitCount([50, 50, 50], 150, { gap: 5, moreWidth: 40 }), 2);
});

test('fitCount: none stays when not even the first fits beside More; an empty row keeps nothing', () => {
  assert.equal(fitCount([50], 10, { moreWidth: 40 }), 0);
  assert.equal(fitCount([], 100), 0);
});

test('6. without ResizeObserver nothing moves and More stays hidden', () => {
  assert.equal(window.ResizeObserver, undefined, 'jsdom has no ResizeObserver');
  const row = document.createElement('div');
  const kids = Array.from({ length: 5 }, (_, i) => Object.assign(document.createElement('button'), { textContent: `item ${i}` }));
  row.append(...kids);
  document.body.append(row);
  const layouts = [];
  const ov = overflowRow(row, { onLayout: (r) => layouts.push(r) });
  assert.equal(row.lastElementChild, ov.more, 'More is appended to the row');
  assert.ok(ov.more.matches('button.btn.btn--ghost.btn--sm.overflow-more'));
  assert.equal(ov.more.type, 'button');
  assert.ok(ov.more.querySelector('.overflow-more__count'));
  assert.match(ov.more.textContent, /More/);
  assert.equal(ov.more.hidden, true);
  ov.relayout();
  row.append(Object.assign(document.createElement('button'), { textContent: 'late' }));
  assert.deepEqual([...row.children].slice(0, 5), kids);
  assert.equal(ov.more.hidden, true);
  assert.equal(row.querySelectorAll('[data-popover-item]').length, 0);
  assert.deepEqual(layouts, []);
  ov.destroy();
  row.remove();
});

test('More carries the icon it is given', () => {
  const row = document.createElement('div');
  const ov = overflowRow(row, { moreLabel: 'Filters', moreIcon: 'sliders' });
  assert.ok(ov.more.querySelector('svg'));
  assert.match(ov.more.textContent, /Filters/);
  ov.destroy();
});
