// viewer/tests/unit/text-field.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;
globalThis.HTMLElement = dom.window.HTMLElement;

const { TextField } = await import('../../js/components/edit/fields/text-field.js');

test('read mode renders value as span with editable class when not readOnly', () => {
  const el = TextField.read({ value: 'hello', readOnly: false });
  assert.equal(el.tagName, 'SPAN');
  assert.equal(el.textContent, 'hello');
  assert.ok(el.classList.contains('ef-text'));
  assert.ok(el.classList.contains('ef-editable'));
});

test('read mode without value renders placeholder italics', () => {
  const el = TextField.read({ value: '', readOnly: false, placeholder: 'no title' });
  assert.equal(el.textContent, 'no title');
  assert.ok(el.classList.contains('ef-placeholder'));
});

test('read mode readOnly=true omits ef-editable class', () => {
  const el = TextField.read({ value: 'x', readOnly: true });
  assert.ok(!el.classList.contains('ef-editable'));
});

test('edit mode renders input with value preselected', () => {
  const el = TextField.edit({ value: 'hello', onChange: () => {}, onCommit: () => {}, onCancel: () => {} });
  assert.equal(el.tagName, 'INPUT');
  assert.equal(el.value, 'hello');
  assert.equal(el.type, 'text');
});

test('edit mode Enter calls onCommit with current value', () => {
  let committed = null;
  const el = TextField.edit({ value: 'a', onChange: () => {}, onCommit: (v) => { committed = v; }, onCancel: () => {} });
  el.value = 'b';
  el.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter' }));
  assert.equal(committed, 'b');
});

test('edit mode Escape calls onCancel', () => {
  let cancelled = false;
  const el = TextField.edit({ value: 'a', onChange: () => {}, onCommit: () => {}, onCancel: () => { cancelled = true; } });
  el.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape' }));
  assert.equal(cancelled, true);
});

test('edit mode input event calls onChange with current value', () => {
  let lastChange = null;
  const el = TextField.edit({ value: 'a', onChange: (v) => { lastChange = v; }, onCommit: () => {}, onCancel: () => {} });
  el.value = 'ab';
  el.dispatchEvent(new dom.window.Event('input'));
  assert.equal(lastChange, 'ab');
});

test('coerce trims and returns null for empty', () => {
  assert.equal(TextField.coerce('  hi  '), 'hi');
  assert.equal(TextField.coerce('   '), null);
  assert.equal(TextField.coerce(''), null);
});

test('validate enforces required', () => {
  assert.equal(TextField.validate(null, { required: true }), 'required');
  assert.equal(TextField.validate('x', { required: true }), null);
});

test('validate enforces maxLength', () => {
  assert.equal(TextField.validate('abcdef', { maxLength: 3 }), 'too long (max 3)');
  assert.equal(TextField.validate('abc', { maxLength: 3 }), null);
});
