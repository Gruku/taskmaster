import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { assignEpicColors, epicColor, epicCssVar, epicIndex, epicSwatch } from '../../js/lib/epics.js';

test('epicSwatch — the epic\'s position among the epics, wrapping after 6; null when not found', () => {
  const seven = ['a', 'b', 'c', 'd', 'e', 'f', 'g'].map((id) => ({ id }));
  assert.equal(epicSwatch('b', [{ id: 'a' }, { id: 'b' }]), 2);
  assert.equal(epicSwatch('a', seven), 1);
  assert.equal(epicSwatch('g', seven), 1);
  assert.equal(epicSwatch('f', seven), 6);
  assert.equal(epicSwatch('nope', seven), null);
  assert.equal(epicSwatch(null, seven), null);
  assert.equal(epicSwatch('b', [{ name: 'no id' }, null, { id: 'a' }, { id: 'b' }]), 2, 'entries without an id do not count');
  assert.equal(epicSwatch('a', undefined), null);
});

test('epicIndex — one entry per epic with an id, in order, named by its name or else its id', () => {
  const index = epicIndex([{ id: 'a', name: 'Alpha' }, { id: 'b' }, { name: 'no id' }, { id: 'c', name: '' }]);
  assert.equal(index.size, 3);
  assert.deepEqual([...index.keys()], ['a', 'b', 'c']);
  assert.deepEqual(index.get('a'), { name: 'Alpha', swatch: 1 });
  assert.deepEqual(index.get('b'), { name: 'b', swatch: 2 });
  assert.deepEqual(index.get('c'), { name: 'c', swatch: 3 });
  assert.equal(epicIndex(undefined).size, 0);
});

test('epicIndex — the 7th epic wraps to swatch 1', () => {
  const seven = ['a', 'b', 'c', 'd', 'e', 'f', 'g'].map((id) => ({ id }));
  assert.equal(epicIndex(seven).get('g').swatch, 1);
});

test('assignEpicColors — the swatch numbers', () => {
  assert.deepEqual(assignEpicColors([{ id: 'a', name: 'Alpha' }, { id: 'b' }, { name: 'no id' }, { id: 'c', name: '' }]), { a: 1, b: 2, c: 3 });
  assert.deepEqual(assignEpicColors(null), {});
});

test('an epic\'s own color picks its swatch only when it names one of the six', () => {
  const epics = [
    { id: 'p1' }, { id: 'p2' }, { id: 'p3' }, { id: 'p4' },
    { id: 'int', color: 3 },        // position 5
    { id: 'cat', color: 'cat-3' },  // position 6
    { id: 'var', color: '--cat-5' },// position 7 → 1
    { id: 'hex', color: '#ff0000' },// position 8 → 2
    { id: 'big', color: 7 },        // position 9 → 3
  ];
  const expected = { p1: 1, p2: 2, p3: 3, p4: 4, int: 3, cat: 3, var: 5, hex: 2, big: 3 };
  for (const [id, n] of Object.entries(expected)) assert.equal(epicSwatch(id, epics), n, id);
  assert.deepEqual(assignEpicColors(epics), expected);
  const index = epicIndex(epics);
  for (const [id, n] of Object.entries(expected)) assert.equal(index.get(id).swatch, n, id);
});

test('epicColor — the mapped swatch, or null', () => {
  assert.equal(epicColor('zz', {}), null);
  assert.equal(epicColor('a', { a: 4 }), 4);
  assert.equal(epicColor(null, { a: 4 }), null);
});

test('epicCssVar — a swatch points --epic at its categorical token; anything else is empty', () => {
  assert.equal(epicCssVar(4), '--epic: var(--cat-4)');
  assert.equal(epicCssVar(null), '');
  assert.equal(epicCssVar('#6ea8ff'), '');
});

test('epics.js carries no colour literal', () => {
  const src = readFileSync(new URL('../../js/lib/epics.js', import.meta.url), 'utf8');
  assert.doesNotMatch(src, /#[0-9a-f]{3,8}\b/i);
  assert.doesNotMatch(src, /rgba?\(/);
});
