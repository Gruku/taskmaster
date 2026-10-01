// User intent: every field in a form can be tied to its visible label and its error message — whatever control the
// renderer builds, the id and the description land on the one control a label click should reach.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const { TextField } = await import('../../js/components/edit/fields/text-field.js');
const { MdField } = await import('../../js/components/edit/fields/md-field.js');
const { EnumSelect } = await import('../../js/components/edit/fields/enum-select.js');
const { NumberField } = await import('../../js/components/edit/fields/number-field.js');
const { DateField } = await import('../../js/components/edit/fields/date-field.js');
const { ChipInput } = await import('../../js/components/edit/fields/chip-input.js');
const { RelationPicker } = await import('../../js/components/edit/fields/relation-picker.js');
const { EstimateField } = await import('../../js/components/edit/fields/estimate-field.js');

const noop = () => {};
const OPTIONS = [{ value: 'a', label: 'A' }, { value: 'b', label: 'B' }];
// name → [renderer, extra edit args, the tag and type the bound control must have]
const RENDERERS = {
  TextField: [TextField, {}, 'INPUT', 'text'],
  MdField: [MdField, {}, 'TEXTAREA', 'textarea'],
  EnumSelect: [EnumSelect, { options: OPTIONS }, 'SELECT', 'select-one'],
  NumberField: [NumberField, {}, 'INPUT', 'number'],
  DateField: [DateField, {}, 'INPUT', 'date'],
  ChipInput: [ChipInput, { source: async () => [] }, 'INPUT', 'text'],
  RelationPicker: [RelationPicker, { kind: 'tasks', getBacklog: () => ({ tasks: [] }) }, 'INPUT', 'text'],
  EstimateField: [EstimateField, {}, 'INPUT', 'number'],
};
const edit = (name, args = {}) => {
  const [renderer, extra] = RENDERERS[name];
  const el = renderer.edit({ value: undefined, onChange: noop, onCommit: noop, onCancel: noop, ...extra, ...args });
  document.body.replaceChildren(el);
  return el;
};
const tick = () => new Promise((resolve) => setTimeout(resolve, 0));

for (const [name, [, , tag, type]] of Object.entries(RENDERERS)) {
  test(`${name}: id and aria-describedby land on the control a label click should reach`, () => {
    const el = edit(name, { id: `f-${name}`, describedBy: `f-${name}-err`, autoFocus: false });
    const control = el.control ?? el;
    assert.equal(control.tagName, tag);
    assert.equal(control.type, type);
    assert.equal(control.id, `f-${name}`);
    assert.equal(control.getAttribute('aria-describedby'), `f-${name}-err`);
    assert.equal(document.querySelectorAll(`#f-${name}`).length, 1, 'the id is used once');
    assert.equal(document.getElementById(`f-${name}`), control);
    assert.equal(document.querySelectorAll('[aria-describedby]').length, 1);
  });

  test(`${name}: without an id or a description the control carries neither attribute`, () => {
    const el = edit(name, { autoFocus: false });
    const control = el.control ?? el;
    assert.equal(control.hasAttribute('id'), false);
    assert.equal(control.hasAttribute('aria-describedby'), false);
  });

  test(`${name}: takes focus when mounted on its own, and leaves it alone when a form asks it to`, async () => {
    document.body.replaceChildren();
    const quiet = edit(name, { autoFocus: false });
    await tick();
    assert.notEqual(document.activeElement, quiet.control ?? quiet, 'autoFocus: false leaves focus where the form put it');
    const eager = edit(name);
    await tick();
    assert.equal(document.activeElement, eager.control ?? eager, 'inline editing still focuses the control');
  });

  test(`${name}: a null, missing or wrong-typed value never throws in read, edit, coerce or validate`, () => {
    const [renderer, extra] = RENDERERS[name];
    for (const value of [null, undefined, '', 3, 'text', { a: 1 }, ['x', null, 7, { value: 'v' }], true]) {
      const where = `${name} ${JSON.stringify(value)}`;
      assert.doesNotThrow(() => renderer.read({ value, ...extra }), where);
      assert.doesNotThrow(() => renderer.read({ value, readOnly: true, ...extra }), where);
      assert.doesNotThrow(() => edit(name, { value, autoFocus: false }), where);
      assert.doesNotThrow(() => renderer.validate(renderer.coerce(value), { ...extra }), where);
      assert.doesNotThrow(() => renderer.validate(value, { required: true, ...extra }), where);
    }
  });
}
