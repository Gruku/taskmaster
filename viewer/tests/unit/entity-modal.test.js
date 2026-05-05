// viewer/tests/unit/entity-modal.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body><div id="entity-modal-host"></div></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.queueMicrotask = queueMicrotask;

const { TextField } = await import('../../js/components/edit/fields/text-field.js');
const { EnumSelect } = await import('../../js/components/edit/fields/enum-select.js');
const { openEntityModal } = await import('../../js/components/edit/entity-modal.js');

const STATUSES = [{ value: 'todo', label: 'Todo' }, { value: 'done', label: 'Done' }];
const SCHEMA = {
  entity: 'task',
  label: 'Task',
  fields: [
    { key: 'title', label: 'Title', renderer: TextField, required: true, maxLength: 140 },
    { key: 'status', label: 'Status', renderer: EnumSelect, required: true, options: STATUSES },
  ],
};

test('opens modal in create mode with empty fields and disabled Save', () => {
  const close = openEntityModal({ schema: SCHEMA, mode: 'create', initialEntity: {}, onSave: async () => {}, onCancel: () => {} });
  const modal = document.querySelector('.em-modal');
  assert.ok(modal, 'modal mounted');
  assert.match(modal.querySelector('.em-header').textContent, /Create.*Task/);
  const saveBtn = modal.querySelector('.em-save');
  assert.equal(saveBtn.disabled, true);
  close();
  assert.equal(document.querySelector('.em-modal'), null);
});

test('Save enables when required fields are filled and valid', () => {
  let savedEntity = null;
  const close = openEntityModal({
    schema: SCHEMA, mode: 'create', initialEntity: {},
    onSave: async (e) => { savedEntity = e; },
    onCancel: () => {},
  });
  const titleInput = document.querySelector('.em-field[data-key="title"] input');
  titleInput.value = 'hi'; titleInput.dispatchEvent(new dom.window.Event('input'));
  const statusSel = document.querySelector('.em-field[data-key="status"] select');
  statusSel.value = 'todo'; statusSel.dispatchEvent(new dom.window.Event('change'));
  const saveBtn = document.querySelector('.em-save');
  assert.equal(saveBtn.disabled, false);
  close();
});

test('Cancel closes without firing onSave', () => {
  let saveCalled = false;
  let cancelled = false;
  const close = openEntityModal({
    schema: SCHEMA, mode: 'create', initialEntity: {},
    onSave: async () => { saveCalled = true; },
    onCancel: () => { cancelled = true; },
  });
  document.querySelector('.em-cancel').click();
  assert.equal(saveCalled, false);
  assert.equal(cancelled, true);
  assert.equal(document.querySelector('.em-modal'), null);
});

test('edit mode prefills initialEntity values', () => {
  const close = openEntityModal({
    schema: SCHEMA, mode: 'edit',
    initialEntity: { title: 'existing', status: 'done' },
    onSave: async () => {}, onCancel: () => {},
  });
  const titleInput = document.querySelector('.em-field[data-key="title"] input');
  assert.equal(titleInput.value, 'existing');
  const statusSel = document.querySelector('.em-field[data-key="status"] select');
  assert.equal(statusSel.value, 'done');
  close();
});

test('Save calls onSave with current draft and closes on success', async () => {
  let savedEntity = null;
  const close = openEntityModal({
    schema: SCHEMA, mode: 'create', initialEntity: {},
    onSave: async (e) => { savedEntity = e; return undefined; },
    onCancel: () => {},
  });
  const titleInput = document.querySelector('.em-field[data-key="title"] input');
  titleInput.value = 'hi'; titleInput.dispatchEvent(new dom.window.Event('input'));
  const statusSel = document.querySelector('.em-field[data-key="status"] select');
  statusSel.value = 'todo'; statusSel.dispatchEvent(new dom.window.Event('change'));
  document.querySelector('.em-save').click();
  // Wait for microtask chain.
  await new Promise(r => setTimeout(r, 10));
  assert.deepEqual(savedEntity, { title: 'hi', status: 'todo' });
  assert.equal(document.querySelector('.em-modal'), null);
});
