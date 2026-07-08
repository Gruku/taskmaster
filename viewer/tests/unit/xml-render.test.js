import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const { window } = new JSDOM('<!DOCTYPE html>');
globalThis.document = window.document;

import { hasKnownTags, renderInline, renderBlock, KNOWN_TAGS } from '../../js/lib/xml-render.js';

test('hasKnownTags is false for plain text', () => {
  assert.equal(hasKnownTags(''), false);
  assert.equal(hasKnownTags('plain string'), false);
  assert.equal(hasKnownTags(null), false);
  assert.equal(hasKnownTags(undefined), false);
});

test('hasKnownTags detects every recognized tag', () => {
  for (const name of Object.keys(KNOWN_TAGS)) {
    assert.equal(hasKnownTags(`pre <${name}>x</${name}> post`), true,
      `expected ${name} to be detected`);
  }
});

test('renderInline emits text + chip nodes', () => {
  const nodes = renderInline('Before <thinking>brain</thinking> after');
  assert.equal(nodes.length, 3);
  assert.equal(nodes[0].nodeType, window.Node.TEXT_NODE);
  assert.equal(nodes[0].textContent, 'Before ');
  assert.equal(nodes[1].nodeType, window.Node.ELEMENT_NODE);
  assert.equal(nodes[1].className, 'co-xtag co-xtag--th');
  assert.equal(nodes[1].textContent, 'Thinking');
  assert.equal(nodes[1].getAttribute('title'), 'brain');
  assert.equal(nodes[2].textContent, ' after');
});

test('renderInline handles multiple tags in one string', () => {
  const nodes = renderInline('<example>e</example> and <decision>d</decision>');
  const chips = nodes.filter(n => n.nodeType === window.Node.ELEMENT_NODE);
  assert.equal(chips.length, 2);
  assert.equal(chips[0].textContent, 'Example');
  assert.equal(chips[1].textContent, 'Decision');
});

test('renderInline truncates very long inner text in title attr', () => {
  const inner = 'x'.repeat(200);
  const [chip] = renderInline(`<thinking>${inner}</thinking>`);
  const title = chip.getAttribute('title');
  assert.ok(title.length <= 80, `expected title <= 80 chars, got ${title.length}`);
  assert.ok(title.endsWith('…'));
});

test('renderInline returns empty array for empty/null input', () => {
  assert.deepEqual(renderInline(''), []);
  assert.deepEqual(renderInline(null), []);
});

test('renderInline emits the whole string as plain text when no recognized tags', () => {
  const text = 'Before <unknown>x</unknown> after';
  const nodes = renderInline(text);
  assert.equal(nodes.length, 1);
  assert.equal(nodes[0].nodeType, window.Node.TEXT_NODE);
  assert.equal(nodes[0].textContent, text);
  // hasKnownTags should also report false so callers can avoid the wrap.
  assert.equal(hasKnownTags(text), false);
});

test('renderBlock builds paragraphs and tagged blocks', () => {
  const text = 'intro para\n\nsecond para\n\n<decision>capture this</decision>\n\ntrailing';
  const root = renderBlock(text);
  const paras = root.querySelectorAll('.co-xblock__p');
  const tags  = root.querySelectorAll('.co-xblock__tag');
  assert.equal(paras.length, 3); // intro, second, trailing
  assert.equal(tags.length, 1);
  assert.equal(tags[0].querySelector('.co-xblock__tag-label').textContent, 'Decision');
  assert.equal(tags[0].querySelector('.co-xblock__tag-body').textContent, 'capture this');
});

test('renderBlock returns an empty container for empty input', () => {
  const root = renderBlock('');
  assert.equal(root.children.length, 0);
});
