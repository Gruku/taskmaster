// User intent: Escape belongs to whatever is open innermost — a field that used the key for itself (an inline edit to
// cancel, a suggestion list to close) says so, and one that did not leaves the key for the modal around it.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const F = '../../js/components/edit/fields/';
const { TextField } = await import(`${F}text-field.js`);
const { MdField } = await import(`${F}md-field.js`);
const { EnumSelect } = await import(`${F}enum-select.js`);
const { NumberField } = await import(`${F}number-field.js`);
const { DateField } = await import(`${F}date-field.js`);
const { ChipInput } = await import(`${F}chip-input.js`);
const { RelationPicker } = await import(`${F}relation-picker.js`);
const { EstimateField } = await import(`${F}estimate-field.js`);
const { KeyValueField } = await import(`${F}keyvalue-field.js`);

const BACKLOG = { tasks: [{ id: 'T-101', title: 'Tokens', status: 'done' }, { id: 'T-105', title: 'Rebuild', status: 'todo' }] };
const RENDERERS = {
  TextField: [TextField, {}],
  MdField: [MdField, {}],
  EnumSelect: [EnumSelect, { options: [{ value: 'a', label: 'A' }] }],
  NumberField: [NumberField, {}],
  DateField: [DateField, {}],
  ChipInput: [ChipInput, { source: async () => [], allowFree: true }],
  RelationPicker: [RelationPicker, { kind: 'tasks', getBacklog: () => BACKLOG }],
  EstimateField: [EstimateField, {}],
  KeyValueField: [KeyValueField, { value: { spec: 'a.md' } }],
};
const tick = (ms = 0) => new Promise((ok) => setTimeout(ok, ms));
// True when the key was left alone (not default-prevented), as dispatchEvent reports it.
const press = (el, key) => el.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
function mount(name, args = {}) {
  const [renderer, extra] = RENDERERS[name];
  const el = renderer.edit({ onChange() {}, onCommit() {}, autoFocus: false, ...extra, ...args });
  document.body.replaceChildren(el);
  return { el, control: el.control ?? el };
}

for (const name of Object.keys(RENDERERS)) {
  test(`${name}: Escape with nothing of its own to close is left for the modal`, () => {
    const { control } = mount(name);
    assert.equal(press(control, 'Escape'), true, 'not default-prevented');
  });

  test(`${name}: Escape cancels an inline edit and claims the key`, () => {
    let cancelled = 0;
    const { control } = mount(name, { onCancel: () => { cancelled++; } });
    assert.equal(press(control, 'Escape'), false, 'default-prevented');
    assert.equal(cancelled, 1);
  });
}

test('RelationPicker: Escape with the suggestion list open closes the list only; the next Escape is the modal’s', async () => {
  let cancelled = 0;
  for (const args of [{}, { onCancel: () => { cancelled++; } }]) {
    const { el, control } = mount('RelationPicker', args);
    control.focus();   // typed into, as a user does
    control.value = 'T-10';
    control.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
    await tick();
    const list = el.querySelector('.ef-chip-dropdown');
    assert.equal(list.querySelectorAll('.ef-chip-dd-row').length, 2);
    assert.equal(press(control, 'Escape'), false, 'the list used the key');
    assert.equal(list.isConnected, false);
    assert.equal(cancelled, 0, 'closing the list is not cancelling the edit');
    press(control, 'Enter');
    assert.equal(el.querySelectorAll('.ef-chip').length, 0, 'a closed list offers nothing to Enter');
  }
  const { control } = mount('RelationPicker');
  assert.equal(press(control, 'Escape'), true);
});

test('ChipInput: Escape with typed text clears the text first; a list a slow source returns afterwards stays closed', async () => {
  let answer;
  const { el, control } = mount('ChipInput', { source: () => new Promise((ok) => { answer = ok; }) });
  control.value = 'vie';
  control.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  assert.equal(press(control, 'Escape'), false);
  assert.equal(control.value, '');
  answer(['viewer']);
  await tick();
  assert.equal(el.querySelector('.ef-chip-dropdown'), null);
  assert.equal(press(control, 'Escape'), true);
});

test('ChipInput: leaving the input closes the list; free text that was typed becomes a chip instead of being lost', async () => {
  const changes = [];
  const { el, control } = mount('ChipInput', { value: ['a'], source: async () => ['alpha', 'alps'], onChange: (v) => changes.push(v) });
  control.focus();
  control.value = 'al';
  control.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  await tick();
  assert.notEqual(el.querySelector('.ef-chip-dropdown'), null);
  control.dispatchEvent(new dom.window.Event('blur'));
  await tick(120);
  assert.equal(el.querySelector('.ef-chip-dropdown'), null);
  assert.deepEqual(changes.at(-1), ['a', 'al']);
  assert.equal(control.value, '');
});

test('RelationPicker: leaving the input with a half-typed query adds nothing', async () => {
  const changes = [];
  const { control } = mount('RelationPicker', { onChange: (v) => changes.push(v) });
  control.value = 'T-10';
  control.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  await tick();
  control.dispatchEvent(new dom.window.Event('blur'));
  await tick(120);
  assert.deepEqual(changes, []);
});
