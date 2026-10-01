// User intent: an estimate is picked, not typed from memory — a size (S, M, L) or a whole number of days, never both,
// and a value the picker cannot produce is refused instead of being saved as free text.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const { EstimateField } = await import('../../js/components/edit/fields/estimate-field.js');

const noop = () => {};
function mount(args = {}) {
  const changes = [];
  const el = EstimateField.edit({ onChange: (v) => changes.push(v), onCommit: noop, onCancel: noop, autoFocus: false, ...args });
  document.body.replaceChildren(el);
  const sizes = Object.fromEntries([...el.querySelectorAll('button')].map((b) => [b.textContent, b]));
  const days = el.querySelector('input[type="number"]');
  const type = (text) => { days.value = text; days.dispatchEvent(new dom.window.Event('input', { bubbles: true })); };
  const pressed = () => Object.entries(sizes).filter(([, b]) => b.getAttribute('aria-pressed') === 'true').map(([k]) => k);
  return { el, sizes, days, type, pressed, changes };
}

test('coerce normalises what the picker can express and leaves the rest for validate to refuse', () => {
  for (const [raw, out] of [
    ['S', 'S'], ['s', 'S'], [' m ', 'M'], ['L', 'L'],
    ['3d', '3d'], ['3D', '3d'], ['12d', '12d'], ['03d', '3d'],
    [null, null], [undefined, null], ['', null], ['   ', null],
    ['XL', 'XL'], ['3 d', '3 d'], ['0d', '0d'], ['-1d', '-1d'], ['3', '3'],
  ]) assert.equal(EstimateField.coerce(raw), out, JSON.stringify(raw));
});

test('coerce reads a stored bare number as that many days, and never throws on a wrong type', () => {
  assert.equal(EstimateField.coerce(3), '3d');
  assert.equal(EstimateField.coerce(0), '0');
  assert.equal(EstimateField.coerce(2.5), '2.5');
  assert.equal(EstimateField.coerce(-1), '-1');
  for (const odd of [{}, [], ['S'], true, NaN]) assert.equal(typeof EstimateField.coerce(odd), 'string');
});

test('validate accepts a size or a positive whole number of days and refuses everything else', () => {
  for (const ok of ['S', 'M', 'L', '1d', '3d', '120d', 's', null, undefined, '']) {
    assert.equal(EstimateField.validate(ok), null, JSON.stringify(ok));
  }
  for (const bad of ['0d', '-1d', 'XL', '3 d', '3', 'd', '2.5d', '1e3d', 'SM', 'S 3d', 0, {}, []]) {
    assert.equal(typeof EstimateField.validate(bad), 'string', JSON.stringify(bad));
  }
  assert.equal(EstimateField.validate(3), null, 'a stored bare number is days');
  assert.equal(EstimateField.validate(null, { required: true }), 'required');
  assert.equal(EstimateField.validate('M', { required: true }), null);
});

test('edit renders three real buttons with aria-pressed and a days number input', () => {
  const { el, sizes, days, pressed } = mount({ value: 'M' });
  assert.equal(el.getAttribute('role'), 'group');
  assert.deepEqual(Object.keys(sizes), ['S', 'M', 'L']);
  for (const b of Object.values(sizes)) {
    assert.equal(b.tagName, 'BUTTON');
    assert.equal(b.type, 'button');
    assert.ok(['true', 'false'].includes(b.getAttribute('aria-pressed')));
  }
  assert.deepEqual(pressed(), ['M']);
  assert.equal(days.value, '');
  assert.equal(days.min, '1');
  assert.equal(days.closest('label').textContent.trim(), 'days', 'the number input is labelled "days"');
});

test('edit shows a days value in the number input with no size pressed', () => {
  const { days, pressed } = mount({ value: '5d' });
  assert.equal(days.value, '5');
  assert.deepEqual(pressed(), []);
});

test('choosing a size clears the days; typing days clears the size', () => {
  const { sizes, days, type, pressed, changes } = mount({ value: '5d' });
  sizes.L.click();
  assert.deepEqual(pressed(), ['L']);
  assert.equal(days.value, '');
  assert.equal(changes.at(-1), 'L');
  type('2');
  assert.deepEqual(pressed(), []);
  assert.equal(changes.at(-1), '2d');
  sizes.S.click();
  assert.deepEqual(pressed(), ['S']);
  assert.equal(days.value, '');
  assert.equal(changes.at(-1), 'S');
});

test('pressing the chosen size again clears the estimate; emptying the days clears it too', () => {
  const { sizes, type, pressed, changes } = mount({ value: 'S' });
  sizes.S.click();
  assert.deepEqual(pressed(), []);
  assert.equal(changes.at(-1), null);
  type('4');
  assert.equal(changes.at(-1), '4d');
  type('');
  assert.equal(changes.at(-1), null);
});

test('days the picker cannot accept are passed on for validate to refuse, not silently dropped', () => {
  const { type, changes } = mount({ value: null });
  for (const [typed, emitted] of [['0', '0d'], ['-1', '-1d'], ['2.5', '2.5d']]) {
    type(typed);
    assert.equal(changes.at(-1), emitted);
    assert.equal(typeof EstimateField.validate(EstimateField.coerce(emitted)), 'string');
  }
});

test('mounting never reports a change: a form that was only opened stays clean', () => {
  for (const value of ['S', '3d', null, undefined, 3, 'XL', { a: 1 }]) {
    const { changes } = mount({ value });
    assert.deepEqual(changes, [], JSON.stringify(value));
  }
});

test('a stored value of the wrong type or shape opens without throwing', () => {
  assert.equal(mount({ value: 3 }).days.value, '3');
  assert.equal(mount({ value: '0d' }).days.value, '0', 'a refused day count stays visible');
  const odd = mount({ value: 'XL' });
  assert.deepEqual(odd.pressed(), ['XL'], 'kept as a choice of its own, never as one of the sizes');
  assert.equal(odd.days.value, '');
  assert.doesNotThrow(() => mount({ value: { a: 1 } }));
  assert.doesNotThrow(() => EstimateField.read({ value: { a: 1 } }));
});

test('edit puts the id and the description on the days input and exposes it as the control', () => {
  const { el, days } = mount({ value: null, id: 'f-estimate', describedBy: 'f-estimate-err', label: 'Estimate' });
  assert.equal(days.id, 'f-estimate');
  assert.equal(days.getAttribute('aria-describedby'), 'f-estimate-err');
  assert.equal(el.control, days);
  assert.equal(el.getAttribute('aria-label'), 'Estimate');
});

test('Enter in the days input commits, Escape cancels, a size commits at once', () => {
  let committed; let cancelled = false;
  const { sizes, days, type } = mount({ value: null, onCommit: (v) => { committed = v; }, onCancel: () => { cancelled = true; } });
  type('7');
  days.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', bubbles: true }));
  assert.equal(committed, '7d');
  sizes.M.click();
  assert.equal(committed, 'M');
  days.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
  assert.equal(cancelled, true);
});

test('read shows the stored value, a placeholder when empty, and the editable affordance unless read-only', () => {
  const el = EstimateField.read({ value: 'M' });
  assert.equal(el.textContent, 'M');
  assert.ok(el.classList.contains('ef-estimate') && el.classList.contains('ef-editable'));
  assert.equal(EstimateField.read({ value: '3d' }).textContent, '3d');
  assert.equal(EstimateField.read({ value: 3 }).textContent, '3d');
  const empty = EstimateField.read({ value: null, readOnly: true });
  assert.equal(empty.textContent, '—');
  assert.ok(empty.classList.contains('ef-placeholder') && !empty.classList.contains('ef-editable'));
});

test('a stored value the picker cannot produce is shown as a pressed custom choice with a note, and can be put back', () => {
  const { el, sizes, days, pressed, changes } = mount({ value: '2 weeks' });
  const custom = el.querySelector('.ef-estimate-custom');
  assert.equal(custom.textContent, '2 weeks');
  assert.equal(custom.tagName, 'BUTTON');
  assert.equal(custom.getAttribute('aria-pressed'), 'true');
  assert.equal(days.value, '');
  assert.match(el.querySelector('.ef-estimate-note').textContent, /kept/);
  assert.deepEqual(changes, [], 'showing it is not a change');
  sizes.S.click();
  assert.deepEqual(pressed(), ['S']);
  assert.equal(changes.at(-1), 'S');
  custom.click();
  assert.deepEqual(pressed(), ['2 weeks']);
  assert.equal(changes.at(-1), '2 weeks');
});

test('a value the picker can express has no custom choice and no note', () => {
  for (const value of ['M', '3d', 3, null, '0d', '2.5d']) {
    const { el } = mount({ value });
    assert.equal(el.querySelector('.ef-estimate-custom'), null, JSON.stringify(value));
    assert.equal(el.querySelector('.ef-estimate-note'), null);
  }
});
