import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { RightRail } from '../../js/components/right-rail.js';

test('open() injects rendered content; close() removes it', () => {
  const dom = new JSDOM('<!doctype html><body></body>');
  global.document = dom.window.document;
  const rail = new RightRail({ width: 480 });
  rail.open({ render: () => '<div id="x">hi</div>' });
  assert.ok(document.querySelector('.right-rail'));
  assert.ok(document.querySelector('#x'));
  rail.close();
  assert.equal(document.querySelector('.right-rail'), null);
});

test('open() twice swaps the content', () => {
  const dom = new JSDOM('<!doctype html><body></body>');
  global.document = dom.window.document;
  const rail = new RightRail();
  rail.open({ render: () => '<div id="a">first</div>' });
  rail.open({ render: () => '<div id="b">second</div>' });
  assert.equal(document.querySelector('#a'), null);
  assert.ok(document.querySelector('#b'));
  rail.close();
});
