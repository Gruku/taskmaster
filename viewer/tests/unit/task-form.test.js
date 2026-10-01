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

test('every field belongs to one of the four form groups, in the order the form lays them out', () => {
  const s = taskSchema({ getBacklog: FAKE });
  const byGroup = {};
  for (const f of s.fields) (byGroup[f.group] ??= []).push(f.key);
  assert.deepEqual(byGroup, {
    basics: ['title', 'status', 'priority', 'epic', 'phase', 'estimate', 'stage'],
    tracking: ['sub_repo', 'release', 'branch', 'worktree'],
    relations: ['depends_on', 'docs', 'anchors'],
    content: ['description', 'specification', 'plan', 'notes', 'review_instructions', 'patchnote'],
  });
});

test('status and priority read as markers; no other field does', () => {
  const s = taskSchema({ getBacklog: FAKE });
  const markers = Object.fromEntries(s.fields.filter(f => f.marker).map(f => [f.key, f.marker]));
  assert.deepEqual(markers, { status: 'status', priority: 'priority' });
  const status = s.fields.find(f => f.key === 'status');
  const el = status.renderer.read({ value: 'done', ...status });
  assert.equal(el.querySelector('.marker__word').textContent, 'Done');
});

test('estimate is picked with the estimate field and labelled plainly', () => {
  const s = taskSchema({ getBacklog: FAKE });
  const estimate = s.fields.find(f => f.key === 'estimate');
  assert.equal(estimate.label, 'Estimate');
  const base = { title: 'x', status: 'todo', priority: 'medium', epic: 'v3-edit' };
  for (const ok of ['S', 'M', 'L', '3d', null, undefined]) {
    assert.equal(runValidation({ ...base, estimate: ok }, s).valid, true, JSON.stringify(ok));
  }
  for (const bad of ['XL', '0d', '-1d', '3 d']) {
    assert.ok(runValidation({ ...base, estimate: bad }, s).errors.estimate, bad);
  }
});

test('a task with null, missing or wrong-typed fields validates and renders without throwing', () => {
  const s = taskSchema({ getBacklog: FAKE });
  const odd = {
    id: 't-1', title: 'x', status: 'someday', priority: 7, epic: 'v3-edit', phase: null,
    estimate: 3, stage: '2', depends_on: null, docs: { spec: 'docs/spec.md' }, anchors: 'a.py',
    description: 42, notes: null, plan: ['a'],
  };
  let result;
  assert.doesNotThrow(() => { result = runValidation(odd, s); });
  assert.equal(result.errors.estimate, undefined, 'a stored bare number of days is accepted');
  assert.equal(result.errors.depends_on, undefined);
  assert.equal(result.errors.status, 'invalid value');
  for (const f of s.fields) {
    assert.doesNotThrow(() => f.renderer.read({ value: odd[f.key], ...f }), `read ${f.key}`);
    assert.doesNotThrow(() => {
      const el = f.renderer.edit({ value: odd[f.key], onChange() {}, onCommit() {}, onCancel() {}, ...f, autoFocus: false });
      assert.ok((el.control ?? el).tagName, `edit ${f.key} exposes a control`);
    }, `edit ${f.key}`);
    assert.doesNotThrow(() => f.renderer.coerce(odd[f.key]), `coerce ${f.key}`);
  }
  assert.doesNotThrow(() => runValidation({}, s));
});

test('neither two-column group ends on a lone field: Title spans the row and the rest pair up', () => {
  const s = taskSchema({ getBacklog: FAKE });
  for (const group of ['basics', 'tracking']) {
    const cells = s.fields.filter(f => f.group === group).reduce((n, f) => n + (f.wide ? 2 : 1), 0);
    assert.equal(cells % 2, 0, group);
  }
  assert.deepEqual(s.fields.filter(f => f.wide).map(f => f.key), ['title']);
});

test('docs is edited as a map of type to path, never as a list', async () => {
  const { KeyValueField } = await import('../../js/components/edit/fields/keyvalue-field.js');
  const s = taskSchema({ getBacklog: FAKE });
  const docs = s.fields.find(f => f.key === 'docs');
  assert.equal(docs.renderer, KeyValueField);
  const base = { title: 'x', status: 'todo', priority: 'medium', epic: 'v3-edit' };
  assert.equal(runValidation({ ...base, docs: { spec: 'docs/spec.md' } }, s).valid, true);
  assert.ok(runValidation({ ...base, docs: [{ key: 'a', value: '1' }, { key: 'a', value: '2' }] }, s).errors.docs);
});
