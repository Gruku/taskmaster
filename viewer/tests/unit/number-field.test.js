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

test('a stored value that is not a number is shown beside an empty input, and the note describes the input', () => {
  const el = NumberField.edit({ value: 'beta', id: 'stage', describedBy: 'stage-error', autoFocus: false });
  const input = el.control;
  assert.equal(input.tagName, 'INPUT');
  assert.equal(input.id, 'stage');
  assert.equal(input.value, '');
  const note = el.querySelector('span.ef-num-note');
  assert.equal(note.textContent, 'Current: beta — not a number. It is kept unless you type one.');
  assert.ok(note.id);
  assert.deepEqual(input.getAttribute('aria-describedby').split(' '), ['stage-error', note.id]);
  assert.equal(NumberField.coerce('beta'), null, 'nothing a form would send');
  for (const fine of [3, '2', null, undefined, '']) {
    const plain = NumberField.edit({ value: fine, autoFocus: false });
    assert.equal(plain.querySelector?.('.ef-num-note') ?? null, null, JSON.stringify(fine));
  }
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
