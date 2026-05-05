// viewer/tests/unit/md-field.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const { MdField } = await import('../../js/components/edit/fields/md-field.js');

test('read mode renders value (newlines preserved as <br>)', () => {
  const el = MdField.read({ value: 'line1\nline2', readOnly: false });
  assert.match(el.innerHTML, /line1.*<br.*line2/s);
  assert.ok(el.classList.contains('ef-md'));
  assert.ok(el.classList.contains('ef-editable'));
});

test('edit mode renders textarea with value', () => {
  const el = MdField.edit({ value: 'hi', onChange: () => {}, onCommit: () => {}, onCancel: () => {} });
  assert.equal(el.tagName, 'TEXTAREA');
  assert.equal(el.value, 'hi');
});

test('edit mode plain Enter inserts newline (does not commit)', () => {
  let committed = false;
  const el = MdField.edit({ value: 'a', onChange: () => {}, onCommit: () => { committed = true; }, onCancel: () => {} });
  const ev = new dom.window.KeyboardEvent('keydown', { key: 'Enter' });
  // jsdom does not auto-insert newlines from synthetic keydown — we only assert
  // that the onCommit handler did NOT fire on plain Enter.
  el.dispatchEvent(ev);
  assert.equal(committed, false);
});

test('edit mode Cmd/Ctrl+Enter commits', () => {
  let committed = null;
  const el = MdField.edit({ value: 'a', onChange: () => {}, onCommit: (v) => { committed = v; }, onCancel: () => {} });
  el.value = 'b';
  el.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true }));
  assert.equal(committed, 'b');
});

test('edit mode Escape cancels', () => {
  let cancelled = false;
  const el = MdField.edit({ value: 'a', onChange: () => {}, onCommit: () => {}, onCancel: () => { cancelled = true; } });
  el.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape' }));
  assert.equal(cancelled, true);
});

test('coerce returns null for empty/whitespace', () => {
  assert.equal(MdField.coerce(''), null);
  assert.equal(MdField.coerce('   \n\n  '), null);
  assert.equal(MdField.coerce('hello\n'), 'hello');
});

test('validate enforces required', () => {
  assert.equal(MdField.validate(null, { required: true }), 'required');
  assert.equal(MdField.validate('x', { required: true }), null);
});
