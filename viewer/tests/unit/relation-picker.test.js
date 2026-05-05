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
