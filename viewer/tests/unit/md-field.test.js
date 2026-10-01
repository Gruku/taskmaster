// viewer/tests/unit/md-field.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

// The real vendored parser runs here, loaded the way index.html loads it.
const dom = new JSDOM('<!doctype html><html><body></body></html>', { runScripts: 'outside-only' });
dom.window.eval(readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../../vendor/marked.min.js'), 'utf8'));
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const { MdField } = await import('../../js/components/edit/fields/md-field.js');

test('read mode renders the value as markdown: headings and tables are elements, not raw syntax', () => {
  const el = MdField.read({ value: '## Scope\n\nline1\nline2\n\n| a | b |\n|---|---|\n| 1 | 2 |\n', readOnly: false });
  assert.equal(el.querySelector('h2').textContent, 'Scope');
  assert.equal(el.querySelectorAll('table td').length, 2);
  assert.match(el.querySelector('p').innerHTML, /line1.*<br.*line2/s, 'a single newline is still a line break');
  assert.doesNotMatch(el.textContent, /##|\|/);
  assert.ok(el.classList.contains('ef-md'));
  assert.ok(el.classList.contains('md-body'), 'rendered markdown takes the shared document styles');
  assert.ok(el.classList.contains('ef-editable'));
});

test('read mode renders hostile content inert', () => {
  const el = MdField.read({ value: '<img src=x onerror=alert(1)> [a](javascript:alert(1)) <script>alert(1)</script> text' });
  assert.equal(el.querySelector('img, script'), null);
  for (const node of el.querySelectorAll('*')) {
    for (const attr of node.attributes) assert.ok(!/^on|^style$/.test(attr.name) && !/javascript:/i.test(attr.value));
  }
  assert.match(el.textContent, /text/);
});

test('read mode with no content shows the placeholder as plain text, without the document styles', () => {
  for (const value of [null, undefined, '', '  \n ']) {
    const el = MdField.read({ value, placeholder: '<b>nothing yet</b>' });
    assert.equal(el.textContent, '<b>nothing yet</b>');
    assert.equal(el.children.length, 0);
    assert.ok(el.classList.contains('ef-placeholder'));
    assert.ok(!el.classList.contains('md-body'));
  }
  assert.equal(MdField.read({ value: null }).textContent, 'no content');
});

test('read mode readOnly omits the editable affordance', () => {
  assert.ok(!MdField.read({ value: 'x', readOnly: true }).classList.contains('ef-editable'));
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
