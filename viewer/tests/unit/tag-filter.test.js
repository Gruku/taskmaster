// User intent: the Ideas "Tags" filter is one searchable multi-select on the shared popover where "UX" and "ux" are
// one tag; its grouping, its button's announcement, its search and its repaint keeping the user's place are pinned here.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const { tagKey, collectTags, tagFilter } = await import('../../js/components/tag-filter.js');

const click = (el) => el.dispatchEvent(new dom.window.MouseEvent('click', { bubbles: true, cancelable: true }));
const key = (el, k) => {
  const ev = new dom.window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true });
  el.dispatchEvent(ev);
  return ev;
};
const type = (input, value) => {
  input.value = value;
  input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
};
const pop = () => document.querySelector('.tag-filter__popover');
const boxes = () => [...pop().querySelectorAll('.tag-filter__list input[type="checkbox"]')];
const box = (k) => pop().querySelector(`.tag-filter__list input[value="${k}"]`);
const option = (k) => box(k).closest('.tag-filter__option');

const ITEMS = [{ tags: ['UX', 'perf'] }, { tags: ['ux'] }, { tags: ['ux', ''] }];
const filters = [];
function mount(opts = {}) {
  const seen = [];
  let data = collectTags(ITEMS);
  const tf = tagFilter({ getTags: () => data, onChange: (keys) => seen.push(keys), ...opts });
  document.body.replaceChildren(tf.el);
  filters.push(tf);
  return Object.assign(tf, { seen, setData: (d) => { data = d; } });
}
test.afterEach(() => {
  // Close whatever a test left open, so the next one starts with no popover.
  for (const tf of filters.splice(0)) if (pop()) click(tf.el);
});

test('tagKey trims and lowercases; nothing is the empty key', () => {
  assert.equal(tagKey(' UX '), 'ux');
  assert.equal(tagKey('Perf'), 'perf');
  assert.equal(tagKey(null), '');
  assert.equal(tagKey(undefined), '');
});

test('collectTags groups by key, labels with the spelling used most, counts items, drops empties, sorts by count', () => {
  assert.deepEqual(collectTags(ITEMS), [{ key: 'ux', label: 'ux', count: 3 }, { key: 'perf', label: 'perf', count: 1 }]);
});

test('collectTags: a spelling tie keeps the first seen; equal counts sort by label; items without tags are skipped', () => {
  const got = collectTags([{ tags: ['Beta', 'alpha'] }, { tags: ['beta'] }, {}, { tags: null }, { tags: ['Gamma'] }]);
  assert.deepEqual(got, [
    { key: 'beta', label: 'Beta', count: 2 },
    { key: 'alpha', label: 'alpha', count: 1 },
    { key: 'gamma', label: 'Gamma', count: 1 },
  ]);
});

test('collectTags: an item carrying a tag in two spellings counts once', () => {
  assert.deepEqual(collectTags([{ tags: ['UX', 'ux'] }]), [{ key: 'ux', label: 'UX', count: 1 }]);
});

test('the button: a secondary small button labelled Tags; with two selected it says "· 2" and "Tags, 2 selected"', () => {
  const none = mount();
  assert.equal(none.el.tagName, 'BUTTON');
  assert.equal(none.el.type, 'button');
  assert.equal(none.el.className, 'btn btn--secondary btn--sm tag-filter');
  assert.equal(none.el.getAttribute('aria-label'), 'Tags');
  assert.equal(none.el.querySelector('.tag-filter__on'), null);

  const two = mount({ selected: ['ux', 'perf'] });
  assert.equal(two.el.getAttribute('aria-label'), 'Tags, 2 selected');
  assert.equal(two.el.querySelector('.tag-filter__on').textContent, '· 2');
  assert.deepEqual(two.selected(), ['ux', 'perf']);
});

test('a click opens a labelled dialog after the button with the search, one checkbox per tag, the status and Clear', () => {
  const tf = mount();
  click(tf.el);
  const p = pop();
  assert.ok(p);
  assert.equal(p.previousElementSibling, tf.el);
  assert.equal(p.getAttribute('role'), 'dialog');
  assert.equal(p.getAttribute('aria-label'), 'Filter by tag');
  assert.equal(tf.el.getAttribute('aria-expanded'), 'true');

  const search = p.querySelector('input.tag-filter__search');
  assert.equal(search.type, 'search');
  assert.equal(search.getAttribute('aria-label'), 'Find a tag');
  assert.equal(search.placeholder, 'Find a tag…');
  assert.equal(document.activeElement, search);

  const list = p.querySelector('div.tag-filter__list');
  assert.equal(list.getAttribute('role'), 'group');
  assert.equal(list.getAttribute('aria-label'), 'Tags');
  assert.deepEqual(boxes().map((b) => b.value), ['ux', 'perf']);
  for (const b of boxes()) {
    assert.ok(b.hasAttribute('data-popover-item'));
    const opt = b.closest('label.tag-filter__option');
    assert.ok(opt);
    assert.deepEqual([...opt.children].map((c) => c.className || c.tagName), ['INPUT', 'tag-filter__name', 'tag-filter__count']);
  }
  assert.equal(option('ux').querySelector('.tag-filter__name').textContent, 'ux');
  assert.equal(option('ux').querySelector('.tag-filter__count').textContent, '3');

  const none = p.querySelector('p.tag-filter__none');
  assert.equal(none.getAttribute('role'), 'status');
  assert.equal(none.textContent, '');
  const clear = p.querySelector('button.btn.btn--ghost.btn--sm.tag-filter__clear');
  assert.equal(clear.textContent, 'Clear tags');
  assert.equal(clear.hidden, true);
  assert.deepEqual([...p.children].map((c) => c.className.split(' ')[0]),
    ['tag-filter__search', 'tag-filter__list', 'tag-filter__none', 'btn']);

  click(tf.el);
  assert.equal(pop(), null);
  assert.equal(tf.el.getAttribute('aria-expanded'), 'false');
});

test('typing filters case-insensitively: a choice that does not match is hidden and disabled; none → the status says so', () => {
  const tf = mount();
  click(tf.el);
  const search = pop().querySelector('.tag-filter__search');
  type(search, 'PE');
  assert.equal(option('ux').hidden, true);
  assert.equal(box('ux').disabled, true);
  assert.equal(option('perf').hidden, false);
  assert.equal(box('perf').disabled, false);
  assert.equal(pop().querySelector('.tag-filter__none').textContent, '');

  type(search, 'zzz');
  assert.ok(boxes().every((b) => b.disabled));
  assert.equal(pop().querySelector('.tag-filter__none').textContent, 'No tag matches “zzz”.');

  type(search, '');
  assert.ok(boxes().every((b) => !b.disabled && !b.closest('.tag-filter__option').hidden));
  assert.equal(pop().querySelector('.tag-filter__none').textContent, '');
  void tf;
});

test('ArrowDown in the search box moves to the first enabled checkbox and is used up', () => {
  const tf = mount();
  click(tf.el);
  const search = pop().querySelector('.tag-filter__search');
  key(search, 'ArrowDown');
  assert.equal(document.activeElement, box('ux'), 'the first choice in the list, not the last built');
  search.focus();
  type(search, 'pe');
  const ev = key(search, 'ArrowDown');
  assert.equal(ev.defaultPrevented, true);
  assert.equal(document.activeElement, box('perf'));
  void tf;
});

test('checking a tag calls onChange with the keys in the order chosen and repaints the button; focus stays', () => {
  const tf = mount();
  click(tf.el);
  box('perf').focus();
  click(box('perf'));
  assert.deepEqual(tf.seen, [['perf']]);
  assert.equal(document.activeElement, box('perf'));
  assert.equal(tf.el.querySelector('.tag-filter__on').textContent, '· 1');
  assert.equal(tf.el.getAttribute('aria-label'), 'Tags, 1 selected');
  assert.equal(pop().querySelector('.tag-filter__clear').hidden, false);

  click(box('ux'));
  assert.deepEqual(tf.seen.at(-1), ['perf', 'ux']);
  click(box('perf'));
  assert.deepEqual(tf.seen.at(-1), ['ux']);
  assert.deepEqual(tf.selected(), ['ux']);
});

test('Clear tags empties the selection, unchecks every box, calls onChange([]) and keeps focus in the popover', () => {
  const tf = mount({ selected: ['ux', 'perf'] });
  click(tf.el);
  assert.ok(boxes().every((b) => b.checked));
  const clear = pop().querySelector('.tag-filter__clear');
  clear.focus();
  click(clear);
  assert.deepEqual(tf.seen, [[]]);
  assert.ok(boxes().every((b) => !b.checked));
  assert.equal(clear.hidden, true);
  assert.equal(tf.el.getAttribute('aria-label'), 'Tags');
  assert.equal(document.activeElement, pop().querySelector('.tag-filter__search'));
});

test('clear() empties the selection while closed too', () => {
  const tf = mount({ selected: ['ux'] });
  tf.clear();
  assert.deepEqual(tf.selected(), []);
  assert.deepEqual(tf.seen, [[]]);
  assert.equal(tf.el.querySelector('.tag-filter__on'), null);
});

test('Escape in the search box or on a checkbox closes and returns focus to the button', () => {
  const tf = mount();
  click(tf.el);
  key(pop().querySelector('.tag-filter__search'), 'Escape');
  assert.equal(pop(), null);
  assert.equal(document.activeElement, tf.el);

  click(tf.el);
  box('ux').focus();
  key(box('ux'), 'Escape');
  assert.equal(pop(), null);
  assert.equal(document.activeElement, tf.el);
});

test('update() while open keeps the search text and its filter, the checked keys, and focus on the same key', () => {
  const tf = mount();
  click(tf.el);
  const search = pop().querySelector('.tag-filter__search');
  type(search, 'pe');
  box('perf').focus();
  click(box('perf'));
  tf.setData([{ key: 'perf', label: 'Perf', count: 4 }, { key: 'ux', label: 'UX', count: 2 }, { key: 'pen', label: 'pen', count: 1 }]);
  tf.update();
  assert.equal(pop().querySelector('.tag-filter__search'), search);
  assert.equal(search.value, 'pe');
  assert.deepEqual(boxes().map((b) => b.value), ['perf', 'ux', 'pen']);
  assert.equal(box('perf').checked, true);
  assert.equal(box('pen').checked, false);
  assert.equal(option('perf').querySelector('.tag-filter__name').textContent, 'Perf');
  assert.equal(option('perf').querySelector('.tag-filter__count').textContent, '4');
  assert.equal(option('ux').hidden, true);
  assert.equal(box('ux').disabled, true);
  assert.equal(document.activeElement, box('perf'));
});

test('a selected key missing from new data stays listed with count 0, so it can be released', () => {
  const tf = mount({ selected: ['perf'] });
  click(tf.el);
  tf.setData([{ key: 'ux', label: 'ux', count: 3 }]);
  tf.update();
  assert.deepEqual(boxes().map((b) => b.value), ['ux', 'perf']);
  assert.equal(box('perf').checked, true);
  assert.equal(option('perf').querySelector('.tag-filter__name').textContent, 'perf');
  assert.equal(option('perf').querySelector('.tag-filter__count').textContent, '0');
  click(box('perf'));
  assert.deepEqual(tf.seen.at(-1), []);
  assert.equal(tf.el.getAttribute('aria-label'), 'Tags');
});

test('update() while closed repaints the button only', () => {
  const tf = mount({ selected: ['ux'] });
  tf.update();
  assert.equal(pop(), null);
  assert.equal(tf.el.querySelector('.tag-filter__on').textContent, '· 1');
});

test('a tag that looks like markup is text', () => {
  const evil = '<img src=x onerror=alert(1)>';
  const tf = mount({ getTags: () => collectTags([{ tags: [evil] }]) });
  click(tf.el);
  assert.equal(pop().querySelector('img'), null);
  assert.equal(pop().querySelector('.tag-filter__name').textContent, evil);
  assert.equal(box(tagKey(evil).replace(/"/g, '\\"')).value, tagKey(evil));
});
