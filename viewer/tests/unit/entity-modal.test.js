// User intent: the task form must be honest — not dirty until something really changed, no complaint before the user
// has had a chance to type, Save that says why it will not save, and a save that carries only what was changed.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { readFileSync } from 'node:fs';

const PAGE = '<div class="shell"><button id="opener">o</button><section id="screen-mount"></section></div><div id="modal-host"></div><div id="conflict-banner-host"></div>';
const dom = new JSDOM(`<!doctype html><html><body>${PAGE}</body></html>`);
globalThis.document = dom.window.document;
globalThis.window = dom.window;
globalThis.HTMLElement = dom.window.HTMLElement;
// The form must never fall back to the browser's own dialogs.
const native = [];
for (const name of ['confirm', 'alert', 'prompt']) dom.window[name] = (...a) => { native.push([name, ...a]); return true; };

const { openModalCount } = await import('../../js/components/modal.js');
const { openEntityModal } = await import('../../js/components/edit/entity-modal.js');
const { taskSchema } = await import('../../js/components/edit/forms/task-form.js');

const BACKLOG = {
  epics: [{ id: 'viewer', name: 'Viewer' }, { id: 'store', name: 'Store' }],
  phases: [{ id: 'P1', name: 'Foundation' }],
  tasks: [{ id: 'T-101', title: 'Tokens', status: 'done' }, { id: 'T-105', title: 'Rebuild', status: 'todo' }],
};
const schema = () => taskSchema({ getBacklog: () => BACKLOG });
const CREATE = { epic: 'viewer', status: 'todo', priority: 'medium' };
const RICH = {
  id: 'T-102', title: 'Re-skin the cards', status: 'in-progress', priority: 'critical', epic: 'viewer', phase: 'P1',
  estimate: 'M', stage: 2, sub_repo: 'viewer', branch: 'feat/x', worktree: '.worktrees/x', release: '7.1.0',
  depends_on: ['T-101'], docs: { spec: 'docs/spec.md', plan: 'docs/plan.md' }, anchors: ['viewer/js/a.js'],
  description: 'Cards take the new grounds.\n\nSecond paragraph.', specification: '# Spec\n\nText', plan: '', notes: null,
  created: '2026-09-28',
};

const tick = (ms = 0) => new Promise((ok) => setTimeout(ok, ms));
const fire = (el, type, init = {}) => el.dispatchEvent(Object.assign(new dom.window.Event(type, { bubbles: true, cancelable: true }), init));
const dialogs = () => [...document.querySelectorAll('.modal')];
const form = () => dialogs()[0];
const confirmBox = () => document.querySelector('.modal--confirm');
const field = (key) => form().querySelector(`[data-key="${key}"]`);
const control = (key) => document.getElementById(field(key).querySelector('label').getAttribute('for'));
const errorOf = (key) => field(key).querySelector('.ef-error').textContent;
const saveBtn = () => form().querySelector('[data-save]');
const cancelBtn = () => form().querySelector('[data-cancel]');
const status = () => form().querySelector('.modal-footer [role="status"]').textContent;
const alertText = () => form().querySelector('.modal-footer [role="alert"]').textContent;
const type = (key, text) => { const c = control(key); c.value = text; fire(c, 'input'); };
const pick = (key, value) => { const c = control(key); c.value = value; fire(c, 'change'); };
const escape = () => fire(document.activeElement ?? document.body, 'keydown', { key: 'Escape' });

function open(opts = {}) {
  const calls = { saved: [], cancelled: 0, closed: 0 };
  const close = openEntityModal({
    schema: schema(), mode: 'create', initialEntity: { ...CREATE },
    onSave: async (draft, info) => { calls.saved.push({ draft, changes: info.changes }); },
    onCancel: () => { calls.cancelled++; },
    onClose: () => { calls.closed++; },
    ...opts,
  });
  return { close, calls };
}
const openEdit = (entity, opts = {}) => open({ mode: 'edit', initialEntity: entity, ...opts });

test.beforeEach(() => {
  assert.equal(openModalCount(), 0, 'the previous test left a modal open');
  document.body.innerHTML = PAGE;
  native.length = 0;
});
test.afterEach(() => { assert.deepEqual(native, [], 'no native dialog was used'); });

// ── 1. Title ──
test('1. the title names the action; in edit mode the eyebrow is the task id', async () => {
  const a = open();
  assert.equal(form().querySelector('.modal-title').textContent, 'Create task');
  assert.equal(form().querySelector('.modal-eyebrow').hidden, true);
  assert.equal(document.querySelectorAll('.modal-overlay').length, 1, 'the form stands on the shared shell, with no overlay of its own');
  a.close();
  const b = openEdit(RICH);
  assert.equal(form().querySelector('.modal-title').textContent, 'Edit task');
  assert.equal(form().querySelector('.modal-eyebrow').textContent, 'T-102');
  await tick();
  assert.equal(document.activeElement, control('title'), 'focus starts in the title');
  b.close();
  assert.equal(dialogs().length, 0);
  assert.equal(b.calls.closed, 1);
});

// ── 2. Layout ──
test('2. four headed groups; Title spans the grid; Content fields are collapsible sections', () => {
  const { close } = open();
  const groups = [...form().querySelectorAll('.eform-group')];
  assert.deepEqual(groups.map((g) => g.querySelector('.eform-group-title').textContent), ['Basics', 'Tracking', 'Relations', 'Content']);
  for (const g of groups) assert.equal(document.getElementById(g.getAttribute('aria-labelledby')), g.querySelector('.eform-group-title'));
  assert.deepEqual([...groups[0].querySelectorAll('.eform-grid > [data-key]')].map((f) => f.dataset.key),
    ['title', 'status', 'priority', 'epic', 'phase', 'estimate', 'stage']);
  assert.deepEqual([...groups[1].querySelectorAll('.eform-grid > [data-key]')].map((f) => f.dataset.key),
    ['sub_repo', 'release', 'branch', 'worktree']);
  assert.ok(field('title').classList.contains('eform-field--wide'));
  assert.equal(form().querySelectorAll('.eform-field--wide').length, 1);
  const sections = [...groups[3].querySelectorAll('.eform-section')];
  assert.deepEqual(sections.map((s) => s.dataset.key), ['description', 'specification', 'plan', 'notes', 'review_instructions', 'patchnote']);
  for (const s of sections) {
    const toggle = s.querySelector('button[aria-expanded]');
    const panel = document.getElementById(toggle.getAttribute('aria-controls'));
    assert.ok(s.contains(panel));
    assert.equal(panel.hidden, toggle.getAttribute('aria-expanded') !== 'true');
    assert.ok(panel.querySelector('textarea'));
  }
  close();
});

test('2. a Content section is open when it has content, or when it is the description of a new task', () => {
  const expanded = () => Object.fromEntries([...form().querySelectorAll('.eform-section')]
    .map((s) => [s.dataset.key, s.querySelector('button[aria-expanded]').getAttribute('aria-expanded') === 'true']));
  const a = open();
  assert.deepEqual(expanded(), { description: true, specification: false, plan: false, notes: false, review_instructions: false, patchnote: false });
  a.close();
  const b = openEdit(RICH);
  assert.deepEqual(expanded(), { description: true, specification: true, plan: false, notes: false, review_instructions: false, patchnote: false });
  b.close();
  const c = openEdit({ ...RICH, description: '' });
  assert.equal(expanded().description, false, 'an empty description of an existing task stays closed');
  c.close();
});

test('2. which section a new item opens on is the schema\'s to say, not a field name the form knows', async () => {
  const { MdField } = await import('../../js/components/edit/fields/md-field.js');
  const sections = { fields: [
    { key: 'description', label: 'Description', renderer: MdField, group: 'content' },
    { key: 'body', label: 'Body', renderer: MdField, group: 'content', open: true },
  ] };
  const expanded = () => Object.fromEntries([...form().querySelectorAll('.eform-section')]
    .map((s) => [s.dataset.key, s.querySelector('button[aria-expanded]').getAttribute('aria-expanded') === 'true']));
  const a = open({ schema: sections, initialEntity: {} });
  assert.deepEqual(expanded(), { description: false, body: true });
  a.close();
  const b = open({ schema: sections, mode: 'edit', initialEntity: { id: 'X-1' } });
  assert.deepEqual(expanded(), { description: false, body: false }, 'an existing item opens only what has content');
  b.close();
});

test('2. a collapsed section opens from its heading and says how much it holds when closed', () => {
  const { close } = openEdit(RICH);
  const section = field('specification');
  const toggle = section.querySelector('button[aria-expanded]');
  const panel = document.getElementById(toggle.getAttribute('aria-controls'));
  assert.equal(section.querySelector('.eform-section-hint').textContent, '', 'no hint while open');
  toggle.click();
  assert.equal(toggle.getAttribute('aria-expanded'), 'false');
  assert.equal(panel.hidden, true);
  assert.equal(section.querySelector('.eform-section-hint').textContent, '3 lines');
  assert.equal(field('plan').querySelector('.eform-section-hint').textContent, '', 'an empty section has no hint');
  toggle.click();
  assert.equal(panel.hidden, false);
  close();
  const b = openEdit({ ...RICH, notes: 'short' });
  field('notes').querySelector('button[aria-expanded]').click();
  assert.equal(field('notes').querySelector('.eform-section-hint').textContent, '5 characters');
  b.close();
});

// ── 3. Labels ──
test('3. every control has a bound label; required fields say "required" in words', () => {
  const { close } = openEdit(RICH);
  const s = schema();
  for (const f of s.fields) {
    const wrap = field(f.key);
    const label = wrap.querySelector('label');
    assert.ok(label, `${f.key} has a label`);
    const c = document.getElementById(label.getAttribute('for'));
    assert.ok(c && wrap.contains(c), `${f.key}: the label points at a control inside its field`);
    assert.ok(['INPUT', 'SELECT', 'TEXTAREA', 'BUTTON'].includes(c.tagName), `${f.key}: ${c.tagName}`);
    assert.ok(label.textContent.startsWith(f.label), f.key);
    assert.equal(c.getAttribute('aria-describedby'), wrap.querySelector('.ef-error').id, `${f.key}: the error is its description`);
    const required = label.querySelector('.eform-required');
    assert.equal(required?.textContent ?? null, f.required ? 'required' : null, f.key);
  }
  assert.equal(form().querySelector('label').textContent.includes('*'), false);
  assert.equal(new Set([...form().querySelectorAll('[id]')].map((e) => e.id)).size, form().querySelectorAll('[id]').length, 'ids are unique');
  close();
});

// ── 4. Dirty ──
test('4. an untouched form is not dirty: Save is disabled and it closes without asking', async () => {
  for (const mode of ['create', 'edit']) {
    const { calls } = mode === 'create' ? open() : openEdit(RICH);
    await tick();
    assert.equal(saveBtn().disabled, true, mode);
    escape();
    await tick();
    assert.equal(dialogs().length, 0, `${mode}: closed at once, no confirm`);
    assert.equal(calls.cancelled, 1);
    assert.equal(calls.closed, 1);
  }
});

test('4. focusing and blurring every field leaves the form clean (EM-01)', async () => {
  const { calls } = openEdit(RICH);
  await tick();
  for (const toggle of form().querySelectorAll('.eform-section button[aria-expanded="false"]')) toggle.click();
  const controls = [...form().querySelectorAll('.modal-body input, .modal-body select, .modal-body textarea')];
  assert.ok(controls.length >= 18);
  for (const c of controls) c.focus();
  cancelBtn().focus();
  await tick(120);   // the chip inputs commit a moment after blur
  assert.equal(saveBtn().disabled, true);
  assert.deepEqual([...form().querySelectorAll('.ef-error')].filter((e) => e.textContent).map((e) => e.textContent), []);
  cancelBtn().click();
  await tick();
  assert.equal(dialogs().length, 0, 'closed without a confirm');
  assert.equal(calls.cancelled, 1);
});

test('4. typing and then restoring the original text is not dirty', async () => {
  const { close } = openEdit(RICH);
  type('title', 'Something else');
  assert.equal(saveBtn().disabled, false);
  type('title', 'Re-skin the cards');
  assert.equal(saveBtn().disabled, true);
  type('title', '  Re-skin the cards  ');
  assert.equal(saveBtn().disabled, true, 'surrounding space is not a change');
  type('description', RICH.description + '\n');
  assert.equal(saveBtn().disabled, true);
  close();
});

test('4. null, missing, an empty list, an empty map and an empty string are the same emptiness', async () => {
  const empties = { depends_on: [null, undefined, []], docs: [null, undefined, {}, []], anchors: [null, []], notes: [null, undefined, ''], branch: [null, ''], estimate: [null, ''] };
  for (const [key, values] of Object.entries(empties)) {
    for (const value of values) {
      const entity = { ...RICH, [key]: value };
      if (value === undefined) delete entity[key];
      const { close } = openEdit(entity);
      await tick();
      for (const c of field(key).querySelectorAll('input, textarea, button')) c.focus();
      cancelBtn().focus();
      await tick(100);
      assert.equal(saveBtn().disabled, true, `${key}: ${JSON.stringify(value)}`);
      close();
    }
  }
});

test('4. wrong-typed and legacy values open without throwing and are not dirty', async () => {
  const odd = {
    id: 't-1', title: 'x', status: 'someday', priority: 7, epic: 'gone-epic', phase: 'gone-phase', estimate: 3, stage: '2',
    depends_on: null, docs: ['spec: docs/spec.md', 'docs/loose.md'], anchors: 'a.py', description: 42, notes: null, plan: ['a'],
  };
  let handle;
  assert.doesNotThrow(() => { handle = openEdit(odd); });
  await tick();
  assert.equal(saveBtn().disabled, true);
  assert.equal(control('estimate').value, '3', 'a bare number reads as days');
  escape();
  await tick();
  assert.equal(dialogs().length, 0);
  assert.equal(handle.calls.saved.length, 0);
});

// ── 5. Closing ──
test('5. closing a dirty form asks in-app; "Keep editing" keeps the form and the edit, "Discard" closes', async () => {
  for (const how of ['escape', 'cancel', 'close button', 'overlay']) {
    const { calls } = open();
    await tick();
    type('title', 'Draft');
    const ask = () => {
      if (how === 'escape') escape();
      else if (how === 'cancel') cancelBtn().click();
      else if (how === 'close button') form().querySelector('.modal-close').click();
      else { const overlay = form().parentElement; for (const t of ['pointerdown', 'pointerup', 'click']) fire(overlay, t); }
    };
    ask();
    await tick();
    assert.ok(confirmBox(), `${how}: the confirm is shown`);
    assert.equal(confirmBox().querySelector('.modal-title').textContent, 'Discard changes?');
    assert.equal(confirmBox().querySelector('.modal-message').textContent, 'Your edits to this task will be lost.');
    assert.equal(confirmBox().querySelector('[data-confirm]').textContent, 'Discard');
    assert.ok(confirmBox().querySelector('[data-confirm]').classList.contains('btn--critical'));
    assert.equal(confirmBox().querySelector('[data-cancel]').textContent, 'Keep editing');
    confirmBox().querySelector('[data-cancel]').click();
    await tick();
    assert.equal(dialogs().length, 1, `${how}: still open`);
    assert.equal(control('title').value, 'Draft');
    assert.equal(calls.cancelled, 0);
    ask();
    await tick();
    confirmBox().querySelector('[data-confirm]').click();
    await tick();
    assert.equal(dialogs().length, 0, `${how}: discarded`);
    assert.equal(calls.cancelled, 1);
    assert.equal(calls.closed, 1);
    assert.equal(calls.saved.length, 0);
  }
});

// ── 6. Validation timing ──
test('6. nothing is flagged on open; a field is flagged once it was left, and the control is marked and described (EM-05)', async () => {
  const { close } = open();
  await tick();
  assert.equal(form().querySelectorAll('[aria-invalid="true"]').length, 0);
  assert.deepEqual([...form().querySelectorAll('.ef-error')].map((e) => e.textContent).filter(Boolean), []);
  assert.equal(status(), '');
  assert.equal(alertText(), '');
  control('title').focus();
  control('status').focus();
  assert.equal(errorOf('title'), 'Title is required');
  assert.equal(control('title').getAttribute('aria-invalid'), 'true');
  assert.equal(control('title').getAttribute('aria-describedby'), field('title').querySelector('.ef-error').id);
  assert.equal(status(), '', 'the summary waits for a save attempt');
  assert.equal(form().querySelectorAll('[aria-invalid="true"]').length, 1, 'only the field that was left');
  type('title', 'Now it has one');
  assert.equal(errorOf('title'), '');
  assert.equal(control('title').hasAttribute('aria-invalid'), false);
  close();
});

test('6. leaving a field for the footer does not flag it: a message must not move the button being clicked', async () => {
  const { close } = open();
  await tick();
  control('title').focus();
  cancelBtn().focus();
  assert.equal(errorOf('title'), '');
  close();
});

test('6. a field left while a press is held is judged only once the press is released, wherever it ends', async () => {
  const { close } = open();
  await tick();
  control('title').focus();
  fire(control('status'), 'pointerdown');
  control('status').focus();
  await tick();
  assert.equal(errorOf('title'), '', 'no message mid-press');
  fire(document, 'pointerup');
  await tick();
  assert.equal(errorOf('title'), 'Title is required');
  close();
});

test('6. a field is judged once focus leaves it for anywhere in the dialog but its header and footer controls, or the window', async () => {
  const { close } = open();
  await tick();
  type('description', 'Dirty, so Save can take focus');
  const leave = (to) => { control('title').focus(); if (to) to.focus(); else control('title').blur(); };
  leave(saveBtn());
  assert.equal(errorOf('title'), '', 'left for Save');
  leave(cancelBtn());
  assert.equal(errorOf('title'), '', 'left for Cancel');
  leave(form().querySelector('.modal-close'));
  assert.equal(errorOf('title'), '', 'left for the close button');
  leave(null);
  assert.equal(errorOf('title'), '', 'left the window (no element took focus)');
  leave(form());
  assert.equal(errorOf('title'), 'Title is required', 'a click on blank dialog space puts focus on the dialog itself');
  close();
});

test('6. the same rule read from the event: relatedTarget is the dialog → judged; Save or nothing → not', async () => {
  for (const [to, judged] of [[() => saveBtn(), false], [() => null, false], [() => form(), true]]) {
    const { close } = open();
    await tick();
    const title = control('title');
    title.dispatchEvent(new dom.window.FocusEvent('focusout', { bubbles: true, relatedTarget: to() }));
    assert.equal(errorOf('title'), judged ? 'Title is required' : '', String(to()?.className ?? null));
    close();
  }
});

test('18. the form reaches the shell only through its hooks: no walk up to the overlay, no listener of its own on the dialog', () => {
  const source = readFileSync(new URL('../../js/components/edit/entity-modal.js', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /parentElement/);
  assert.doesNotMatch(source, /dialog\.addEventListener/);
});

// ── 7. Save ──
test('7. Save is the primary button and is enabled exactly when the form is dirty', async () => {
  const { close } = open();
  assert.ok(saveBtn().classList.contains('btn--primary'));
  assert.ok(cancelBtn().classList.contains('btn--secondary'));
  assert.equal(saveBtn().disabled, true);
  type('description', 'A note');
  assert.equal(saveBtn().disabled, false, 'dirty but invalid: Save can be pressed and will say why');
  type('description', '');
  assert.equal(saveBtn().disabled, true);
  close();
});

test('7. Save with one invalid field shows its message, focuses it and says "1 field needs attention"', async () => {
  const { calls, close } = open();
  await tick();
  type('description', 'A note');
  control('description').focus();
  saveBtn().click();
  await tick();
  assert.equal(calls.saved.length, 0);
  assert.equal(errorOf('title'), 'Title is required');
  assert.equal(document.activeElement, control('title'));
  assert.equal(status(), '1 field needs attention');
  assert.equal(form().querySelector('.modal-footer [role="status"]').closest('.modal-footer') !== null, true);
  type('title', 'Fixed');
  assert.equal(status(), '', 'the summary clears with the last problem');
  close();
});

test('7. Save with three invalid fields says "3 fields need attention" and focuses the first in form order', async () => {
  const { close } = openEdit(RICH);
  await tick();
  type('title', '');
  type('estimate', '0');
  type('stage', '-4');
  saveBtn().click();
  await tick();
  assert.equal(status(), '3 fields need attention');
  assert.equal(document.activeElement, control('title'));
  assert.match(errorOf('estimate'), /S, M, L or a whole number of days/);
  assert.match(errorOf('stage'), /≥ 0/);
  type('title', 'Back');
  assert.equal(status(), '2 fields need attention');
  close();
});

test('7. an invalid field inside a collapsed section is opened before it takes focus', async () => {
  const s = schema();
  s.fields.find((f) => f.key === 'plan').required = true;
  const { close } = open({ schema: s });
  await tick();
  type('title', 'Has a title');
  saveBtn().click();
  await tick();
  assert.equal(field('plan').querySelector('button[aria-expanded]').getAttribute('aria-expanded'), 'true');
  assert.equal(document.activeElement, control('plan'));
  close();
});

test('7. Ctrl+Enter and ⌘+Enter save from anywhere, including a textarea; on a clean form they do nothing', async () => {
  for (const mod of ['ctrlKey', 'metaKey']) {
    const { calls } = open();
    await tick();
    fire(control('title'), 'keydown', { key: 'Enter', [mod]: true });
    await tick();
    assert.equal(calls.saved.length, 0);
    assert.equal(status(), '', 'a clean form is not scolded');
    type('title', 'From the keyboard');
    type('description', 'Body');
    const prevented = !fire(control('description'), 'keydown', { key: 'Enter', [mod]: true });
    await tick();
    assert.equal(prevented, true);
    assert.equal(calls.saved.length, 1, mod);
    assert.equal(dialogs().length, 0, 'saved and closed');
  }
});

test('7. text still sitting in a chip input when Ctrl+Enter is pressed is saved, not dropped', async () => {
  const { calls } = open();
  await tick();
  type('title', 'Has an anchor');
  const anchors = control('anchors');
  anchors.focus();
  anchors.value = 'CHANGELOG.md';
  fire(anchors, 'input');
  fire(anchors, 'keydown', { key: 'Enter', ctrlKey: true });
  await tick(120);
  assert.deepEqual(calls.saved[0].changes, { title: 'Has an anchor', anchors: ['CHANGELOG.md'] });
});

test('7. create sends the defaults plus what was typed, and nothing for fields left empty', async () => {
  const { calls } = open();
  await tick();
  for (const c of form().querySelectorAll('.modal-body input')) c.focus();
  type('title', '  New task  ');
  type('description', 'Body');
  saveBtn().click();
  await tick(120);
  assert.deepEqual(calls.saved, [{
    draft: { epic: 'viewer', status: 'todo', priority: 'medium', title: 'New task', description: 'Body' },
    changes: { title: 'New task', description: 'Body' },
  }]);
  assert.equal(dialogs().length, 0);
  assert.equal(calls.closed, 1);
  assert.equal(calls.cancelled, 0);
});

test('7. edit hands over only the changed fields; the draft keeps every untouched value as it was stored', async () => {
  const { calls } = openEdit(RICH);
  await tick();
  type('title', 'Renamed');
  saveBtn().click();
  await tick();
  assert.deepEqual(calls.saved[0].changes, { title: 'Renamed' });
  assert.deepEqual(calls.saved[0].draft, { ...RICH, title: 'Renamed' });
});

test('7. a cleared field is a change, sent as its empty value', async () => {
  const { calls } = openEdit(RICH);
  await tick();
  type('branch', '');
  field('docs').querySelectorAll('.ef-kv-remove').forEach((b) => b.click());
  field('depends_on').querySelector('.ef-chip-x').click();
  saveBtn().click();
  await tick();
  assert.deepEqual(calls.saved[0].changes, { branch: null, docs: {}, depends_on: [] });
});

// ── Docs: a map in, a map out ──
test('docs: untouched is not sent; editing one row sends a map with exactly that content; never a list', async () => {
  const a = openEdit(RICH);
  await tick();
  type('title', 'Other change');
  saveBtn().click();
  await tick();
  assert.equal('docs' in a.calls.saved[0].changes, false);

  const b = openEdit(RICH);
  await tick();
  const row = field('docs').querySelectorAll('.ef-kv-row')[1];
  const value = row.querySelector('.ef-kv-value');
  value.value = 'docs/plan-v2.md';
  fire(value, 'input');
  saveBtn().click();
  await tick();
  assert.deepEqual(b.calls.saved[0].changes, { docs: { spec: 'docs/spec.md', plan: 'docs/plan-v2.md' } });
  assert.equal(Array.isArray(b.calls.saved[0].draft.docs), false);
});

test('docs: a duplicate or unnamed row blocks the save with a message instead of sending something else', async () => {
  const { calls, close } = openEdit(RICH);
  await tick();
  const key = field('docs').querySelectorAll('.ef-kv-key')[1];
  key.value = 'spec';
  fire(key, 'input');
  saveBtn().click();
  await tick();
  assert.equal(calls.saved.length, 0);
  assert.match(errorOf('docs'), /spec.*twice/);
  assert.equal(status(), '1 field needs attention');
  assert.equal(document.activeElement, key, 'focus lands on the row at fault, not the first row (M7)');
  close();
});

test('docs: the failed save focuses the input at fault in its row, and every row input is described by the message (M7)', async () => {
  const { calls, close } = openEdit(RICH);
  await tick();
  const rows = () => [...field('docs').querySelectorAll('.ef-kv-row')];
  const errId = field('docs').querySelector('.ef-error').id;
  rows()[1].querySelector('.ef-kv-value').value = '';
  fire(rows()[1].querySelector('.ef-kv-value'), 'input');
  saveBtn().click();
  await tick();
  assert.equal(calls.saved.length, 0);
  assert.match(errorOf('docs'), /"plan" needs a path or URL/);
  assert.equal(document.activeElement, rows()[1].querySelector('.ef-kv-value'), 'the empty path of the second row');
  for (const input of field('docs').querySelectorAll('.ef-kv-row input')) {
    assert.equal(input.getAttribute('aria-describedby'), errId, `${input.getAttribute('aria-label')} is described by the message`);
  }
  // A blank row in between is not at fault; the next real row without a type is.
  field('docs').querySelector('.ef-kv-add').click();
  rows()[1].querySelector('.ef-kv-value').value = 'docs/plan.md';
  fire(rows()[1].querySelector('.ef-kv-value'), 'input');
  field('docs').querySelector('.ef-kv-add').click();
  rows()[3].querySelector('.ef-kv-value').value = 'docs/untyped.md';
  fire(rows()[3].querySelector('.ef-kv-value'), 'input');
  saveBtn().click();
  await tick();
  assert.match(errorOf('docs'), /"docs\/untyped\.md" needs a type/);
  assert.equal(document.activeElement, rows()[3].querySelector('.ef-kv-key'));
  close();
});

test('docs: a stored value that is not text is edited as its JSON text and keeps what nobody edited', async () => {
  const docs = { spec: ['a.md', 'b.md'], plan: 'p.md' };
  const a = openEdit({ ...RICH, docs });
  await tick();
  const values = () => [...field('docs').querySelectorAll('.ef-kv-value')];
  assert.deepEqual(values().map((v) => v.value), ['["a.md","b.md"]', 'p.md']);
  for (const c of field('docs').querySelectorAll('input, button')) c.focus();
  cancelBtn().focus();
  assert.equal(saveBtn().disabled, true, 'opened and left untouched: not dirty');
  escape();
  await tick();
  assert.equal(dialogs().length, 0, 'closed without asking');

  const b = openEdit({ ...RICH, docs });
  await tick();
  values()[1].value = 'p2.md';
  fire(values()[1], 'input');
  saveBtn().click();
  await tick();
  assert.deepEqual(b.calls.saved[0].changes, { docs: { spec: ['a.md', 'b.md'], plan: 'p2.md' } });
});

test('docs: every faulted row is named and flagged — the type of a row without one, the path of a row without one', async () => {
  const { calls, close } = openEdit(RICH);
  await tick();
  const rows = () => [...field('docs').querySelectorAll('.ef-kv-row')];
  const set = (input, text) => { input.value = text; fire(input, 'input'); };
  set(rows()[0].querySelector('.ef-kv-key'), '');
  set(rows()[1].querySelector('.ef-kv-value'), '');
  saveBtn().click();
  await tick();
  assert.equal(calls.saved.length, 0);
  assert.equal(errorOf('docs'), '"docs/spec.md" needs a type · "plan" needs a path or URL');
  assert.deepEqual([...form().querySelectorAll('[aria-invalid="true"]')],
    [rows()[0].querySelector('.ef-kv-key'), rows()[1].querySelector('.ef-kv-value')]);
  assert.equal(document.activeElement, rows()[0].querySelector('.ef-kv-key'), 'focus goes to the first fault');
  set(rows()[0].querySelector('.ef-kv-key'), 'spec');
  assert.deepEqual([...form().querySelectorAll('[aria-invalid="true"]')], [rows()[1].querySelector('.ef-kv-value')]);
  set(rows()[1].querySelector('.ef-kv-value'), 'docs/plan.md');
  assert.deepEqual([...form().querySelectorAll('[aria-invalid="true"]')], []);
  assert.equal(errorOf('docs'), '');
  close();
});

test('a stored stage that is not a number is shown, kept, not dirty and not sent', async () => {
  const { calls } = openEdit({ ...RICH, stage: 'beta' });
  await tick();
  const input = control('stage');
  assert.equal(input.value, '');
  const note = field('stage').querySelector('.ef-num-note');
  assert.equal(note.textContent, 'Current: beta — not a number. It is kept unless you type one.');
  assert.ok(input.getAttribute('aria-describedby').split(' ').includes(note.id));
  assert.ok(input.getAttribute('aria-describedby').split(' ').includes(field('stage').querySelector('.ef-error').id));
  input.focus();
  cancelBtn().focus();
  assert.equal(saveBtn().disabled, true);
  type('title', 'Unrelated');
  saveBtn().click();
  await tick();
  assert.deepEqual(calls.saved[0].changes, { title: 'Unrelated' });
  assert.equal(calls.saved[0].draft.stage, 'beta');
});

// ── Text typed into a chip input that is not a chip yet ──
test('text left in a chip input is an edit: Save is enabled and closing asks first (M1)', async () => {
  const { calls } = open();
  await tick();
  assert.equal(saveBtn().disabled, true);
  const anchors = control('anchors');
  anchors.focus();
  anchors.value = 'CHANGELOG.md';
  fire(anchors, 'input');
  assert.equal(saveBtn().disabled, false, 'something was typed');
  cancelBtn().click();
  await tick();
  assert.ok(confirmBox(), 'closing asks before the text is lost');
  confirmBox().querySelector('[data-confirm]').click();
  await tick();
  assert.equal(dialogs().length, 0);
  assert.equal(calls.cancelled, 1);
});

test('text left in a relation input that was never picked from its list blocks the save and says so (M1)', async () => {
  const { calls, close } = openEdit(RICH);
  await tick();
  const deps = control('depends_on');
  deps.value = 'T-99';
  fire(deps, 'input');
  assert.equal(saveBtn().disabled, false);
  saveBtn().click();
  await tick();
  assert.equal(calls.saved.length, 0, 'nothing is sent while the text would be dropped');
  assert.match(errorOf('depends_on'), /list/);
  assert.equal(document.activeElement, deps);
  deps.value = '';
  fire(deps, 'input');
  assert.equal(saveBtn().disabled, true, 'cleared: clean again');
  close();
});

// ── Legacy values the form cannot represent ──
test('legacy: a stored estimate, status and phase the form cannot represent are shown and survive an unrelated edit', async () => {
  const legacy = { ...RICH, estimate: '2 weeks', status: 'someday', phase: 'P0-gone' };
  const { calls } = openEdit(legacy);
  await tick();
  const custom = field('estimate').querySelector('.ef-estimate-custom');
  assert.equal(custom.textContent, '2 weeks');
  assert.equal(custom.getAttribute('aria-pressed'), 'true');
  assert.ok(field('estimate').querySelector('.ef-estimate-note').textContent.length > 10);
  for (const key of ['status', 'phase']) {
    const option = control(key).selectedOptions[0];
    assert.equal(option.value, legacy[key]);
    assert.equal(option.textContent, legacy[key]);
    assert.equal(option.disabled, true);
  }
  assert.equal(saveBtn().disabled, true);
  type('title', 'Unrelated');
  saveBtn().click();
  await tick();
  assert.deepEqual(calls.saved[0].changes, { title: 'Unrelated' }, 'untouched legacy values do not block the save and are not sent');
  assert.equal(calls.saved[0].draft.estimate, '2 weeks');
  assert.equal(calls.saved[0].draft.status, 'someday');
  assert.equal(calls.saved[0].draft.phase, 'P0-gone');
});

test('legacy: changing such a field is allowed, validated, and can be put back', async () => {
  const legacy = { ...RICH, estimate: '2 weeks', status: 'someday' };
  const { calls } = openEdit(legacy);
  await tick();
  const sizes = [...field('estimate').querySelectorAll('.ef-estimate-sizes .ef-estimate-size')];
  const custom = field('estimate').querySelector('.ef-estimate-custom');
  sizes[0].click();
  assert.equal(custom.getAttribute('aria-pressed'), 'false');
  assert.equal(saveBtn().disabled, false);
  custom.click();
  assert.equal(custom.getAttribute('aria-pressed'), 'true');
  assert.equal(saveBtn().disabled, true, 'back to the stored value');
  type('estimate', '0');
  saveBtn().click();
  await tick();
  assert.equal(calls.saved.length, 0, 'a changed value is validated');
  assert.equal(status(), '1 field needs attention');
  type('estimate', '5');
  pick('status', 'done');
  saveBtn().click();
  await tick();
  assert.deepEqual(calls.saved[0].changes, { status: 'done', estimate: '5d' });
});

test('validation rule: create judges every field — a prefilled epic that no longer exists is refused in the form', async () => {
  // Everything a new item carries is sent, so nothing in it is "as stored".
  const a = open({ initialEntity: { ...CREATE, epic: 'gone' } });
  await tick();
  assert.equal(control('epic').selectedOptions[0].textContent, 'gone');
  type('title', 'x');
  saveBtn().click();
  await tick();
  assert.equal(a.calls.saved.length, 0, 'nothing is sent');
  assert.equal(errorOf('epic'), 'Unknown epic');
  assert.equal(control('epic').getAttribute('aria-invalid'), 'true');
  assert.equal(status(), '1 field needs attention');
  assert.equal(document.activeElement, control('epic'));
  pick('epic', 'store');
  saveBtn().click();
  await tick();
  assert.deepEqual(a.calls.saved[0].changes, { title: 'x', epic: 'store' });
});

test('validation rule: in edit an untouched value is not judged — only a required field left empty is', async () => {
  // An existing task whose required title is empty: editing something else still says so.
  const b = openEdit({ ...RICH, title: '', estimate: '2h', phase: 'gone' });
  await tick();
  type('branch', 'feat/y');
  saveBtn().click();
  await tick();
  assert.equal(b.calls.saved.length, 0);
  assert.equal(errorOf('title'), 'Title is required');
  assert.equal(errorOf('estimate'), '', 'the untouched legacy estimate is not an error');
  assert.equal(errorOf('phase'), '');
  assert.equal(status(), '1 field needs attention');
  type('title', 'Named');
  saveBtn().click();
  await tick();
  assert.deepEqual(b.calls.saved[0].changes, { title: 'Named', branch: 'feat/y' });
  assert.equal(b.calls.saved[0].draft.estimate, '2h');
  assert.equal(b.calls.saved[0].draft.phase, 'gone');
});

test('validation rule: a legacy value the user changed to something invalid is refused with a visible message', async () => {
  for (const stored of ['2 weeks', 'XL', '2h', '0.5d']) {
    const { calls, close } = openEdit({ ...RICH, estimate: stored });
    await tick();
    assert.equal(saveBtn().disabled, true, stored);
    type('estimate', '0');
    saveBtn().click();
    await tick();
    assert.equal(calls.saved.length, 0, stored);
    assert.match(errorOf('estimate'), /S, M, L or a whole number of days/);
    assert.equal(control('estimate').getAttribute('aria-invalid'), 'true');
    close();
  }
});

test('a required select with no stored value shows a placeholder instead of pretending the first option is chosen', async () => {
  const { calls, close } = open({ initialEntity: { status: 'todo', priority: 'medium' } });
  await tick();
  assert.equal(control('epic').value, '');
  type('title', 'x');
  saveBtn().click();
  await tick();
  assert.equal(calls.saved.length, 0);
  assert.equal(errorOf('epic'), 'Epic is required');
  close();
});

// ── 8. Saving ──
test('8. while saving: the button says so, every control is disabled and no close path works', async () => {
  let finish;
  const { calls } = open({ onSave: () => new Promise((ok) => { finish = ok; }) });
  await tick();
  type('title', 'Slow save');
  saveBtn().click();
  await tick();
  assert.equal(saveBtn().textContent, 'Saving…');
  const controls = [...form().querySelectorAll('button, input, select, textarea')];
  assert.deepEqual(controls.filter((c) => !c.disabled).map((c) => c.className), [], 'everything is disabled');
  escape();
  form().querySelector('.modal-close').click();
  cancelBtn().click();
  fire(form(), 'keydown', { key: 'Enter', ctrlKey: true });
  await tick();
  assert.equal(dialogs().length, 1, 'still open, and no confirm on top');
  finish();
  await tick();
  assert.equal(dialogs().length, 0);
  assert.equal(calls.closed, 1);
});

test('8. a server error is announced in the footer and the form stays open and editable', async () => {
  let fail = true;
  const saved = [];
  const { close } = open({ onSave: async (draft) => { if (fail) return { error: 'epic: unknown epic' }; saved.push(draft); } });
  await tick();
  type('title', 'Will fail');
  control('title').focus();
  saveBtn().click();
  await tick();
  assert.equal(dialogs().length, 1);
  assert.equal(alertText(), 'epic: unknown epic');
  assert.equal(status(), '');
  assert.equal(saveBtn().textContent, 'Save');
  assert.equal(saveBtn().disabled, false);
  assert.equal(control('title').disabled, false);
  assert.equal(form().querySelector('.modal-close').disabled, false);
  type('title', 'Will pass');
  assert.equal(alertText(), '', 'the old error goes once the user edits again');
  fail = false;
  saveBtn().click();
  await tick();
  assert.equal(saved.length, 1);
  assert.equal(dialogs().length, 0);
  close();
});

// The raw error goes to the console; what the page says is worded by describeWriteError.
async function quietly(run) {
  const warn = console.warn;
  console.warn = () => {};
  try { return await run(); } finally { console.warn = warn; }
}

test('8. a save that throws is said in words — never the raw message, a URL or a JSON body', () => quietly(async () => {
  const raw = Object.assign(new Error('POST /api/tasks → 500: {"error":"x"}'), { code: 500 });
  const { close } = open({ onSave: async () => { throw raw; } });
  await tick();
  type('title', 'x');
  saveBtn().click();
  await tick();
  assert.equal(alertText(), 'The server could not save this change. Try again in a moment.');
  for (const leak of ['→', '/api', '{']) assert.equal(alertText().includes(leak), false, leak);
  assert.equal(saveBtn().disabled, false);
  close();
  const b = open({ onSave: async () => { throw new Error('Failed to fetch'); } });
  await tick();
  type('title', 'x');
  saveBtn().click();
  await tick();
  assert.equal(alertText(), 'Could not reach the server, so nothing was saved. Check that the viewer is still running.');
  b.close();
}));

test('8. a wait that rejects is said in words too, with the form\'s own noun, and frees the form', () => quietly(async () => {
  let fail;
  const s = schema();
  const { close } = openEdit(RICH, {
    schema: { ...s, label: 'Idea' },
    onSave: async () => ({ error: 'Conflict — see banner', wait: new Promise((_, no) => { fail = no; }) }),
  });
  await tick();
  type('title', 'Mine');
  saveBtn().click();
  await tick();
  fail(Object.assign(new Error('PATCH /api/tasks/T-102 → 404: {"error":"gone"}'), { code: 404 }));
  await tick();
  assert.equal(alertText(), 'This idea no longer exists — it may have been archived or removed.');
  assert.equal(control('title').disabled, false);
  close();
}));

test('8. a save that is waiting on the user elsewhere holds the form until it is answered', async () => {
  for (const [answer, open_, message] of [[undefined, 0, ''], [{}, 1, ''], [{ error: 'Still stale' }, 1, 'Still stale']]) {
    let settle;
    const { close } = openEdit(RICH, { onSave: async () => ({ error: 'Conflict — see banner', wait: new Promise((ok) => { settle = ok; }) }) });
    await tick();
    type('title', 'Mine');
    saveBtn().click();
    await tick();
    assert.equal(alertText(), 'Conflict — see banner');
    assert.equal(saveBtn().textContent, 'Save');
    assert.equal(control('title').disabled, true, 'held: the form cannot drift from what the banner shows');
    escape();
    await tick();
    assert.equal(dialogs().length, 1);
    settle(answer);
    await tick();
    assert.equal(dialogs().length, open_, JSON.stringify(answer));
    if (open_) {
      assert.equal(alertText(), message);
      assert.equal(control('title').disabled, false);
      assert.equal(control('title').value, 'Mine');
      assert.equal(saveBtn().disabled, false, 'still dirty, still savable');
      close();
    }
  }
});

test('9. footer: the summary comes first, then Cancel, then Save', () => {
  const { close } = open();
  const footer = form().querySelector('.modal-footer');
  const order = [...footer.querySelectorAll('[role="status"], [role="alert"], button')].map((e) => e.getAttribute('role') ?? e.textContent);
  assert.deepEqual(order, ['status', 'alert', 'Cancel', 'Save']);
  close();
});
