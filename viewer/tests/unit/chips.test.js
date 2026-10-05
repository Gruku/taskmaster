// User intent: filter chips are real toggle buttons whose words are text, and a pressed filter can always be turned
// off — even at zero count — while a row of them updates in place without losing the button the user is on.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const { filterChip, chipRow } = await import('../../js/components/chips.js');

const click = (el, init = {}) => el.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true, ...init }));

test('filterChip: a toggle button with swatch, label and count', () => {
  const b = filterChip({ label: 'Viewer', value: 'viewer', pressed: true, count: 4, swatch: 3 });
  assert.equal(b.tagName, 'BUTTON');
  assert.equal(b.type, 'button');
  assert.equal(b.className, 'chip');
  assert.equal(b.getAttribute('aria-pressed'), 'true');
  assert.equal(b.dataset.value, 'viewer');
  assert.deepEqual([...b.children].map((c) => c.className), ['chip__swatch chip__swatch--cat-3', 'chip__label', 'chip__count']);
  assert.equal(b.querySelector('.chip__swatch').getAttribute('aria-hidden'), 'true');
  assert.equal(b.querySelector('.chip__label').textContent, 'Viewer');
  assert.equal(b.querySelector('.chip__count').textContent, '4');
  assert.equal(b.disabled, false);
});

test('filterChip: released by default, no swatch or count unless given', () => {
  const b = filterChip({ label: 'Open', value: 'open' });
  assert.equal(b.getAttribute('aria-pressed'), 'false');
  assert.deepEqual([...b.children].map((c) => c.className), ['chip__label']);
  assert.equal(b.disabled, false);
});

test('filterChip: a label is text, never markup', () => {
  const b = filterChip({ label: '<img src=x onerror=alert(1)>', value: 'x' });
  assert.equal(b.querySelector('img'), null);
  assert.equal(b.querySelector('.chip__label').textContent, '<img src=x onerror=alert(1)>');
});

test('filterChip: title is the label unless one is given', () => {
  assert.equal(filterChip({ label: 'In progress', value: 'ip' }).title, 'In progress');
  assert.equal(filterChip({ label: 'Hi', value: 'hi', title: 'High priority' }).title, 'High priority');
});

test('filterChip: zero count disables a released chip only', () => {
  assert.equal(filterChip({ label: 'Blocked', value: 'b', count: 0 }).disabled, true);
  assert.equal(filterChip({ label: 'Blocked', value: 'b', count: 0, pressed: true }).disabled, false);
});

test('filterChip: a click reports the value and the event', () => {
  const calls = [];
  const b = filterChip({ label: 'Open', value: 'open', onToggle: (v, e) => calls.push([v, e.shiftKey]) });
  click(b, { shiftKey: true });
  assert.deepEqual(calls, [['open', true]]);
});

test('a pressed chip at zero count stays enabled; released, it disables', () => {
  const row = chipRow({ label: 'Status', chips: [{ value: 'open', label: 'Open', count: 3 }, { value: 'blocked', label: 'Blocked', count: 0, pressed: true }] });
  const blocked = row.el.querySelector('[data-value="blocked"]');
  assert.equal(blocked.disabled, false);
  assert.equal(blocked.getAttribute('aria-pressed'), 'true');
  row.update([{ value: 'open', label: 'Open', count: 3 }, { value: 'blocked', label: 'Blocked', count: 0, pressed: false }]);
  const after = row.el.querySelector('[data-value="blocked"]');
  assert.equal(after, blocked, 'the same button');
  assert.equal(after.disabled, true);
  assert.equal(after.getAttribute('aria-pressed'), 'false');
  row.destroy();
});

test('chipRow: onToggle receives the value and the event (shift-click)', () => {
  const calls = [];
  const row = chipRow({ label: 'Status', chips: [{ value: 'open', label: 'Open' }], onToggle: (v, e) => calls.push([v, e.shiftKey]) });
  click(row.el.querySelector('.chip'), { shiftKey: true });
  assert.deepEqual(calls, [['open', true]]);
  row.destroy();
});

test('chipRow: a group labelled by its label span, the chips under an overflow row', () => {
  const row = chipRow({ label: 'Epic', hint: 'Shift-click to add', chips: [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }] });
  assert.ok(row.el.matches('div.chip-row[role="group"]'));
  const label = row.el.querySelector(':scope > .chip-row__label');
  assert.equal(label.textContent, 'Epic');
  assert.equal(label.title, 'Shift-click to add');
  assert.ok(label.id);
  assert.equal(row.el.getAttribute('aria-labelledby'), label.id);
  const chips = row.el.querySelector(':scope > .chip-row__chips');
  assert.deepEqual([...chips.querySelectorAll('.chip')].map((c) => c.dataset.value), ['a', 'b']);
  assert.ok(chips.lastElementChild.matches('.overflow-more'));
  assert.equal(chips.lastElementChild.hidden, true);
  assert.equal(chips.lastElementChild.getAttribute('aria-label'), 'More Epic, 0 hidden');
  row.destroy();
});

test('chipRow: no title on the label without a hint; ids are unique', () => {
  const a = chipRow({ label: 'A', chips: [] });
  const b = chipRow({ label: 'B', chips: [] });
  assert.equal(a.el.querySelector('.chip-row__label').hasAttribute('title'), false);
  assert.notEqual(a.el.getAttribute('aria-labelledby'), b.el.getAttribute('aria-labelledby'));
});

test('chipRow update: buttons are reused, label and count change in place, adds, removes and reorders apply', () => {
  const row = chipRow({ label: 'Epic', chips: [{ value: 'a', label: 'A', count: 1 }, { value: 'b', label: 'B', count: 2 }, { value: 'c', label: 'C' }] });
  document.body.append(row.el);
  const [a, b] = row.el.querySelectorAll('.chip');
  b.focus();
  row.update([{ value: 'b', label: 'Bee', count: 5, pressed: true }, { value: 'd', label: 'D', swatch: 2 }, { value: 'a', label: 'A' }]);
  const chips = [...row.el.querySelectorAll('.chip')];
  assert.deepEqual(chips.map((c) => c.dataset.value), ['b', 'd', 'a']);
  assert.equal(chips[0], b);
  assert.equal(chips[2], a);
  assert.equal(document.activeElement, b, 'focus survives a reorder');
  assert.equal(b.querySelector('.chip__label').textContent, 'Bee');
  assert.equal(b.title, 'Bee');
  assert.equal(b.querySelector('.chip__count').textContent, '5');
  assert.equal(b.getAttribute('aria-pressed'), 'true');
  assert.equal(a.querySelector('.chip__count'), null, 'a count that went away is removed');
  assert.ok(chips[1].querySelector('.chip__swatch--cat-2'));
  assert.equal(row.el.querySelector('.overflow-more').previousElementSibling, a, 'More stays last');
  row.destroy();
  row.el.remove();
});
