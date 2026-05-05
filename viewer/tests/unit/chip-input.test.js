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
