// viewer/tests/unit/inline-field.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;
globalThis.queueMicrotask = queueMicrotask;

const { TextField } = await import('../../js/components/edit/fields/text-field.js');
const { mountInlineField } = await import('../../js/components/edit/inline-field.js');

const SCHEMA = {
  entity: 'task',
  fields: [{ key: 'title', label: 'Title', renderer: TextField, required: true, maxLength: 140 }],
};

test('Keep mine retains a newer draft typed while the first save was rejected', async () => {
  const root = document.createElement('div');
  const host = document.createElement('div');
  host.id = 'conflict-banner-host';
  document.body.append(root, host);
  const saved = [];
  let reject;
  const first = new Promise((resolve, no) => { reject = no; });
  const ctrl = mountInlineField(root, {
    schema: SCHEMA, fieldKey: 'title', entity: {id: 'conflict-race', title: 'old'},
    onSave: async value => { saved.push(value); if (saved.length === 1) await first; },
  });
  try {
    root.querySelector('.ef-text').click();
    const input = root.querySelector('input');
    input.value = 'first';
    input.dispatchEvent(new dom.window.KeyboardEvent('keydown', {key: 'Enter'}));
    input.value = 'latest';
    input.dispatchEvent(new dom.window.Event('input'));
    reject(Object.assign(new Error('stale'), {code: 409, current: {title: 'peer'}, current_etag: 'peer-tag'}));
    await new Promise(resolve => setTimeout(resolve, 50));
    host.querySelector('.cb-keep-mine').click();
    await new Promise(resolve => setTimeout(resolve, 30));
    assert.deepEqual(saved, ['first', 'latest']);
    assert.equal(root.querySelector('.ef-text')?.textContent, 'latest');
  } finally { ctrl.destroy(); host.remove(); root.remove(); }
});

test('an Enter commit during an autosave drains the latest draft before closing', async () => {
  const root = document.createElement('div');
  document.body.appendChild(root);
  const saved = [];
  let release;
  const first = new Promise(resolve => { release = resolve; });
  const ctrl = mountInlineField(root, {
    schema: SCHEMA, fieldKey: 'title', entity: { id: 'race-001', title: 'old' },
    onSave: async value => { saved.push(value); if (saved.length === 1) await first; },
  });
  root.querySelector('.ef-text').click();
  const input = root.querySelector('input');
  input.value = 'first';
  input.dispatchEvent(new dom.window.Event('input'));
  await new Promise(resolve => setTimeout(resolve, 650));
  assert.deepEqual(saved, ['first']);
  input.value = 'latest';
  input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter' }));
  release();
  await new Promise(resolve => setTimeout(resolve, 30));
  assert.deepEqual(saved, ['first', 'latest']);
  assert.equal(root.querySelector('.ef-text')?.textContent, 'latest');
  ctrl.destroy();
});

test('mounts in read mode with editable affordance', () => {
  const root = document.createElement('div');
  document.body.appendChild(root);
  const ctrl = mountInlineField(root, {
    schema: SCHEMA, fieldKey: 'title', entity: { title: 'hello' },
    onSave: async () => {},
  });
  const span = root.querySelector('.ef-text');
  assert.equal(span.textContent, 'hello');
  assert.ok(span.classList.contains('ef-editable'));
  ctrl.destroy();
});

test('click swaps to edit mode', () => {
  const root = document.createElement('div');
  document.body.appendChild(root);
  const ctrl = mountInlineField(root, {
    schema: SCHEMA, fieldKey: 'title', entity: { title: 'hi' },
    onSave: async () => {},
  });
  root.querySelector('.ef-text').click();
  const inp = root.querySelector('input.ef-text-input');
  assert.ok(inp);
  assert.equal(inp.value, 'hi');
  ctrl.destroy();
});

test('Enter triggers onSave with new value and reverts to read mode', async () => {
  const root = document.createElement('div');
  document.body.appendChild(root);
  let saved = null;
  const ctrl = mountInlineField(root, {
    schema: SCHEMA, fieldKey: 'title', entity: { title: 'old' },
    onSave: async (v) => { saved = v; },
  });
  root.querySelector('.ef-text').click();
  const inp = root.querySelector('input.ef-text-input');
  inp.value = 'new';
  inp.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter' }));
  await new Promise(r => setTimeout(r, 30));
  assert.equal(saved, 'new');
  // After save, swap back to read mode.
  await new Promise(r => setTimeout(r, 30));
  assert.ok(root.querySelector('.ef-text'));
  ctrl.destroy();
});

test('Escape reverts without calling onSave', async () => {
  const root = document.createElement('div');
  document.body.appendChild(root);
  let called = false;
  const ctrl = mountInlineField(root, {
    schema: SCHEMA, fieldKey: 'title', entity: { title: 'old' },
    onSave: async () => { called = true; },
  });
  root.querySelector('.ef-text').click();
  const inp = root.querySelector('input.ef-text-input');
  inp.value = 'changed';
  inp.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape' }));
  await new Promise(r => setTimeout(r, 20));
  assert.equal(called, false);
  // Read view shows the ORIGINAL value, not the typed one.
  assert.equal(root.querySelector('.ef-text').textContent, 'old');
  ctrl.destroy();
});

test('readOnly skips edit mode entirely', () => {
  const root = document.createElement('div');
  document.body.appendChild(root);
  const ctrl = mountInlineField(root, {
    schema: SCHEMA, fieldKey: 'title', entity: { title: 'x' }, readOnly: true,
    onSave: async () => {},
  });
  root.querySelector('.ef-text').click();
  // Should still be read-mode after click.
  assert.ok(root.querySelector('.ef-text'));
  assert.equal(root.querySelector('input.ef-text-input'), null);
  ctrl.destroy();
});

test('save error shows ✕ indicator', async () => {
  const root = document.createElement('div');
  document.body.appendChild(root);
  const ctrl = mountInlineField(root, {
    schema: SCHEMA, fieldKey: 'title', entity: { title: 'old' },
    onSave: async () => ({ error: 'server hated it' }),
  });
  root.querySelector('.ef-text').click();
  const inp = root.querySelector('input.ef-text-input');
  inp.value = 'new';
  inp.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter' }));
  await new Promise(r => setTimeout(r, 50));
  const err = root.querySelector('.if-status-error');
  assert.ok(err, 'error indicator visible');
  ctrl.destroy();
});
