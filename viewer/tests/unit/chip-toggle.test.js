import test from 'node:test';
import assert from 'node:assert/strict';
import { chipClickNext, CHIP_CLICK_HINT } from '../../js/util/chip-toggle.js';

const click = {};
const shift = { shiftKey: true };

test('chipClickNext — plain click, none active → only that key', () => {
  assert.deepEqual(chipClickNext(click, [], 'a'), ['a']);
});

test('chipClickNext — plain click, only that key active → clears', () => {
  assert.deepEqual(chipClickNext(click, ['a'], 'a'), []);
});

test('chipClickNext — plain click on different key → replaces selection', () => {
  assert.deepEqual(chipClickNext(click, ['b'], 'a'), ['a']);
});

test('chipClickNext — plain click with multi-active → collapses to that key', () => {
  assert.deepEqual(chipClickNext(click, ['a', 'b', 'c'], 'a'), ['a']);
  assert.deepEqual(chipClickNext(click, ['a', 'b', 'c'], 'b'), ['b']);
});

test('chipClickNext — shift-click adds to empty pool', () => {
  assert.deepEqual(chipClickNext(shift, [], 'a'), ['a']);
});

test('chipClickNext — shift-click adds to existing pool', () => {
  const out = chipClickNext(shift, ['b'], 'a');
  assert.deepEqual(out.sort(), ['a', 'b']);
});

test('chipClickNext — shift-click toggles existing key off', () => {
  const out = chipClickNext(shift, ['a', 'b'], 'a');
  assert.deepEqual(out, ['b']);
});

test('chipClickNext — accepts a Set as `current`', () => {
  const out = chipClickNext(click, new Set(['a', 'b']), 'a');
  assert.deepEqual(out, ['a']);
});

test('chipClickNext — null event treated as plain click', () => {
  assert.deepEqual(chipClickNext(null, ['a'], 'b'), ['b']);
});

test('CHIP_CLICK_HINT — non-empty string for tooltips', () => {
  assert.equal(typeof CHIP_CLICK_HINT, 'string');
  assert.ok(CHIP_CLICK_HINT.length > 0);
});
