// viewer/tests/unit/schema.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const { TextField } = await import('../../js/components/edit/fields/text-field.js');
const { EnumSelect } = await import('../../js/components/edit/fields/enum-select.js');
const { runValidation, isSystemManaged } = await import('../../js/components/edit/schema.js');

const SCHEMA = {
  entity: 'task',
  fields: [
    { key: 'title', label: 'Title', renderer: TextField, required: true, maxLength: 140 },
    { key: 'status', label: 'Status', renderer: EnumSelect, required: true,
      options: [{ value: 'todo', label: 'Todo' }, { value: 'done', label: 'Done' }] },
  ],
  systemManaged: ['id', 'created'],
  crossField: [
    (e) => e.title === 'forbidden' ? { key: 'title', error: 'reserved word' } : null,
  ],
};

test('runValidation passes when all fields valid', () => {
  const r = runValidation({ title: 'hi', status: 'todo' }, SCHEMA);
  assert.equal(r.valid, true);
  assert.deepEqual(r.errors, {});
});

test('runValidation collects per-field errors', () => {
  const r = runValidation({ title: '', status: 'bogus' }, SCHEMA);
  assert.equal(r.valid, false);
  assert.equal(r.errors.title, 'required');
  assert.equal(r.errors.status, 'invalid value');
});

test('runValidation runs cross-field rules', () => {
  const r = runValidation({ title: 'forbidden', status: 'todo' }, SCHEMA);
  assert.equal(r.valid, false);
  assert.equal(r.errors.title, 'reserved word');
});

test('isSystemManaged reads schema list', () => {
  assert.equal(isSystemManaged('id', SCHEMA), true);
  assert.equal(isSystemManaged('title', SCHEMA), false);
});
