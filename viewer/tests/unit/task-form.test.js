// viewer/tests/unit/task-form.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const { taskSchema } = await import('../../js/components/edit/forms/task-form.js');
const { runValidation } = await import('../../js/components/edit/schema.js');

const FAKE = () => ({
  epics: [{ id: 'v3-edit', name: 'V3 Edit' }],
  phases: [{ id: 'ship-v3', name: 'Ship V3' }],
  tasks: [{ id: 'v3-edit-001', title: 'Renderers', status: 'todo' }],
});

test('schema includes all editable task fields', () => {
  const s = taskSchema({ getBacklog: FAKE });
  const keys = s.fields.map(f => f.key);
  for (const k of ['title', 'status', 'priority', 'epic', 'phase', 'estimate',
                    'depends_on', 'docs', 'anchors', 'description', 'notes',
                    'specification', 'plan', 'review_instructions']) {
    assert.ok(keys.includes(k), `missing ${k}`);
  }
});

test('systemManaged covers id/created/started/completed/etc', () => {
  const s = taskSchema({ getBacklog: FAKE });
  for (const k of ['id', 'created', 'started', 'completed', 'last_referenced',
                    'activity', 'spec_review', 'auto_mode', 'locked_by']) {
    assert.ok(s.systemManaged.includes(k), `missing systemManaged: ${k}`);
  }
});

test('valid task passes validation', () => {
  const s = taskSchema({ getBacklog: FAKE });
  const r = runValidation({ title: 'New task', status: 'todo', priority: 'medium', epic: 'v3-edit' }, s);
  assert.equal(r.valid, true);
});

test('invalid epic is flagged', () => {
  const s = taskSchema({ getBacklog: FAKE });
  const r = runValidation({ title: 'x', status: 'todo', priority: 'medium', epic: 'bogus' }, s);
  assert.equal(r.valid, false);
  assert.match(r.errors.epic, /unknown epic/);
});

test('depends_on with self id is rejected', () => {
  const s = taskSchema({ getBacklog: FAKE });
  const r = runValidation({
    id: 'v3-edit-001', title: 'x', status: 'todo', priority: 'medium',
    epic: 'v3-edit', depends_on: ['v3-edit-001'],
  }, s);
  assert.equal(r.valid, false);
  assert.match(r.errors.depends_on, /cannot depend on itself/);
});

test('priority must be in enum', () => {
  const s = taskSchema({ getBacklog: FAKE });
  const r = runValidation({ title: 'x', status: 'todo', priority: 'urgent', epic: 'v3-edit' }, s);
  assert.equal(r.valid, false);
  assert.equal(r.errors.priority, 'invalid value');
});
