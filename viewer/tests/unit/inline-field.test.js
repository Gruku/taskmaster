// viewer/tests/unit/inline-field.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;
globalThis.queueMicrotask = queueMicrotask;

const { TextField } = await import('../../js/components/edit/fields/text-field.js');
const { EnumSelect } = await import('../../js/components/edit/fields/enum-select.js');
const { MdField } = await import('../../js/components/edit/fields/md-field.js');
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
    // The banner module is loaded on first use; how long that takes depends on how busy the machine is.
    for (let i = 0; i < 400 && !host.querySelector('.cb-keep-mine'); i++) await new Promise(resolve => setTimeout(resolve, 5));
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

test('a renderer that returns a wrapper around its control edits and saves inline like any other', async () => {
  const root = document.createElement('div');
  document.body.replaceChildren(root);
  const options = [{ value: 'todo', label: 'Todo' }, { value: 'done', label: 'Done' }];
  const saved = [];
  const ctrl = mountInlineField(root, {
    schema: { entity: 'task', fields: [{ key: 'status', label: 'Status', renderer: EnumSelect, options, marker: 'status' }] },
    fieldKey: 'status', entity: { id: 'wrap-1', status: 'todo' },
    onSave: async (v) => { saved.push(v); },
  });
  try {
    assert.equal(root.querySelector('.ef-enum .marker__word').textContent, 'Todo');
    root.querySelector('.ef-enum').click();
    const select = root.querySelector('.if-wrap .ef-select > select');
    assert.ok(select, 'the select is mounted inside its wrapper');
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(document.activeElement, select, 'inline editing puts focus in the control');
    select.value = 'done';
    select.dispatchEvent(new dom.window.Event('change'));
    await new Promise((resolve) => setTimeout(resolve, 20));
    assert.deepEqual(saved, ['done']);
    assert.equal(root.querySelector('select'), null);
    assert.equal(root.querySelector('.ef-enum .marker__word').textContent, 'Done');
  } finally { ctrl.destroy(); }
});

test('a click on a link inside a read-mode field follows the link instead of opening the editor', () => {
  const root = document.createElement('div');
  document.body.replaceChildren(root);
  const link = document.createElement('a');
  link.href = 'https://example.com/';
  link.textContent = 'spec';
  const Linked = { ...MdField, read: (args) => { const el = MdField.read(args); el.appendChild(link); return el; } };
  const ctrl = mountInlineField(root, {
    schema: { entity: 'task', fields: [{ key: 'notes', label: 'Notes', renderer: Linked }] },
    fieldKey: 'notes', entity: { id: 'link-1', notes: 'see' },
    onSave: async () => {},
  });
  try {
    link.addEventListener('click', (e) => e.preventDefault());   // jsdom would try to navigate
    link.click();
    assert.equal(root.querySelector('textarea'), null, 'the link click did not open the editor');
    root.querySelector('.ef-md').click();
    assert.ok(root.querySelector('textarea'), 'a click on the text still does');
  } finally { ctrl.destroy(); }
});

test('tabbing through an inline estimate editor writes nothing, whatever form the estimate is stored in', async () => {
  const { EstimateField } = await import('../../js/components/edit/fields/estimate-field.js');
  const schema = { entity: 'task', fields: [{ key: 'estimate', label: 'Estimate', renderer: EstimateField }] };
  for (const stored of [3, 'm', '2 weeks']) {
    const root = document.createElement('div');
    document.body.append(root);
    const saved = [];
    const ctrl = mountInlineField(root, {
      schema, fieldKey: 'estimate', entity: { id: 'est-tab', estimate: stored },
      onSave: async (value) => { saved.push(value); },
    });
    try {
      root.querySelector('.ef-estimate').click();
      const days = root.querySelector('input[type="number"]');
      assert.ok(days, 'the editor opened');
      days.focus();
      days.dispatchEvent(new dom.window.FocusEvent('focusout', { bubbles: true, relatedTarget: null }));
      await new Promise(resolve => setTimeout(resolve, 700));   // past the autosave debounce
      assert.deepEqual(saved, [], JSON.stringify(stored));
      assert.ok(root.querySelector('.ef-estimate'), 'back in read mode');
      assert.equal(root.querySelector('input'), null);
    } finally { ctrl.destroy(); root.remove(); }
  }
});

// A 409 is one of two things. With `current_etag` the write lost a race: the banner settles it. Without one the server
// refused the write (gates still open, a legacy layout): that is an error with the server's reason, the stored
// revision is left alone, and no banner claims another writer changed anything.
test('a refusing 409 shows the server\'s reason as an error, raises no banner and keeps the stored revision', async () => {
  const { store } = await import('../../js/store.js');
  const root = document.createElement('div');
  const host = document.createElement('div');
  host.id = 'conflict-banner-host';
  document.body.append(root, host);
  store.setEtag('task:refused-1', 'rev-7');
  const reason = 'Completion blocked: review-gate is still open';
  const ctrl = mountInlineField(root, {
    schema: SCHEMA, fieldKey: 'title', entity: { id: 'refused-1', title: 'old' },
    onSave: async () => { throw Object.assign(new Error(reason), { code: 409 }); },
  });
  try {
    root.querySelector('.ef-text').click();
    const input = root.querySelector('input');
    input.value = 'new';
    input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter' }));
    for (let i = 0; i < 100 && !root.querySelector('.if-status-error'); i++) await new Promise((resolve) => setTimeout(resolve, 5));
    // Give a wrongly loaded banner module time to arrive before saying there is none.
    await new Promise((resolve) => setTimeout(resolve, 100));
    assert.equal(root.querySelector('.if-status-error')?.title, reason);
    assert.equal(host.children.length, 0, 'no conflict banner');
    assert.equal(store.getEtag('task:refused-1'), 'rev-7');
    assert.ok(root.querySelector('input'), 'the edit stays open with the value typed');
    assert.equal(root.querySelector('input').value, 'new');
  } finally { ctrl.destroy(); host.remove(); root.remove(); }
});

test('a save error is shown as text beside the field, announced, and tied to the control; cancelling clears it', async () => {
  const root = document.createElement('div');
  document.body.append(root);
  const reason = 'Completion blocked: review-gate is still open';
  const ctrl = mountInlineField(root, {
    schema: SCHEMA, fieldKey: 'title', entity: { id: 'refused-2', title: 'old' },
    onSave: async () => { throw Object.assign(new Error(reason), { code: 409 }); },
  });
  try {
    root.querySelector('.ef-text').click();
    const input = root.querySelector('input');
    input.value = 'new';
    input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter' }));
    const message = () => root.querySelector('.if-error');
    for (let i = 0; i < 100 && !message()?.textContent; i++) await new Promise((resolve) => setTimeout(resolve, 5));
    assert.equal(message().textContent, reason, 'the reason is visible text, not only a tooltip');
    assert.equal(message().getAttribute('role'), 'alert');
    assert.ok(message().id);
    assert.ok(input.getAttribute('aria-describedby')?.split(' ').includes(message().id), 'the control is described by the message');
    assert.equal(root.querySelector('.if-status-error').getAttribute('aria-hidden'), 'true', 'the glyph is not read twice');

    input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape' }));
    assert.equal(message()?.textContent ?? '', '', 'cancelling clears the message');
    assert.equal(root.querySelector('.ef-text')?.textContent, 'old');
  } finally { ctrl.destroy(); root.remove(); }
});

test('a lost race (409 naming the current revision) still raises the banner, and settling it stores that revision', async () => {
  const { store } = await import('../../js/store.js');
  const root = document.createElement('div');
  const host = document.createElement('div');
  host.id = 'conflict-banner-host';
  document.body.append(root, host);
  store.setEtag('task:race-2', 'rev-1');
  const ctrl = mountInlineField(root, {
    schema: SCHEMA, fieldKey: 'title', entity: { id: 'race-2', title: 'old' },
    onSave: async () => { throw Object.assign(new Error('stale'), { code: 409, current: { title: 'peer' }, current_etag: 'rev-2' }); },
  });
  try {
    root.querySelector('.ef-text').click();
    const input = root.querySelector('input');
    input.value = 'mine';
    input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter' }));
    for (let i = 0; i < 400 && !host.querySelector('.cb-use-server'); i++) await new Promise((resolve) => setTimeout(resolve, 5));
    host.querySelector('.cb-use-server').click();
    assert.equal(store.getEtag('task:race-2'), 'rev-2');
    assert.equal(root.querySelector('.ef-text')?.textContent, 'peer');
  } finally { ctrl.destroy(); host.remove(); root.remove(); }
});

test('a save error\'s message clears when the next save succeeds', async () => {
  const root = document.createElement('div');
  document.body.append(root);
  let refuse = true;
  const saved = [];
  const ctrl = mountInlineField(root, {
    schema: SCHEMA, fieldKey: 'title', entity: { id: 'retry-1', title: 'old' },
    onSave: async (v) => {
      if (refuse) { refuse = false; return { error: 'Title is locked while in review' }; }
      saved.push(v);
    },
  });
  const wait = async (ok) => { for (let i = 0; i < 100 && !ok(); i++) await new Promise((resolve) => setTimeout(resolve, 5)); };
  try {
    root.querySelector('.ef-text').click();
    const input = root.querySelector('input');
    input.value = 'new';
    input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter' }));
    const message = root.querySelector('.if-error');
    await wait(() => message.textContent);
    assert.equal(message.textContent, 'Title is locked while in review');
    assert.ok(input.getAttribute('aria-describedby').split(' ').includes(message.id));

    input.value = 'newer';
    input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter' }));
    await wait(() => saved.length && root.querySelector('.ef-text'));
    assert.deepEqual(saved, ['newer']);
    assert.equal(message.textContent, '', 'the message is gone after the save that worked');
    assert.equal(root.querySelector('.ef-text').textContent, 'newer');
  } finally { ctrl.destroy(); root.remove(); }
});

const until = async (ok) => { for (let i = 0; i < 200 && !ok(); i++) await new Promise((resolve) => setTimeout(resolve, 5)); };

// ── Errors in words (I-2) ──
const httpError = (code, message) => Object.assign(new Error(message), { code });

test('a server failure is described in a sentence beside the field, never as the raw request', async () => {
  for (const [error, expected] of [
    [httpError(500, 'PATCH /api/tasks/x-1 → 500: {"ok": false, "error": "KeyError"}'), /could not save/i],
    [httpError(404, 'PATCH /api/tasks/x-1 → 404: {"ok": false, "error": "task x-1 not found"}'), /no longer exists/],
    [Object.assign(new Error('validation failed'), { code: 422, errors: { title: 'too long' } }), /^Title: too long$/],
  ]) {
    const root = document.createElement('div');
    document.body.append(root);
    const ctrl = mountInlineField(root, {
      schema: SCHEMA, fieldKey: 'title', entity: { id: 'x-1', title: 'old' },
      onSave: async () => { throw error; },
    });
    try {
      root.querySelector('.ef-text').click();
      const input = root.querySelector('input');
      input.value = 'new';
      input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter' }));
      await until(() => root.querySelector('.if-error')?.textContent);
      const text = root.querySelector('.if-error').textContent;
      assert.match(text, expected);
      assert.doesNotMatch(text, /PATCH|\/api\/|→|\{/);
      assert.equal(root.querySelector('.if-status-error').title, text);
    } finally { ctrl.destroy(); root.remove(); }
  }
});

test('an emptied field that was never set writes nothing: null, "" and [] are one emptiness (M-4)', async () => {
  const root = document.createElement('div');
  document.body.append(root);
  const saved = [];
  // A renderer that hands back the raw text, with no coerce of its own to turn '' into null first.
  const Raw = { read: TextField.read, edit: TextField.edit };
  const ctrl = mountInlineField(root, {
    schema: { entity: 'task', fields: [{ key: 'title', label: 'Title', renderer: Raw }] },
    fieldKey: 'title', entity: { id: 'empty-1', title: null },
    onSave: async (v) => { saved.push(v); },
  });
  try {
    root.querySelector('.ef-text').click();
    const input = root.querySelector('input');
    input.value = '';
    input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter' }));
    await new Promise((resolve) => setTimeout(resolve, 50));
    assert.deepEqual(saved, []);
  } finally { ctrl.destroy(); root.remove(); }
});
