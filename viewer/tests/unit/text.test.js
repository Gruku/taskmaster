// User intent: text cut short with an ellipsis must never lose its words — the full text is always one hover away.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const { truncate } = await import('../../js/lib/text.js');

const CSS = readFileSync(new URL('../../css/components/rows.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
function declsOf(selector) {
  const out = {};
  for (const m of CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!m[1].split(',').map((s) => s.trim().replace(/\s+/g, ' ')).includes(selector)) continue;
    for (const d of m[2].split(';')) {
      const i = d.indexOf(':');
      if (i > 0) out[d.slice(0, i).trim()] = d.slice(i + 1).trim();
    }
  }
  return out;
}

test('a one-line cut is a span of class truncate whose title is its full text', () => {
  const el = truncate('Re-skin the Kanban cards and columns');
  assert.equal(el.tagName, 'SPAN');
  assert.equal(el.className, 'truncate');
  assert.equal(el.textContent, 'Re-skin the Kanban cards and columns');
  assert.equal(el.getAttribute('title'), 'Re-skin the Kanban cards and columns');
});

test('lines 2 and 3 add their clamp class; tag and className are the caller\'s', () => {
  const two = truncate('a', { lines: 2 });
  assert.deepEqual([...two.classList], ['truncate', 'truncate--2']);
  const three = truncate('a', { lines: 3, tag: 'p', className: 'card__title' });
  assert.equal(three.tagName, 'P');
  assert.deepEqual([...three.classList], ['truncate', 'truncate--3', 'card__title']);
  assert.equal(three.getAttribute('title'), 'a');
});

test('null or undefined is the empty string, and the title is still set', () => {
  for (const v of [null, undefined]) {
    const el = truncate(v);
    assert.equal(el.textContent, '');
    assert.equal(el.title, '');
    assert.ok(el.hasAttribute('title'));
  }
});

test('a number is its string', () => {
  const el = truncate(42);
  assert.equal(el.textContent, '42');
  assert.equal(el.title, '42');
});

test('markup in the text stays text', () => {
  const el = truncate('<b>bold</b>');
  assert.equal(el.children.length, 0);
  assert.equal(el.textContent, '<b>bold</b>');
});

test('lines other than 1, 2 or 3 are refused', () => {
  for (const lines of [0, 4, 1.5, '2', -1]) assert.throws(() => truncate('x', { lines }), RangeError, String(lines));
});

test('the stylesheet cuts one line with an ellipsis, or clamps two or three', () => {
  assert.deepEqual(declsOf('.truncate'), {
    display: 'block', 'min-width': '0', overflow: 'hidden', 'text-overflow': 'ellipsis', 'white-space': 'nowrap',
  });
  for (const n of ['2', '3']) {
    const d = declsOf(`.truncate--${n}`);
    assert.equal(d.display, '-webkit-box', n);
    assert.equal(d['-webkit-box-orient'], 'vertical', n);
    assert.equal(d['white-space'], 'normal', n);
    assert.equal(d['-webkit-line-clamp'], n, n);
  }
});
