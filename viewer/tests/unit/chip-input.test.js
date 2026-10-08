// viewer/tests/unit/chip-input.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;
globalThis.HTMLElement = dom.window.HTMLElement;

const { ChipInput } = await import('../../js/components/edit/fields/chip-input.js');

test('read renders one chip per value with comma separator class', () => {
  const el = ChipInput.read({ value: ['a', 'b', 'c'], readOnly: false });
  const chips = el.querySelectorAll('.ef-chip');
  assert.equal(chips.length, 3);
  assert.equal(chips[0].textContent, 'a');
});

test('read with empty value renders placeholder', () => {
  const el = ChipInput.read({ value: [], readOnly: false, placeholder: 'no tags' });
  assert.match(el.textContent, /no tags/);
  assert.ok(el.classList.contains('ef-placeholder'));
});

test('edit renders chip-list + autocomplete input', () => {
  const el = ChipInput.edit({
    value: ['x', 'y'],
    source: async () => [],
    onChange: () => {}, onCommit: () => {}, onCancel: () => {},
  });
  assert.equal(el.querySelectorAll('.ef-chip').length, 2);
  assert.ok(el.querySelector('input.ef-chip-input-text'));
});

test('clicking ✕ on a chip removes it from draft', async () => {
  let lastDraft = null;
  const el = ChipInput.edit({
    value: ['a', 'b'],
    source: async () => [],
    onChange: (v) => { lastDraft = v; },
    onCommit: () => {}, onCancel: () => {},
  });
  document.body.appendChild(el);
  const removeBtn = el.querySelectorAll('.ef-chip-x')[0];
  removeBtn.click();
  assert.deepEqual(lastDraft, ['b']);
  assert.equal(el.querySelectorAll('.ef-chip').length, 1);
});

test('removing a chip keeps focus in the field: on the chip that took its place, else the one before, else the text input', () => {
  const el = ChipInput.edit({ value: ['a', 'b', 'c'], source: async () => [], onChange: () => {}, onCommit: () => {}, autoFocus: false });
  document.body.replaceChildren(el);
  const remove = (label) => { const x = el.querySelector(`[aria-label="Remove ${label}"]`); x.focus(); x.click(); };
  const focused = () => document.activeElement.getAttribute('aria-label') ?? document.activeElement.className;
  remove('a');
  assert.equal(focused(), 'Remove b', 'the next chip');
  remove('c');
  assert.equal(focused(), 'Remove b', 'the last chip went: the one before it');
  remove('b');
  assert.equal(document.activeElement, el.control, 'no chip left: the text input');
});

test('text typed but not yet a chip is reported as pending; a list-only input also says it cannot keep it (M1)', () => {
  const free = ChipInput.edit({ value: [], source: async () => [], allowFree: true, autoFocus: false });
  const listed = ChipInput.edit({ value: [], source: async () => [], autoFocus: false });
  document.body.replaceChildren(free, listed);
  assert.equal(free.pending, '');
  free.control.value = '  notes.md ';
  assert.equal(free.pending, 'notes.md');
  assert.equal(free.pendingError, null, 'free text becomes a chip when the field is left');
  listed.control.value = 'T-9';
  assert.equal(listed.pending, 'T-9');
  assert.match(listed.pendingError, /list/);
});

test('typing + Enter with allowFree commits a free-text chip', async () => {
  let drafts = [];
  const el = ChipInput.edit({
    value: ['a'],
    source: async () => [],
    allowFree: true,
    onChange: (v) => { drafts.push([...v]); },
    onCommit: () => {}, onCancel: () => {},
  });
  document.body.appendChild(el);
  const input = el.querySelector('input.ef-chip-input-text');
  input.value = 'b';
  input.dispatchEvent(new dom.window.Event('input'));
  input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter' }));
  assert.deepEqual(drafts.at(-1), ['a', 'b']);
});

test('Enter without allowFree and no autocomplete match does nothing', () => {
  let changes = 0;
  const el = ChipInput.edit({
    value: ['a'],
    source: async () => [],
    allowFree: false,
    onChange: () => { changes++; },
    onCommit: () => {}, onCancel: () => {},
  });
  document.body.appendChild(el);
  const input = el.querySelector('input.ef-chip-input-text');
  input.value = 'b';
  input.dispatchEvent(new dom.window.Event('input'));
  input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter' }));
  // Should not have committed a chip — no autocomplete match and free-text disabled.
  assert.equal(el.querySelectorAll('.ef-chip').length, 1);
});

test('coerce dedupes and trims', () => {
  assert.deepEqual(ChipInput.coerce(['a', 'a', 'b ']), ['a', 'b']);
  assert.deepEqual(ChipInput.coerce(null), []);
});

test('validate enforces required + min count', () => {
  assert.equal(ChipInput.validate([], { required: true }), 'required');
  assert.equal(ChipInput.validate(['a'], { required: true }), null);
  assert.equal(ChipInput.validate([], { minCount: 2 }), 'need at least 2');
});

// ── The suggestion list is a combobox's listbox, opened on the shared popover ──
const { openPopoverCount } = await import('../../js/components/popover.js');
const tick = (ms = 0) => new Promise((ok) => setTimeout(ok, ms));
const THREE = async () => [
  { value: 'a1', label: 'alpha one' }, { value: 'a2', label: 'alpha two', hint: 'todo' }, { value: 'a3', label: 'alpha three' },
];
function combo(args = {}) {
  const el = ChipInput.edit({ value: [], source: THREE, label: 'Depends on', onChange() {}, onCommit() {}, autoFocus: false, ...args });
  const outside = document.createElement('button');
  document.body.replaceChildren(el, outside);
  el.control.focus();
  return { el, input: el.control, outside };
}
async function type(input, text) {
  input.value = text;
  input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  await tick();
}
// True when the key was left alone, as dispatchEvent reports it.
const press = (el, key) => el.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
const listbox = () => document.querySelector('[role="listbox"]');
const options = () => [...listbox().querySelectorAll('[role="option"]')];
const chipLabels = (el) => [...el.querySelectorAll('.ef-chip-label')].map((c) => c.textContent);
async function cleared() {
  document.body.replaceChildren();
  await tick();
  assert.equal(openPopoverCount(), 0, 'no suggestion list outlives its field');
}

test('1. the text input is a combobox: expanded only while suggestions show, naming the list and the highlighted option', async () => {
  const { input } = combo();
  assert.equal(input.getAttribute('role'), 'combobox');
  assert.equal(input.getAttribute('aria-autocomplete'), 'list');
  assert.equal(input.getAttribute('aria-expanded'), 'false');
  assert.equal(input.hasAttribute('aria-controls'), false);
  assert.equal(input.hasAttribute('aria-activedescendant'), false);
  await type(input, 'al');
  assert.equal(input.getAttribute('aria-expanded'), 'true');
  assert.equal(input.getAttribute('aria-controls'), listbox().id);
  assert.equal(input.getAttribute('aria-activedescendant'), options()[0].id);
  press(input, 'ArrowDown');
  assert.equal(input.getAttribute('aria-activedescendant'), options()[1].id);
  press(input, 'Escape');
  assert.equal(input.getAttribute('aria-expanded'), 'false');
  assert.equal(input.hasAttribute('aria-controls'), false);
  assert.equal(input.hasAttribute('aria-activedescendant'), false);
  await cleared();
});

test('2. suggestions open on the shared popover as a labelled listbox of options, focus staying in the input', async () => {
  const { input } = combo();
  await type(input, 'al');
  const list = listbox();
  assert.equal(openPopoverCount(), 1);
  assert.ok(list.classList.contains('popover') && list.classList.contains('ef-chip-dropdown'));
  assert.equal(list.getAttribute('aria-label'), 'Depends on suggestions');
  assert.equal(input.nextElementSibling, list, 'placed right after its input');
  assert.equal(list.style.position, 'fixed');
  assert.notEqual(list.style.minWidth, '', 'at least as wide as its input');
  assert.equal(document.activeElement, input);
  const opts = options();
  assert.equal(opts.length, 3);
  assert.equal(new Set(opts.map((o) => o.id)).size, 3, 'each option has an id of its own');
  for (const o of opts) assert.equal(o.tagName, 'DIV');
  const selected = () => opts.map((o) => o.getAttribute('aria-selected') === 'true');
  assert.deepEqual(selected(), [true, false, false]);
  press(input, 'ArrowDown');
  assert.deepEqual(selected(), [false, true, false]);
  // More typing redraws the same list; it does not open a second one.
  await type(input, 'alp');
  assert.equal(openPopoverCount(), 1);
  assert.equal(input.getAttribute('aria-controls'), listbox().id);
  await cleared();
});

test('a suggestion with a status shows the shared marker (shape plus word); a plain hint stays text', async () => {
  const src = async () => [
    { value: 'T-1', label: 'T-1 · One', marker: { label: 'Blocked', shape: '◆', tone: 'critical' } },
    { value: 'T-2', label: 'T-2 · Two', hint: 'todo' },
  ];
  const { input } = combo({ source: src });
  await type(input, 'T');
  const [withMarker, withHint] = options();
  const m = withMarker.querySelector('.ef-chip-dd-hint .marker.marker--critical');
  assert.ok(m, 'the status is a marker');
  assert.equal(m.querySelector('.marker__shape').dataset.shape, 'diamond');
  assert.equal(m.querySelector('.marker__word').textContent, 'Blocked');
  assert.equal(withHint.querySelector('.ef-chip-dd-hint').textContent, 'todo');
  assert.equal(withHint.querySelector('.marker'), null);
  await cleared();
});

test('3. keys are unchanged: arrows move the highlight, Enter and Tab pick, Escape clears and closes, Backspace removes', async () => {
  const { el, input } = combo({ value: ['z9'] });
  await type(input, 'al');
  press(input, 'ArrowUp');
  assert.equal(input.getAttribute('aria-activedescendant'), options()[0].id, 'stops at the first');
  press(input, 'ArrowDown'); press(input, 'ArrowDown'); press(input, 'ArrowDown');
  assert.equal(input.getAttribute('aria-activedescendant'), options()[2].id, 'stops at the last');
  assert.equal(press(input, 'Enter'), false);
  assert.deepEqual(chipLabels(el), ['z9', 'a3']);
  await type(input, 'al');
  press(input, 'ArrowDown');
  assert.equal(press(input, 'Tab'), false, 'Tab picks rather than leaving');
  assert.deepEqual(chipLabels(el), ['z9', 'a3', 'a2']);
  await type(input, 'al');
  assert.equal(press(input, 'Escape'), false, 'used before the form sees it');
  assert.equal(input.value, '');
  assert.equal(listbox(), null);
  assert.equal(press(input, 'Backspace'), false);
  assert.deepEqual(chipLabels(el), ['z9', 'a3']);
  await cleared();
});

test('3. a mousedown on an option picks it and keeps focus in the input', async () => {
  const changes = [];
  const { el, input } = combo({ onChange: (v) => changes.push(v) });
  await type(input, 'al');
  const md = new dom.window.MouseEvent('mousedown', { bubbles: true, cancelable: true });
  assert.equal(options()[1].dispatchEvent(md), false, 'the press does not take focus');
  assert.deepEqual(changes.at(-1), ['a2']);
  assert.deepEqual(chipLabels(el), ['a2']);
  assert.equal(document.activeElement, input);
  assert.equal(openPopoverCount(), 0);
  await cleared();
});

test('4. the list closes on pick, blur, Escape, an outside press and when the field is removed', async () => {
  const closed = (why) => {
    assert.equal(openPopoverCount(), 0, why);
    assert.equal(listbox(), null, why);
  };
  let { el, input, outside } = combo();
  await type(input, 'al'); press(input, 'Enter'); closed('pick');
  await type(input, 'al'); input.blur(); closed('blur');
  input.focus();
  await type(input, 'al'); press(input, 'Escape'); closed('Escape');
  await type(input, 'al');
  outside.dispatchEvent(new dom.window.Event('pointerdown', { bubbles: true, cancelable: true }));
  closed('outside press');
  assert.equal(input.getAttribute('aria-expanded'), 'false');
  assert.equal(input.hasAttribute('aria-activedescendant'), false);
  const chips = el.querySelectorAll('.ef-chip').length;
  press(input, 'Enter');
  assert.equal(el.querySelectorAll('.ef-chip').length, chips, 'a closed list offers nothing to Enter');
  ({ el, input } = combo());
  await type(input, 'al');
  assert.equal(openPopoverCount(), 1);
  el.remove();
  await tick();
  closed('field removed');
  await cleared();
});

test('a source that answers after focus has left opens no list', async () => {
  for (const leave of ['blur', 'outside press']) {
    let answer;
    const { el, input, outside } = combo({ source: () => new Promise((ok) => { answer = ok; }) });
    input.value = 'al';
    input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    if (leave === 'blur') input.blur();
    else { outside.dispatchEvent(new dom.window.Event('pointerdown', { bubbles: true, cancelable: true })); outside.focus(); }
    answer([{ value: 'a1', label: 'alpha one' }]);
    await tick();
    assert.equal(openPopoverCount(), 0, leave);
    assert.equal(listbox(), null, leave);
    assert.equal(input.getAttribute('aria-expanded'), 'false', leave);
    assert.equal(el.querySelectorAll('.ef-chip').length, 0, leave);
  }
  await cleared();
});

test('the highlighted option is scrolled into view within the list as the arrows move it', async () => {
  const seen = [];
  const proto = dom.window.HTMLElement.prototype;
  const had = Object.getOwnPropertyDescriptor(proto, 'scrollIntoView');
  proto.scrollIntoView = function scrollIntoView(opts) { seen.push([this, opts]); };
  try {
    const { input } = combo();
    await type(input, 'al');
    press(input, 'ArrowDown');
    press(input, 'ArrowDown');
    const opts = options();
    assert.deepEqual(seen.at(-2), [opts[1], { block: 'nearest' }]);
    assert.deepEqual(seen.at(-1), [opts[2], { block: 'nearest' }]);
  } finally {
    if (had) Object.defineProperty(proto, 'scrollIntoView', had); else delete proto.scrollIntoView;
  }
  await cleared();
});
