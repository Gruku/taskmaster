// viewer/tests/unit/date-field.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const { DateField } = await import('../../js/components/edit/fields/date-field.js');

test('read renders ISO date or em-dash', () => {
  assert.equal(DateField.read({ value: '2026-05-04', readOnly: false }).textContent, '2026-05-04');
  assert.equal(DateField.read({ value: null, readOnly: false }).textContent, '—');
});

test('edit renders type=date with value', () => {
  const el = DateField.edit({ value: '2026-05-04', onChange: () => {}, onCommit: () => {}, onCancel: () => {} });
  assert.equal(el.type, 'date');
  assert.equal(el.value, '2026-05-04');
});

test('coerce extracts YYYY-MM-DD from full ISO string', () => {
  assert.equal(DateField.coerce('2026-05-04T16:30:00Z'), '2026-05-04');
  assert.equal(DateField.coerce('2026-05-04'), '2026-05-04');
  assert.equal(DateField.coerce(''), null);
  assert.equal(DateField.coerce('garbage'), null);
});
