// User intent: the phone column switcher is a real tablist whose tab ids and buttons stay stable across repaints, so
// the screens that drive it (Issues, Kanban) can label their panels by id and keep the user's focus on a tab.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const { columnTabs } = await import('../../js/components/column-tabs.js');

const COLS = [
  { key: 'a', label: 'Investigating', count: 6, panelId: 'col-a' },
  { key: 'b', label: 'Open', count: 9, panelId: 'col-b' },
  { key: 'c', label: 'Fixed', count: 0, panelId: 'col-c' },
];
const click = (el) => el.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }));
const key = (el, k) => {
  const ev = new dom.window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true });
  el.dispatchEvent(ev);
  return ev;
};
const tabs = (el) => [...el.querySelectorAll('.column-tabs__tab')];
function mount(opts = {}) {
  const seen = [];
  const ct = columnTabs({ label: 'Issue columns', columns: COLS, selected: 'a', onSelect: (k) => seen.push(k), ...opts });
  document.body.replaceChildren(ct.el);
  return { ...ct, seen };
}

test('three columns: a labelled tablist of three tabs with ids, controls, keys and counts', () => {
  const { el } = mount();
  assert.equal(el.tagName, 'DIV');
  assert.equal(el.className, 'column-tabs');
  assert.equal(el.getAttribute('role'), 'tablist');
  assert.equal(el.getAttribute('aria-label'), 'Issue columns');
  assert.equal(el.hidden, false);
  const t = tabs(el);
  assert.equal(t.length, 3);
  t.forEach((b, i) => {
    const c = COLS[i];
    assert.equal(b.tagName, 'BUTTON');
    assert.equal(b.type, 'button');
    assert.equal(b.getAttribute('role'), 'tab');
    assert.equal(b.id, `${c.panelId}-tab`);
    assert.equal(b.getAttribute('aria-controls'), c.panelId);
    assert.equal(b.dataset.key, c.key);
    assert.deepEqual([...b.children].map((s) => s.className), ['column-tabs__label', 'column-tabs__count']);
    assert.equal(b.querySelector('.column-tabs__label').textContent, c.label);
    assert.equal(b.querySelector('.column-tabs__count').textContent, String(c.count));
  });
});

test('the selected tab is aria-selected with tabindex 0; the rest are -1', () => {
  const { el } = mount({ selected: 'b' });
  assert.deepEqual(tabs(el).map((b) => b.getAttribute('aria-selected')), ['false', 'true', 'false']);
  assert.deepEqual(tabs(el).map((b) => b.getAttribute('tabindex')), ['-1', '0', '-1']);
});

test('fewer than two columns: the list is hidden, its tabs still built', () => {
  const { el } = mount({ columns: [COLS[0]] });
  assert.equal(el.hidden, true);
  assert.equal(tabs(el).length, 1);
  assert.equal(tabs(el)[0].id, 'col-a-tab');
  const none = mount({ columns: [] });
  assert.equal(none.el.hidden, true);
});

test('a click selects that tab and calls onSelect with its key', () => {
  const { el, seen } = mount();
  click(tabs(el)[1]);
  assert.deepEqual(seen, ['b']);
  assert.deepEqual(tabs(el).map((b) => b.getAttribute('tabindex')), ['-1', '0', '-1']);
});

test('ArrowRight from the last tab wraps to the first, focuses it and selects it', () => {
  const { el, seen } = mount({ selected: 'c' });
  tabs(el)[2].focus();
  const ev = key(tabs(el)[2], 'ArrowRight');
  assert.equal(ev.defaultPrevented, true);
  assert.equal(document.activeElement, tabs(el)[0]);
  assert.deepEqual(seen, ['a']);
  assert.equal(tabs(el)[0].getAttribute('aria-selected'), 'true');
});

test('ArrowLeft from the first tab wraps to the last', () => {
  const { el, seen } = mount();
  tabs(el)[0].focus();
  const ev = key(tabs(el)[0], 'ArrowLeft');
  assert.equal(ev.defaultPrevented, true);
  assert.equal(document.activeElement, tabs(el)[2]);
  assert.deepEqual(seen, ['c']);
});

test('Home and End go to the first and last tab', () => {
  const { el, seen } = mount({ selected: 'b' });
  tabs(el)[1].focus();
  assert.equal(key(tabs(el)[1], 'End').defaultPrevented, true);
  assert.equal(document.activeElement, tabs(el)[2]);
  assert.equal(key(tabs(el)[2], 'Home').defaultPrevented, true);
  assert.equal(document.activeElement, tabs(el)[0]);
  assert.deepEqual(seen, ['c', 'a']);
});

test('other keys pass through untouched', () => {
  const { el, seen } = mount();
  assert.equal(key(tabs(el)[0], 'ArrowDown').defaultPrevented, false);
  assert.equal(key(tabs(el)[0], 'Tab').defaultPrevented, false);
  assert.deepEqual(seen, []);
});

test('update(): a changed count repaints the same button, which keeps its focus and id', () => {
  const { el, update } = mount({ selected: 'b' });
  const before = tabs(el)[1];
  before.focus();
  update({ columns: COLS.map((c) => (c.key === 'b' ? { ...c, count: 10, label: 'Open now' } : c)), selected: 'b' });
  const after = tabs(el)[1];
  assert.equal(after, before);
  assert.equal(document.activeElement, before);
  assert.equal(after.id, 'col-b-tab');
  assert.equal(after.querySelector('.column-tabs__count').textContent, '10');
  assert.equal(after.querySelector('.column-tabs__label').textContent, 'Open now');
});

test('update(): adds and removes tabs, keeps the order of columns, and moves the selection', () => {
  const { el, update } = mount();
  const a = tabs(el)[0];
  const b = tabs(el)[1];
  b.focus();
  update({ columns: [COLS[1], COLS[0], { key: 'd', label: 'Wontfix', count: 2, panelId: 'col-d' }], selected: 'd' });
  assert.deepEqual(tabs(el).map((t) => t.dataset.key), ['b', 'a', 'd']);
  assert.equal(tabs(el)[0], b);
  assert.equal(tabs(el)[1], a);
  assert.equal(document.activeElement, b);
  assert.equal(tabs(el)[2].id, 'col-d-tab');
  assert.deepEqual(tabs(el).map((t) => t.getAttribute('aria-selected')), ['false', 'false', 'true']);
  assert.deepEqual(tabs(el).map((t) => t.getAttribute('tabindex')), ['-1', '-1', '0']);
  assert.equal(el.querySelector('[data-key="c"]'), null);
});

test('update(): dropping to one column hides the list; back to two shows it', () => {
  const { el, update } = mount();
  update({ columns: [COLS[0]], selected: 'a' });
  assert.equal(el.hidden, true);
  update({ columns: COLS.slice(0, 2), selected: 'a' });
  assert.equal(el.hidden, false);
});

test('a tab id is its panelId plus -tab, also after its panel id changes', () => {
  const { el, update } = mount();
  const b = tabs(el)[1];
  update({ columns: COLS.map((c) => (c.key === 'b' ? { ...c, panelId: 'other-b' } : c)), selected: 'a' });
  assert.equal(tabs(el)[1], b);
  assert.equal(b.id, 'other-b-tab');
  assert.equal(b.getAttribute('aria-controls'), 'other-b');
});

test('update() scrolls the selected tab into view, nearest on both axes', () => {
  const proto = dom.window.Element.prototype;
  const had = Object.getOwnPropertyDescriptor(proto, 'scrollIntoView');
  const seen = [];
  proto.scrollIntoView = function scrollIntoView(opts) { seen.push([this, opts]); };
  try {
    const { el, update } = mount();
    seen.length = 0;
    update({ columns: COLS, selected: 'c' });
    assert.deepEqual(seen, [[tabs(el)[2], { block: 'nearest', inline: 'nearest' }]]);
  } finally {
    if (had) Object.defineProperty(proto, 'scrollIntoView', had); else delete proto.scrollIntoView;
  }
});

test('a label with markup is text', () => {
  const { el } = mount({ columns: [{ key: 'x', label: '<img src=x onerror=alert(1)>', count: 1, panelId: 'p-x' }, COLS[1]] });
  const lab = tabs(el)[0].querySelector('.column-tabs__label');
  assert.equal(lab.textContent, '<img src=x onerror=alert(1)>');
  assert.equal(el.querySelector('img'), null);
});
