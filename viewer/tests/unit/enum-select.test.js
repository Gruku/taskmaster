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

test('edit returns a wrapper holding the <select> (exposed as .control) and an arrow icon', () => {
  const el = EnumSelect.edit({ value: 'done', options: STATUSES, onChange: () => {}, onCommit: () => {}, onCancel: () => {} });
  assert.equal(el.tagName, 'SPAN');
  assert.ok(el.classList.contains('ef-select'));
  const sel = el.control;
  assert.equal(sel.tagName, 'SELECT');
  assert.equal(sel, el.querySelector('select.ef-enum-select'));
  assert.equal(sel.options.length, 3);
  assert.equal(sel.value, 'done');
  // The arrow is an inline icon, hidden from assistive tech — never a background image with a colour literal.
  const arrow = el.querySelector('svg.icon');
  assert.ok(arrow);
  assert.equal(arrow.getAttribute('aria-hidden'), 'true');
  assert.equal(arrow.getAttribute('width'), '16');
  assert.equal(el.children.length, 2);
});

test('edit change event commits the new value', () => {
  let committed = null;
  const el = EnumSelect.edit({ value: 'todo', options: STATUSES, onChange: () => {}, onCommit: (v) => { committed = v; }, onCancel: () => {} });
  el.control.value = 'in-progress';
  el.control.dispatchEvent(new dom.window.Event('change'));
  assert.equal(committed, 'in-progress');
});

test('edit Escape on the select cancels', () => {
  let cancelled = false;
  const el = EnumSelect.edit({ value: 'todo', options: STATUSES, onChange: () => {}, onCommit: () => {}, onCancel: () => { cancelled = true; } });
  el.control.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape' }));
  assert.equal(cancelled, true);
});

test('read with marker: status shows the shape plus the word, inside the editable wrapper', () => {
  const el = EnumSelect.read({ value: 'in-progress', options: STATUSES, marker: 'status' });
  assert.ok(el.classList.contains('ef-enum') && el.classList.contains('ef-editable'));
  const marker = el.querySelector('.marker');
  assert.equal(marker.className, 'marker marker--accent');
  assert.equal(marker.querySelector('.marker__shape').dataset.shape, 'half');
  assert.equal(marker.querySelector('.marker__word').textContent, 'In progress');
  assert.equal(el.children.length, 1);
});

test('read with marker: priority uses the priority table', () => {
  const el = EnumSelect.read({ value: 'critical', options: [{ value: 'critical', label: 'Critical' }], marker: 'priority', readOnly: true });
  assert.equal(el.querySelector('.marker').className, 'marker marker--critical');
  assert.equal(el.querySelector('.marker__word').textContent, 'Critical');
  assert.ok(!el.classList.contains('ef-editable'));
});

test('read with marker: an unknown value is a neutral marker carrying the value as text, never markup', () => {
  const hostile = '<img src=x onerror=x>';
  const el = EnumSelect.read({ value: hostile, options: STATUSES, marker: 'status' });
  assert.equal(el.querySelector('img'), null);
  assert.equal(el.querySelector('.marker').className, 'marker marker--neutral');
  assert.equal(el.querySelector('.marker__word').textContent, hostile);
});

test('read with marker: an empty value is the placeholder, and an unknown marker kind falls back to the label', () => {
  for (const value of [null, undefined, '']) {
    const el = EnumSelect.read({ value, options: STATUSES, marker: 'status' });
    assert.equal(el.querySelector('.marker'), null);
    assert.equal(el.textContent, '—');
    assert.ok(el.classList.contains('ef-placeholder'));
  }
  for (const marker of ['severity', 'constructor', undefined]) {
    const el = EnumSelect.read({ value: 'done', options: STATUSES, marker });
    assert.equal(el.querySelector('.marker'), null);
    assert.equal(el.textContent, 'Done');
  }
});

test('validate enforces value-in-options', () => {
  assert.equal(EnumSelect.validate('done', { options: STATUSES }), null);
  assert.equal(EnumSelect.validate('bogus', { options: STATUSES }), 'invalid value');
  assert.equal(EnumSelect.validate(null, { options: STATUSES, required: true }), 'required');
});
