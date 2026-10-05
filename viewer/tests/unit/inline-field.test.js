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

// Chrome blurs a focused input as it is removed, and a text field commits on blur. The editor that closes is taken
// away while focused: that blur must neither write a cancelled draft nor end the edit a second time.
test('the blur of an editor taken away as it closes writes nothing and ends the edit once', async () => {
  const { store } = await import('../../js/store.js');
  const saved = [];
  const root = document.createElement('div');
  document.body.appendChild(root);
  const ctrl = mountInlineField(root, {
    schema: SCHEMA, fieldKey: 'title', entity: { id: 'gone-1', title: 'old' },
    onSave: async (v) => { saved.push(v); },
  });
  try {
    root.querySelector('.ef-text').click();
    let input = root.querySelector('input');
    input.value = 'cancelled draft';
    input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape' }));
    input.dispatchEvent(new dom.window.FocusEvent('blur'));
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert.deepEqual(saved, [], 'Escape wrote nothing');
    assert.equal(root.querySelector('.ef-text').textContent, 'old');

    store.beginEdit('gone-1');   // another editor of the same task holds a lease too
    root.querySelector('.ef-text').click();
    input = root.querySelector('input');
    input.value = 'kept';
    input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter' }));
    for (let i = 0; i < 100 && root.querySelector('input'); i++) await new Promise((resolve) => setTimeout(resolve, 5));
    input.dispatchEvent(new dom.window.FocusEvent('blur'));
    await new Promise((resolve) => setTimeout(resolve, 30));
    assert.deepEqual(saved, ['kept']);
    assert.equal(store.isEditing('gone-1'), true, 'the other lease is still held: this edit ended once');
    store.endEdit('gone-1');
    assert.equal(store.isEditing('gone-1'), false);
  } finally { ctrl.destroy(); root.remove(); }
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

test('a save error is shown as text beside the field, announced, and tied to the control; cancelling keeps it in view', async () => {
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
    // The field is back on the stored value, and the reason it is still that value stays said beside it.
    assert.equal(root.querySelector('.ef-text')?.textContent, 'old');
    assert.equal(message().textContent, reason, 'cancelling keeps the reason in view');
    root.querySelector('.ef-text').click();
    assert.equal(message().textContent, '', 'opening the field again clears it');
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
    assert.equal(root.querySelector('.if-error').textContent, 'Conflict — see banner');
    host.querySelector('.cb-use-server').click();
    assert.equal(store.getEtag('task:race-2'), 'rev-2');
    assert.equal(root.querySelector('.ef-text')?.textContent, 'peer');
    assert.equal(root.querySelector('.if-error').textContent, '', 'settled: the banner it pointed to is gone, and so is the message');
    assert.equal(root.querySelector('.if-status-error'), null);
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

// ── A picker that is left without a choice, or whose choice is refused (I-1) ──
// An open inline editor holds the task's edit lease, and the detail views skip live updates while it is held.
const STATUS_SCHEMA = {
  entity: 'task',
  fields: [{ key: 'status', label: 'Status', renderer: EnumSelect, marker: 'status',
    options: [{ value: 'todo', label: 'Todo' }, { value: 'in-review', label: 'In review' }, { value: 'done', label: 'Done' }] }],
};
const until = async (ok) => { for (let i = 0; i < 200 && !ok(); i++) await new Promise((resolve) => setTimeout(resolve, 5)); };

function mountStatus(id, onSave) {
  const root = document.createElement('div');
  const outside = document.createElement('button');
  document.body.append(root, outside);
  const ctrl = mountInlineField(root, { schema: STATUS_SCHEMA, fieldKey: 'status', entity: { id, status: 'todo' }, onSave });
  return { root, outside, ctrl, select: () => root.querySelector('select'), done() { ctrl.destroy(); root.remove(); outside.remove(); } };
}

test('a status picker left without a choice closes and releases the task for live updates', async () => {
  const { store } = await import('../../js/store.js');
  const saved = [];
  const t = mountStatus('blur-1', async (v) => { saved.push(v); });
  try {
    t.root.querySelector('.ef-enum').click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    assert.equal(document.activeElement, t.select());
    assert.equal(store.isEditing('blur-1'), true, 'open: the lease is held');
    t.outside.focus();   // Tab or a click elsewhere
    assert.equal(t.select(), null, 'the picker closed');
    assert.equal(t.root.querySelector('.ef-enum .marker__word').textContent, 'Todo');
    assert.equal(store.isEditing('blur-1'), false, 'the lease is released');
    assert.deepEqual(saved, []);
  } finally { t.done(); }
});

test('a refused status choice puts the picker back on the stored value, keeps the reason, and closes when left', async () => {
  const { store } = await import('../../js/store.js');
  const reason = 'Completion blocked: review-gate is still open';
  const t = mountStatus('blur-2', async () => ({ error: reason }));
  try {
    t.root.querySelector('.ef-enum').click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const select = t.select();
    select.value = 'done';
    select.dispatchEvent(new dom.window.Event('change'));
    await until(() => t.root.querySelector('.if-error')?.textContent);
    assert.equal(t.root.querySelector('.if-error').textContent, reason);
    assert.equal(t.select(), select, 'still open, beside its reason');
    assert.equal(select.value, 'todo', 'it no longer shows "Done" as if the change took');
    t.outside.focus();
    assert.equal(t.select(), null);
    assert.equal(t.root.querySelector('.ef-enum .marker__word').textContent, 'Todo');
    assert.equal(t.root.querySelector('.if-error').textContent, reason, 'left with Tab, the reason is still said');
    assert.equal(store.isEditing('blur-2'), false);
  } finally { t.done(); }
});

test('a refusal that arrives after the picker was left closes it then', async () => {
  const { store } = await import('../../js/store.js');
  let answer;
  const t = mountStatus('blur-3', () => new Promise((resolve) => { answer = resolve; }));
  try {
    t.root.querySelector('.ef-enum').click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const select = t.select();
    select.value = 'done';
    select.dispatchEvent(new dom.window.Event('change'));
    t.outside.focus();
    assert.equal(t.select(), select, 'a choice in flight is not cancelled by leaving');
    answer({ error: 'refused' });
    await until(() => !t.select());
    assert.equal(t.select(), null);
    assert.equal(t.root.querySelector('.ef-enum .marker__word').textContent, 'Todo');
    assert.equal(t.root.querySelector('.if-error').textContent, 'refused', 'closed by the refusal, it still says why');
    assert.equal(store.isEditing('blur-3'), false);
  } finally { t.done(); }
});

// ── A refusal stays said after the picker is left (plan 2b Task 4) ──
test('a refused status choice keeps its reason in read mode and through a repaint, until the field is opened again', async () => {
  const t = mountStatus('stay-1', async () => ({ error: 'Gates are still open' }));
  const message = () => t.root.querySelector('.if-error');
  try {
    t.root.querySelector('.ef-enum').click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const select = t.select();
    select.value = 'done';
    select.dispatchEvent(new dom.window.Event('change'));
    await until(() => message()?.textContent);
    select.blur();
    assert.equal(t.select(), null, 'read mode');
    assert.equal(message().textContent, 'Gates are still open');
    assert.ok(t.root.querySelector('.if-status-error'), 'the cross stays beside it');

    t.ctrl.update({ id: 'stay-1', status: 'todo', title: 'changed elsewhere' });
    assert.equal(t.select(), null);
    assert.equal(message().textContent, 'Gates are still open', 'a repaint in read mode keeps it');

    t.root.querySelector('.ef-enum').click();
    assert.ok(t.select(), 'open again');
    assert.equal(message().textContent, '', 'opening the field clears it');
    assert.equal(t.root.querySelector('.if-status-error'), null);
    assert.equal(t.select().hasAttribute('aria-describedby'), false, 'nothing describes the control any more');
  } finally { t.done(); }
});

test('a status that saves after a refusal clears the reason', async () => {
  let refuse = true;
  const t = mountStatus('stay-2', async () => {
    if (refuse) { refuse = false; return { error: 'Gates are still open' }; }
  });
  const message = () => t.root.querySelector('.if-error');
  try {
    t.root.querySelector('.ef-enum').click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    let select = t.select();
    select.value = 'done';
    select.dispatchEvent(new dom.window.Event('change'));
    await until(() => message()?.textContent);
    select.blur();
    assert.equal(message().textContent, 'Gates are still open');

    t.root.querySelector('.ef-enum').click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    select = t.select();
    select.value = 'in-review';
    select.dispatchEvent(new dom.window.Event('change'));
    await until(() => !t.select());
    assert.equal(t.root.querySelector('.ef-enum .marker__word').textContent, 'In review');
    assert.equal(message().textContent, '');
  } finally { t.done(); }
});

test('the message goes to messageHost when one is given; the glyph stays beside the field, unread; both leave with it', () => {
  const root = document.createElement('h2');
  const messageHost = document.createElement('div');
  document.body.append(root, messageHost);
  const ctrl = mountInlineField(root, {
    schema: SCHEMA, fieldKey: 'title', entity: { id: 'host-1', title: 'Named' }, onSave: async () => {}, messageHost,
  });
  try {
    assert.equal(root.querySelector('.if-error'), null, 'no message in the heading');
    assert.equal(root.querySelector(':scope > .if-status')?.getAttribute('aria-hidden'), 'true',
      'the saving and saved glyphs sit beside the field, where they take no line of their own, and are never read');
    assert.deepEqual([...messageHost.children].map((el) => el.className), ['ef-error if-error'], 'the host holds the words alone');
    ctrl.destroy();
    assert.equal(messageHost.children.length, 0);
    assert.equal(root.children.length, 0);
  } finally { root.remove(); messageHost.remove(); }
});

// The message's aria-live at the moment its words were written, replayed from the observer's ordered records: the
// value an assistive technology would see when it picked up the change.
async function liveWhenSaid(message, act) {
  const records = [];
  const observer = new dom.window.MutationObserver((batch) => records.push(...batch));
  observer.observe(message, { childList: true, characterData: true, subtree: true, attributes: true, attributeFilter: ['aria-live'], attributeOldValue: true });
  await act();
  records.push(...observer.takeRecords());
  observer.disconnect();
  const at = records.findLastIndex((r) => r.type !== 'attributes' && (r.type === 'characterData' || r.addedNodes.length));
  assert.ok(at >= 0, 'words were written');
  const later = records.slice(at + 1).find((r) => r.type === 'attributes');
  return later ? later.oldValue : message.getAttribute('aria-live');
}

test('a refusal said again after a re-mount is already quiet when its words land; a fresh refusal is announced', async () => {
  const t = mountStatus('quiet-1', async () => ({ error: 'Gates are still open' }));
  try {
    const message = t.root.querySelector('.if-error');
    const wrap = t.root.querySelector('.if-wrap');
    assert.equal(await liveWhenSaid(message, () => wrap.sayRefusal('Gates are still open')), 'off');
    assert.equal(message.textContent, 'Gates are still open');

    t.root.querySelector('.ef-enum').click();
    await new Promise((resolve) => setTimeout(resolve, 0));
    const select = t.select();
    assert.equal(await liveWhenSaid(message, async () => {
      select.value = 'done';
      select.dispatchEvent(new dom.window.Event('change'));
      await until(() => message.textContent);
    }), null, 'a new refusal is an alert like any other');
  } finally { t.done(); }
});

// ── Errors in words (I-2) and a timer that outlives its save (M-5) ──
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

test('a refusal right after a successful save keeps its message: the success tick does not wipe it', async () => {
  const root = document.createElement('div');
  document.body.append(root);
  let n = 0;
  const ctrl = mountInlineField(root, {
    schema: SCHEMA, fieldKey: 'title', entity: { id: 'tick-1', title: 'old' },
    onSave: async () => (n++ ? { error: 'Title is locked while in review' } : undefined),
  });
  const commit = (text) => {
    root.querySelector('.ef-text').click();
    const input = root.querySelector('input');
    input.value = text;
    input.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter' }));
  };
  try {
    commit('first');
    await until(() => root.querySelector('.ef-text')?.textContent === 'first');
    commit('second');
    await until(() => root.querySelector('.if-error')?.textContent);
    await new Promise((resolve) => setTimeout(resolve, 900));   // past the success tick of the first save
    assert.equal(root.querySelector('.if-error').textContent, 'Title is locked while in review');
    assert.ok(root.querySelector('.if-status-error'), 'the cross is still there too');
  } finally { ctrl.destroy(); root.remove(); }
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
