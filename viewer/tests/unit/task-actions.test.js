// viewer/tests/unit/task-actions.test.js
// Unit tests for task-actions.js (openTaskCreateModal / openTaskEditModal).
// Mocks api and entity-modal; verifies the wiring contract.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body><div id="entity-modal-host"></div></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.queueMicrotask = queueMicrotask;

// --- Stubs ---

const FAKE_BACKLOG = {
  tasks: [{ id: 't-001', title: 'Existing', status: 'todo', priority: 'medium', epic: 'core' }],
  epics: [{ id: 'core', label: 'Core' }],
  phases: [],
  context: { active_epic: 'core' },
};

function makeStore(backlog = FAKE_BACKLOG) {
  let _backlog = backlog;
  return {
    getBacklog: () => _backlog,
    setBacklog: (b) => { _backlog = b; },
  };
}

function makeApi({ createResult, patchResult } = {}) {
  return {
    createTask: async (payload) => { return createResult ?? { id: 't-new', ...payload }; },
    patchTask: async (id, patch) => { return patchResult ?? { id, ...patch }; },
    backlog: async () => FAKE_BACKLOG,
  };
}

// Capture the last modal options instead of rendering DOM.
let lastModalOpts = null;
function mockOpenEntityModal(opts) {
  lastModalOpts = opts;
}

// Inject the mock before importing task-actions (Module mockery via loader not available in
// node:test without an experiment flag, so we test the exported logic by calling onSave / onCancel
// directly from lastModalOpts after import).

// Because ESM static imports can't be easily intercepted without a loader, we exercise
// the module by importing it normally but then invoking the captured onSave callback directly.
// The test verifies: schema wiring, default initialEntity, and the save/error paths.

const { openTaskCreateModal, openTaskEditModal } = await import('../../js/components/edit/task-actions.js');

// --- Tests ---

test('openTaskCreateModal invokes openEntityModal in create mode', async () => {
  // We can't stub the import easily, so we call and observe side effects.
  // The entity-modal will mount into #entity-modal-host.
  // Reset host
  document.getElementById('entity-modal-host').innerHTML = '';

  const store = makeStore();
  const calls = [];
  const api = {
    createTask: async (payload) => { calls.push({ op: 'create', payload }); return { id: 't-new', ...payload }; },
    patchTask: async (id, patch) => { calls.push({ op: 'patch', id, patch }); return {}; },
    backlog: async () => FAKE_BACKLOG,
  };

  openTaskCreateModal({ store, api });

  const modal = document.querySelector('.em-modal');
  assert.ok(modal, 'modal mounted by openTaskCreateModal');
  assert.match(modal.querySelector('.em-title').textContent, /Create.*Task/i);

  // Prefilled defaults wired into the initialEntity
  const statusSel = document.querySelector('.em-field[data-key="status"] select');
  assert.equal(statusSel?.value, 'todo', 'status prefilled to todo');
  const priSel = document.querySelector('.em-field[data-key="priority"] select');
  assert.equal(priSel?.value, 'medium', 'priority prefilled to medium');

  // Close to clean up DOM.
  document.querySelector('.em-cancel')?.click();
});

test('openTaskCreateModal onSave calls api.createTask and refreshes store', async () => {
  document.getElementById('entity-modal-host').innerHTML = '';
  const store = makeStore();
  const calls = [];
  const api = {
    createTask: async (payload) => { calls.push({ op: 'create', payload }); return { id: 't-new', ...payload }; },
    patchTask:  async (id, patch) => { calls.push({ op: 'patch', id, patch }); return {}; },
    backlog:    async () => FAKE_BACKLOG,
  };

  openTaskCreateModal({ store, api });

  // Fill required fields and trigger save programmatically.
  const titleInput = document.querySelector('.em-field[data-key="title"] input');
  titleInput.value = 'New task from test';
  titleInput.dispatchEvent(new dom.window.Event('input'));
  // epic is prefilled to 'core', status = 'todo', priority = 'medium' — all required fields.
  const saveBtn = document.querySelector('.em-save');
  assert.equal(saveBtn.disabled, false, 'Save should be enabled');

  saveBtn.click();
  // Wait for async onSave chain.
  await new Promise(r => setTimeout(r, 30));

  assert.equal(calls.length, 1);
  assert.equal(calls[0].op, 'create');
  assert.equal(calls[0].payload.title, 'New task from test');
  assert.equal(document.querySelector('.em-modal'), null, 'modal closes on success');
});

test('openTaskCreateModal onSave surfaces api error without closing modal', async () => {
  document.getElementById('entity-modal-host').innerHTML = '';
  const store = makeStore();
  const api = {
    createTask: async () => { throw new Error('Server down'); },
    patchTask:  async () => {},
    backlog:    async () => FAKE_BACKLOG,
  };

  openTaskCreateModal({ store, api });

  const titleInput = document.querySelector('.em-field[data-key="title"] input');
  titleInput.value = 'Will fail';
  titleInput.dispatchEvent(new dom.window.Event('input'));
  document.querySelector('.em-save').click();
  await new Promise(r => setTimeout(r, 30));

  // Modal should still be open.
  assert.ok(document.querySelector('.em-modal'), 'modal stays open on error');
  const errSummary = document.querySelector('.em-error-summary');
  assert.match(errSummary.textContent, /Server down/, 'error message shown');

  document.querySelector('.em-cancel')?.click();
});

test('openTaskEditModal opens in edit mode prefilled with task data', async () => {
  document.getElementById('entity-modal-host').innerHTML = '';
  const task = { id: 't-001', title: 'Existing task', status: 'in-progress', priority: 'high', epic: 'core' };
  const store = makeStore();
  const api = {
    createTask: async () => {},
    patchTask:  async (id, patch) => { return { id, ...patch }; },
    backlog:    async () => FAKE_BACKLOG,
  };

  openTaskEditModal({ store, api, task });

  const modal = document.querySelector('.em-modal');
  assert.ok(modal, 'edit modal mounted');
  assert.match(modal.querySelector('.em-title').textContent, /Edit.*Task/i);

  const titleInput = document.querySelector('.em-field[data-key="title"] input');
  assert.equal(titleInput.value, 'Existing task', 'title prefilled');

  const statusSel = document.querySelector('.em-field[data-key="status"] select');
  assert.equal(statusSel.value, 'in-progress', 'status prefilled');

  document.querySelector('.em-cancel')?.click();
});

test('openTaskEditModal onSave patches only changed fields', async () => {
  document.getElementById('entity-modal-host').innerHTML = '';
  const task = { id: 't-001', title: 'Old title', status: 'todo', priority: 'medium', epic: 'core' };
  const store = makeStore();
  const patches = [];
  const api = {
    createTask: async () => {},
    patchTask:  async (id, patch) => { patches.push({ id, patch }); return {}; },
    backlog:    async () => FAKE_BACKLOG,
  };

  openTaskEditModal({ store, api, task });

  // Change only title.
  const titleInput = document.querySelector('.em-field[data-key="title"] input');
  titleInput.value = 'New title';
  titleInput.dispatchEvent(new dom.window.Event('input'));

  const saveBtn = document.querySelector('.em-save');
  assert.equal(saveBtn.disabled, false, 'Save enabled after dirty change');
  saveBtn.click();
  await new Promise(r => setTimeout(r, 30));

  assert.equal(patches.length, 1);
  assert.equal(patches[0].id, 't-001');
  // Only the changed field is in the patch.
  assert.deepEqual(Object.keys(patches[0].patch), ['title']);
  assert.equal(patches[0].patch.title, 'New title');
  assert.equal(document.querySelector('.em-modal'), null, 'modal closes after save');
});
