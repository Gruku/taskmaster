// User intent: a task's docs are a map of type → path; the editor must show it as rows and hand back that same map,
// never a list, and must flag a row it cannot turn into a map entry instead of silently dropping it.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const { KeyValueField } = await import('../../js/components/edit/fields/keyvalue-field.js');

const fire = (el, type, init = {}) => el.dispatchEvent(Object.assign(new dom.window.Event(type, { bubbles: true, cancelable: true }), init));
function mount(args = {}) {
  const changes = [];
  const el = KeyValueField.edit({ onChange: (v) => changes.push(v), autoFocus: false, label: 'Docs', addLabel: 'Add doc', ...args });
  document.body.replaceChildren(el);
  const rows = () => [...el.querySelectorAll('.ef-kv-row')];
  const type = (input, text) => { input.value = text; fire(input, 'input'); };
  // What a form would keep after the last change.
  const kept = () => KeyValueField.coerce(changes.at(-1));
  return { el, changes, rows, type, kept, add: () => el.querySelector('.ef-kv-add').click() };
}

test('coerce: a map stays a map, with trimmed keys and text values', () => {
  assert.deepEqual(KeyValueField.coerce({ spec: 'docs/spec.md', ' plan ': ' docs/plan.md ' }), { spec: 'docs/spec.md', plan: 'docs/plan.md' });
  assert.deepEqual(KeyValueField.coerce({ n: 3 }), { n: 3 }, 'a stored value that is not text is kept as it is');
});

test('a stored value that is not text is edited as its JSON text and kept as it was unless that text is edited', () => {
  const stored = { spec: ['a.md', 'b.md'], plan: 'p.md' };
  assert.deepEqual(KeyValueField.coerce(stored), stored);
  const { rows, type, kept, changes } = mount({ value: stored });
  assert.deepEqual(rows().map((r) => r.querySelector('.ef-kv-value').value), ['["a.md","b.md"]', 'p.md']);
  type(rows()[1].querySelector('.ef-kv-value'), 'p2.md');
  assert.deepEqual(kept(), { spec: ['a.md', 'b.md'], plan: 'p2.md' });
  type(rows()[0].querySelector('.ef-kv-key'), 'specs');
  assert.deepEqual(kept(), { specs: ['a.md', 'b.md'], plan: 'p2.md' }, 'a renamed type keeps its value');
  type(rows()[0].querySelector('.ef-kv-value'), '["a.md"]');
  assert.deepEqual(kept(), { specs: '["a.md"]', plan: 'p2.md' }, 'edited text is what was typed');
  assert.ok(changes.length);
});

test('validate names every faulted row, in row order, joined with " · "; a long quoted value is cut to 40 characters', () => {
  assert.equal(KeyValueField.validate([{ key: '', value: 'x' }, { key: 'spec', value: '' }]), '"x" needs a type · "spec" needs a path or URL');
  const url = `https://example.com/${'a'.repeat(21)}`;
  assert.equal(url.length, 41);
  assert.equal(KeyValueField.validate([{ key: '', value: url }]), `"${url.slice(0, 40)}…" needs a type`);
  assert.equal(KeyValueField.validate([{ key: '', value: url.slice(0, 40) }]), `"${url.slice(0, 40)}" needs a type`, '40 is not cut');
  assert.equal(KeyValueField.validate([{ key: 'spec', value: 'a' }, { key: 'plan', value: 'b' }, { key: 'spec', value: 'c' }]), '"spec" is used twice');
});

test('markInvalid flags every input at fault and only those; off clears them all', () => {
  const { el, rows, type } = mount({ value: [{ key: '', value: 'x' }, { key: 'spec', value: '' }] });
  const flagged = () => [...el.querySelectorAll('[aria-invalid="true"]')];
  assert.equal(typeof el.markInvalid, 'function');
  el.markInvalid(true);
  assert.deepEqual(flagged(), [rows()[0].querySelector('.ef-kv-key'), rows()[1].querySelector('.ef-kv-value')]);
  el.markInvalid(false);
  assert.deepEqual(flagged(), []);
  el.markInvalid(true);
  type(rows()[0].querySelector('.ef-kv-key'), 'design');
  assert.deepEqual(flagged(), [rows()[1].querySelector('.ef-kv-value')], 'a fixed row stops being flagged');
  type(rows()[0].querySelector('.ef-kv-key'), 'spec');
  assert.deepEqual(flagged(), [rows()[1].querySelector('.ef-kv-key'), rows()[1].querySelector('.ef-kv-value')], 'a repeated type is flagged on the row that repeats it');
  rows()[0].querySelector('.ef-kv-remove').click();
  assert.deepEqual(flagged(), [rows()[0].querySelector('.ef-kv-value')], 'flags follow the rows as they go');
});

test('coerce: null, missing, an empty map and an empty list are all the empty map', () => {
  for (const empty of [null, undefined, {}, [], '', 0, true]) assert.deepEqual(KeyValueField.coerce(empty), {}, JSON.stringify(empty));
});

test('coerce: valid editor rows become a map; blank rows are dropped', () => {
  assert.deepEqual(KeyValueField.coerce([{ key: 'spec', value: 'a.md' }, { key: '', value: '' }, { key: ' plan ', value: 'b.md' }]),
    { spec: 'a.md', plan: 'b.md' });
});

test('coerce never turns rows it cannot represent into a map: they stay rows, and validate refuses them', () => {
  const cases = [
    [[{ key: 'spec', value: 'a.md' }, { key: 'spec', value: 'b.md' }], /spec.*twice/],
    [[{ key: '', value: 'a.md' }], /"a\.md" needs a type/i],
    [[{ key: 'spec', value: '' }], /spec.*needs a path or URL/],
  ];
  for (const [rows, message] of cases) {
    const kept = KeyValueField.coerce(rows);
    assert.ok(Array.isArray(kept), 'kept as rows');
    assert.match(KeyValueField.validate(kept), message);
  }
});

test('validate: a map and the empty value pass; required refuses the empty value', () => {
  assert.equal(KeyValueField.validate({ spec: 'a.md' }), null);
  assert.equal(KeyValueField.validate({}), null);
  assert.equal(KeyValueField.validate(null), null);
  assert.equal(KeyValueField.validate({}, { required: true }), 'required');
});

test('a legacy list of "type: path" strings is shown as rows; an entry without a type keeps its text', () => {
  const { rows } = mount({ value: ['spec: docs/spec.md', 'https://example.com/plan', 'docs/notes.md'] });
  const shown = rows().map((r) => [r.querySelector('.ef-kv-key').value, r.querySelector('.ef-kv-value').value]);
  assert.deepEqual(shown, [['spec', 'docs/spec.md'], ['', 'https://example.com/plan'], ['', 'docs/notes.md']]);
});

test('edit shows one row per entry; with none there is one blank row, so the label always has an input to reach', () => {
  const full = mount({ value: { spec: 'docs/spec.md', plan: 'docs/plan.md' }, id: 'docs' });
  assert.equal(full.rows().length, 2);
  assert.equal(full.el.control, full.rows()[0].querySelector('.ef-kv-key'));
  assert.equal(full.el.control.id, 'docs');
  for (const empty of [null, undefined, {}, [], 3]) {
    const none = mount({ value: empty, id: 'docs' });
    assert.equal(none.rows().length, 1);
    assert.equal(none.el.control, none.rows()[0].querySelector('.ef-kv-key'));
    assert.equal(none.el.control.value, '');
    assert.equal(none.el.control.id, 'docs');
    assert.equal(document.querySelectorAll('#docs').length, 1);
    assert.deepEqual(none.changes, [], 'a blank row is not a change');
  }
  // The add button keeps its own name: a label pointed at it would rename it.
  assert.equal(full.el.querySelector('.ef-kv-add').textContent, 'Add doc');
  assert.equal(full.el.querySelector('.ef-kv-add').hasAttribute('id'), false);
});

test('editing one row hands back a map with exactly the edited content', () => {
  const { rows, type, kept } = mount({ value: { spec: 'docs/spec.md', plan: 'docs/plan.md' } });
  type(rows()[1].querySelector('.ef-kv-value'), 'docs/plan-v2.md');
  assert.deepEqual(kept(), { spec: 'docs/spec.md', plan: 'docs/plan-v2.md' });
  type(rows()[0].querySelector('.ef-kv-key'), 'design');
  assert.deepEqual(kept(), { design: 'docs/spec.md', plan: 'docs/plan-v2.md' });
});

test('add appends a row, focuses its type, and a blank row changes nothing', () => {
  const { el, rows, add, type, kept, changes } = mount({ value: { spec: 'a.md' } });
  add();
  assert.equal(rows().length, 2);
  assert.equal(document.activeElement, rows()[1].querySelector('.ef-kv-key'));
  if (changes.length) assert.deepEqual(kept(), { spec: 'a.md' });
  type(rows()[1].querySelector('.ef-kv-key'), 'plan');
  assert.ok(Array.isArray(kept()), 'a type without a path is not a map entry yet');
  type(rows()[1].querySelector('.ef-kv-value'), 'b.md');
  assert.deepEqual(kept(), { spec: 'a.md', plan: 'b.md' });
  assert.equal(el.control, rows()[0].querySelector('.ef-kv-key'));
});

test('remove drops the row, names itself, moves the id to the new first row and keeps focus in the field', () => {
  const { el, rows, kept } = mount({ value: { spec: 'a.md', plan: 'b.md' }, id: 'docs', describedBy: 'docs-err' });
  const remove = rows()[0].querySelector('.ef-kv-remove');
  assert.equal(remove.getAttribute('aria-label'), 'Remove spec');
  remove.click();
  assert.deepEqual(kept(), { plan: 'b.md' });
  assert.equal(rows().length, 1);
  const first = rows()[0].querySelector('.ef-kv-key');
  assert.equal(first.id, 'docs');
  assert.equal(first.getAttribute('aria-describedby'), 'docs-err');
  assert.equal(document.querySelectorAll('#docs').length, 1);
  assert.equal(document.activeElement, first);
  // The last row is emptied rather than removed.
  rows()[0].querySelector('.ef-kv-remove').click();
  assert.deepEqual(kept(), {});
  assert.equal(rows().length, 1);
  assert.equal(rows()[0].querySelector('.ef-kv-key').value, '');
  assert.equal(rows()[0].querySelector('.ef-kv-value').value, '');
  assert.equal(el.control, rows()[0].querySelector('.ef-kv-key'));
  assert.equal(el.control.id, 'docs');
  assert.equal(document.activeElement, el.control);
  assert.equal(rows()[0].querySelector('.ef-kv-remove').getAttribute('aria-label'), 'Remove row 1');
});

test('every input has an accessible name that says which row it is', () => {
  const { rows } = mount({ value: { spec: 'a.md', plan: 'b.md' } });
  assert.equal(rows()[0].querySelector('.ef-kv-key').placeholder, 'type');
  assert.equal(rows()[0].querySelector('.ef-kv-value').placeholder, 'path or URL');
  assert.equal(rows()[1].querySelector('.ef-kv-key').getAttribute('aria-label'), 'Type, row 2');
  assert.equal(rows()[1].querySelector('.ef-kv-value').getAttribute('aria-label'), 'Path or URL, row 2');
  rows()[0].querySelector('.ef-kv-remove').click();
  assert.equal(rows()[0].querySelector('.ef-kv-key').getAttribute('aria-label'), 'Type, row 1');
});

test('Escape is left to the form unless the field was given a cancel of its own', () => {
  const plain = mount({ value: { spec: 'a.md' } });
  const free = fire(plain.rows()[0].querySelector('.ef-kv-key'), 'keydown', { key: 'Escape' });
  assert.equal(free, true, 'not prevented: the modal may close');
  let cancelled = false;
  const inline = mount({ value: { spec: 'a.md' }, onCancel: () => { cancelled = true; } });
  const used = fire(inline.rows()[0].querySelector('.ef-kv-key'), 'keydown', { key: 'Escape' });
  assert.equal(used, false);
  assert.equal(cancelled, true);
});

test('read lists the entries, or a placeholder when there are none', () => {
  assert.match(KeyValueField.read({ value: { spec: 'docs/spec.md' } }).textContent, /spec.*docs\/spec\.md/);
  assert.match(KeyValueField.read({ value: null, placeholder: 'no docs' }).textContent, /no docs/);
  for (const odd of [3, 'x', ['a'], { a: { b: 1 } }, true]) assert.doesNotThrow(() => KeyValueField.read({ value: odd }));
});
