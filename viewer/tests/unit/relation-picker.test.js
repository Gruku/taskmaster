// viewer/tests/unit/relation-picker.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const FAKE_BACKLOG = {
  tasks: [
    { id: 'v3-edit-001', title: 'Field renderers', status: 'todo' },
    { id: 'v3-edit-002', title: 'Modal shell', status: 'todo' },
    { id: 'v3-polish-029', title: 'Flat tasks fix', status: 'in-review' },
  ],
  epics: [
    { id: 'v3-edit', name: 'V3 Edit-in-UI' },
    { id: 'v3-polish', name: 'V3 Polish' },
  ],
  phases: [
    { id: 'ship-v3', name: 'Ship V3' },
  ],
};

const { makeRelationSource } = await import('../../js/components/edit/fields/relation-picker.js');

test('tasks source filters by id substring', async () => {
  const src = makeRelationSource('tasks', () => FAKE_BACKLOG);
  const out = await src('edit');
  assert.equal(out.length, 2);
  assert.equal(out[0].value, 'v3-edit-001');
  assert.match(out[0].label, /Field renderers/);
});

// Re-audit X-01: the status was a bare word ("In progress"); it is the shared marker's shape, word and tone.
test('a task suggestion carries its status as the shared marker meta', async () => {
  const out = await makeRelationSource('tasks', () => FAKE_BACKLOG)('flat');
  assert.deepEqual(out[0].marker, { label: 'In review', shape: '▲', tone: 'warning' });
  assert.equal(out[0].hint, undefined);
  const none = await makeRelationSource('tasks', () => ({ tasks: [{ id: 'T-1', title: 'No status' }] }))('T-1');
  assert.equal(none[0].marker, null);
});

test('tasks source filters by title substring', async () => {
  const src = makeRelationSource('tasks', () => FAKE_BACKLOG);
  const out = await src('flat');
  assert.equal(out.length, 1);
  assert.equal(out[0].value, 'v3-polish-029');
});

test('epics source returns id+name pairs', async () => {
  const src = makeRelationSource('epics', () => FAKE_BACKLOG);
  const out = await src('polish');
  assert.equal(out.length, 1);
  assert.equal(out[0].value, 'v3-polish');
  assert.match(out[0].label, /V3 Polish/);
});

test('unknown source kind throws', () => {
  assert.throws(() => makeRelationSource('bogus', () => FAKE_BACKLOG));
});

// ── A task is never offered as its own dependency ──
const BOARD = { tasks: ['T-101', 'T-102', 'T-103'].map((id) => ({ id, title: `Task ${id}`, status: 'todo' })) };

test('a source given ids to exclude never offers them', async () => {
  const out = await makeRelationSource('tasks', () => BOARD, { exclude: ['T-102'] })('T-10');
  assert.deepEqual(out.map((o) => o.value), ['T-101', 'T-103']);
  const all = await makeRelationSource('tasks', () => BOARD)('T-10');
  assert.deepEqual(all.map((o) => o.value), ['T-101', 'T-102', 'T-103'], 'nothing excluded by default');
});

test('Depends on, edited inline, does not offer the task itself', async () => {
  globalThis.queueMicrotask ??= queueMicrotask;
  const { RelationPicker } = await import('../../js/components/edit/fields/relation-picker.js');
  const { mountInlineField } = await import('../../js/components/edit/inline-field.js');
  const schema = {
    entity: 'task',
    fields: [{ key: 'depends_on', label: 'Depends on', renderer: RelationPicker, kind: 'tasks', getBacklog: () => BOARD }],
  };
  const root = document.createElement('div');
  document.body.replaceChildren(root);
  const ctrl = mountInlineField(root, { schema, fieldKey: 'depends_on', entity: { id: 'T-102', depends_on: [] }, onSave: async () => {} });
  try {
    root.querySelector('.ef-chips').click();
    const input = root.querySelector('input');
    input.value = 'T-10';
    input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    await new Promise((ok) => setTimeout(ok, 0));
    const offered = [...root.querySelectorAll('[role="option"]')].map((o) => o.textContent);
    assert.equal(offered.length, 2);
    assert.ok(offered.every((t) => !t.includes('T-102')), offered.join(' | '));
  } finally { ctrl.destroy(); root.remove(); }
});
