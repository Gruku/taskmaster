// User intent: when a list screen redraws on a poll, the user's focus lands back on the same control in the fresh DOM
// instead of dropping to <body>, and nothing outside the list is ever moved.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const { keepFocus } = await import('../../js/lib/keep-focus.js');

function setup(markup) {
  const scope = document.createElement('div');
  scope.innerHTML = markup;
  document.body.replaceChildren(scope);
  return scope;
}
const fresh = (scope, markup) => {
  const t = document.createElement('template');
  t.innerHTML = markup;
  scope.replaceChildren(t.content);
};

test('keepFocus: restores focus by data-focus onto the fresh element', () => {
  const markup = '<button data-focus="evidence:ISS-2">2</button><button data-focus="evidence:ISS-1">1</button>';
  const scope = setup(markup);
  const old = scope.querySelector('[data-focus="evidence:ISS-1"]');
  old.focus();
  const restore = keepFocus(scope);
  fresh(scope, markup);
  assert.equal(document.activeElement, document.body);
  assert.equal(restore(), true);
  const now = scope.querySelector('[data-focus="evidence:ISS-1"]');
  assert.notEqual(now, old);
  assert.equal(document.activeElement, now);
});

test('keepFocus: falls back to href when the element has no data-focus', () => {
  const markup = '<a href="#/bug/B-2">B-2</a><a href="#/bug/B-1">B-1</a>';
  const scope = setup(markup);
  scope.querySelector('a[href="#/bug/B-1"]').focus();
  const restore = keepFocus(scope);
  fresh(scope, markup);
  assert.equal(restore(), true);
  assert.equal(document.activeElement.getAttribute('href'), '#/bug/B-1');
  assert.ok(scope.contains(document.activeElement));
});

test('keepFocus: focus outside the scope → false, focus untouched', () => {
  const scope = setup('<button data-focus="x">x</button>');
  const outside = document.createElement('button');
  outside.dataset.focus = 'x';
  document.body.append(outside);
  outside.focus();
  const restore = keepFocus(scope);
  fresh(scope, '<button data-focus="x">x</button>');
  assert.equal(restore(), false);
  assert.equal(document.activeElement, outside);
});

test('keepFocus: the scope itself focused does not count', () => {
  const scope = setup('<button data-focus="x">x</button>');
  scope.tabIndex = -1;
  scope.focus();
  const restore = keepFocus(scope);
  assert.equal(restore(), false);
  assert.equal(document.activeElement, scope);
});

test('keepFocus: of several same-key elements, the one at the same position wins', () => {
  const markup = '<a href="#/bug/B-1">title</a><a href="#/bug/B-1">open</a>';
  const scope = setup(markup);
  scope.querySelectorAll('a')[1].focus();
  const restore = keepFocus(scope);
  fresh(scope, markup);
  assert.equal(restore(), true);
  assert.equal(document.activeElement, scope.querySelectorAll('a')[1]);
});

test('keepFocus: fewer same-key elements after the redraw → the first of them', () => {
  const scope = setup('<a href="#/bug/B-1">title</a><a href="#/bug/B-1">open</a>');
  scope.querySelectorAll('a')[1].focus();
  const restore = keepFocus(scope);
  fresh(scope, '<a href="#/bug/B-1">title</a>');
  assert.equal(restore(), true);
  assert.equal(document.activeElement, scope.querySelector('a'));
});

test('keepFocus: nothing matching → false, focus goes to the scope (made focusable), not <body>', () => {
  const scope = setup('<button data-focus="gone">g</button>');
  scope.querySelector('button').focus();
  const restore = keepFocus(scope);
  fresh(scope, '<button data-focus="other">o</button>');
  assert.equal(restore(), false);
  assert.equal(scope.getAttribute('tabindex'), '-1');
  assert.equal(document.activeElement, scope);
});

test('keepFocus: a scope that is already focusable keeps its own tabindex', () => {
  const scope = setup('<button data-focus="gone">g</button>');
  scope.setAttribute('tabindex', '0');
  scope.querySelector('button').focus();
  const restore = keepFocus(scope);
  fresh(scope, '');
  assert.equal(restore(), false);
  assert.equal(scope.getAttribute('tabindex'), '0');
  assert.equal(document.activeElement, scope);
});

test('keepFocus: nothing matching → false, focus goes to the fallback (element or function)', () => {
  for (const make of [(el) => el, (el) => () => el]) {
    const scope = setup('<button data-focus="gone">g</button>');
    const spare = document.createElement('button');
    document.body.append(spare);
    scope.querySelector('button').focus();
    const restore = keepFocus(scope, { fallback: make(spare) });
    fresh(scope, '');
    assert.equal(restore(), false);
    assert.equal(document.activeElement, spare);
    assert.equal(scope.hasAttribute('tabindex'), false);
  }
});

test('keepFocus: a focused element with neither key → false, focus goes to the scope once lost', () => {
  const scope = setup('<input>');
  scope.querySelector('input').focus();
  const restore = keepFocus(scope);
  fresh(scope, '<input>');
  assert.equal(restore(), false);
  assert.equal(document.activeElement, scope);
});

test('keepFocus: focus that was not lost in the redraw is left alone', () => {
  const scope = setup('<input>');
  const input = scope.querySelector('input');
  input.focus();
  const restore = keepFocus(scope);
  assert.equal(restore(), false);
  assert.equal(document.activeElement, input);
  assert.equal(scope.hasAttribute('tabindex'), false);
});
