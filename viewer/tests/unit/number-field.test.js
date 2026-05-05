// viewer/tests/unit/number-field.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const { NumberField } = await import('../../js/components/edit/fields/number-field.js');

test('read renders integer or em-dash for null', () => {
  assert.equal(NumberField.read({ value: 5, readOnly: false }).textContent, '5');
  assert.equal(NumberField.read({ value: null, readOnly: false }).textContent, '—');
});

test('edit renders type=number input', () => {
  const el = NumberField.edit({ value: 3, onChange: () => {}, onCommit: () => {}, onCancel: () => {} });
  assert.equal(el.type, 'number');
  assert.equal(el.value, '3');
});

test('coerce returns integer or null', () => {
  assert.equal(NumberField.coerce('7'), 7);
  assert.equal(NumberField.coerce(''), null);
  assert.equal(NumberField.coerce('abc'), null);
});

test('validate min/max', () => {
  assert.equal(NumberField.validate(5, { min: 1, max: 10 }), null);
  assert.equal(NumberField.validate(0, { min: 1 }), 'must be ≥ 1');
  assert.equal(NumberField.validate(11, { max: 10 }), 'must be ≤ 10');
});
