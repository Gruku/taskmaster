// viewer/tests/unit/h.test.js
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.document = dom.window.document;
globalThis.window = dom.window;

const { h } = await import('../../js/util/h.js');

test('h() creates element with class and text child', () => {
  const el = h('div', { class: 'foo' }, 'hello');
  assert.equal(el.tagName, 'DIV');
  assert.equal(el.className, 'foo');
  assert.equal(el.textContent, 'hello');
});

test('h() attaches event listeners via on:{}', () => {
  let clicked = false;
  const el = h('button', { on: { click: () => { clicked = true; } } }, 'go');
  el.click();
  assert.equal(clicked, true);
});

test('h() supports html: for raw innerHTML', () => {
  const el = h('div', { html: '<span>raw</span>' });
  assert.equal(el.firstElementChild.tagName, 'SPAN');
});

test('h() filters null/false children', () => {
  const el = h('div', {}, [h('span', {}, 'a'), null, false, h('span', {}, 'b')]);
  assert.equal(el.children.length, 2);
});

test('h() applies a style key through CSSOM, never setAttribute (a strict style-src blocks the attribute)', () => {
  const calls = [];
  const original = dom.window.Element.prototype.setAttribute;
  dom.window.Element.prototype.setAttribute = function (name, value) { calls.push(name); return original.call(this, name, value); };
  try {
    const el = h('span', { style: 'width: 40%; position: fixed' });
    assert.equal(el.style.width, '40%');
    assert.equal(el.style.position, 'fixed');
    assert.deepEqual(calls, []);
  } finally {
    dom.window.Element.prototype.setAttribute = original;
  }
});
