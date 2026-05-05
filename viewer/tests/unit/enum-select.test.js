// viewer/tests/unit/enum-select.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const { EnumSelect } = await import('../../js/components/edit/fields/enum-select.js');

const STATUSES = [
  { value: 'todo', label: 'Todo' },
  { value: 'in-progress', label: 'In Progress' },
  { value: 'done', label: 'Done' },
];

test('read renders label for current value with editable class', () => {
  const el = EnumSelect.read({ value: 'in-progress', options: STATUSES, readOnly: false });
  assert.equal(el.textContent, 'In Progress');
  assert.ok(el.classList.contains('ef-enum'));
  assert.ok(el.classList.contains('ef-editable'));
});

test('read renders raw value when no matching option', () => {
  const el = EnumSelect.read({ value: 'unknown', options: STATUSES, readOnly: false });
  assert.equal(el.textContent, 'unknown');
});

test('edit renders <select> with options + current value selected', () => {
  const el = EnumSelect.edit({ value: 'done', options: STATUSES, onChange: () => {}, onCommit: () => {}, onCancel: () => {} });
  assert.equal(el.tagName, 'SELECT');
  assert.equal(el.options.length, 3);
  assert.equal(el.value, 'done');
});

test('edit change event commits the new value', () => {
  let committed = null;
  const el = EnumSelect.edit({ value: 'todo', options: STATUSES, onChange: () => {}, onCommit: (v) => { committed = v; }, onCancel: () => {} });
  el.value = 'in-progress';
  el.dispatchEvent(new dom.window.Event('change'));
  assert.equal(committed, 'in-progress');
});

test('validate enforces value-in-options', () => {
  assert.equal(EnumSelect.validate('done', { options: STATUSES }), null);
  assert.equal(EnumSelect.validate('bogus', { options: STATUSES }), 'invalid value');
  assert.equal(EnumSelect.validate(null, { options: STATUSES, required: true }), 'required');
});
