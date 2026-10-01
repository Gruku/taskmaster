// User intent: icons come from one inline set drawn to the design system's rules, never from emoji or Unicode glyphs.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><body></body>');
globalThis.document = dom.window.document;
const { icon, ICONS } = await import('../../js/components/icon.js');

const SVG_NS = 'http://www.w3.org/2000/svg';

test('every icon renders as a 24-box stroke svg using currentColor', () => {
  for (const name of Object.keys(ICONS)) {
    const el = icon(name);
    assert.equal(el.getAttribute('viewBox'), '0 0 24 24', name);
    assert.equal(el.getAttribute('stroke'), 'currentColor', name);
    assert.equal(el.getAttribute('stroke-width'), '2.25', name);
    assert.equal(el.getAttribute('aria-hidden'), 'true', name);
    assert.ok(el.children.length > 0, name);
    for (const child of el.children) assert.equal(child.namespaceURI, SVG_NS, `${name} <${child.localName}>`);
  }
});
test('size sets width and height; default is 20', () => {
  assert.equal(icon('search').getAttribute('width'), '20');
  const el = icon('search', { size: 16 });
  assert.equal(el.getAttribute('width'), '16');
  assert.equal(el.getAttribute('height'), '16');
});
test('labelled icon is exposed to assistive tech', () => {
  const el = icon('search', { label: 'Search' });
  assert.equal(el.getAttribute('role'), 'img');
  assert.equal(el.getAttribute('aria-label'), 'Search');
  assert.equal(el.getAttribute('aria-hidden'), null);
});
test('unknown icon throws', () => { assert.throws(() => icon('nope')); });
test('inherited object keys are not icons', () => {
  for (const n of ['constructor', 'toString', '__proto__', 'hasOwnProperty']) assert.throws(() => icon(n), /unknown icon/, n);
});
test('the 16 design-system glyphs and the 7 viewer glyphs exist', () => {
  for (const n of ['arrow','check','chevron','copy','dismiss','document','edit','external','folder','grid','minus','more','plus','polarity','search','sliders','kanban','table','alert','bug','idea','archive','menu']) assert.ok(ICONS[n], n);
  assert.equal(Object.keys(ICONS).length, 23);
});
test('no glyph carries a color literal', () => {
  for (const [name, inner] of Object.entries(ICONS)) assert.ok(!/#[0-9a-f]{3,8}\b|rgb|hsl/i.test(inner), name);
});
