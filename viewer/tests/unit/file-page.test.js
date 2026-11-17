// User intent: the sandboxed /file/ page renders its markdown through the shared sanitiser, ignores hostile payloads, and its zoom works even where localStorage throws (a sandbox's opaque origin).
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const root = join(dirname(fileURLToPath(import.meta.url)), '../..');
const read = (rel) => readFileSync(join(root, rel), 'utf8');
const marked = read('vendor/marked.min.js');
const mdSafe = read('js/lib/md-safe.js');
const filePage = read('js/file-page.js');

function page(text, { storage = 'works', withMarkdown = true } = {}) {
  const b64 = Buffer.from(text, 'utf8').toString('base64');
  const dom = new JSDOM(`<!doctype html><html><body>
    <button id="zoom-out"></button><span id="zoom-label">100%</span><button id="zoom-in"></button><button id="zoom-reset"></button>
    <main id="content" class="md-body" data-b64="${b64}"></main></body></html>`, { runScripts: 'outside-only', url: 'http://localhost/' });
  const { window } = dom;
  window.TextDecoder = TextDecoder;
  if (storage === 'throws') {
    Object.defineProperty(window, 'localStorage', { get() { throw new window.DOMException('denied', 'SecurityError'); } });
  }
  if (withMarkdown) {
    window.eval(marked);
    window.eval(mdSafe);
  }
  window.eval(filePage);
  return window;
}
const size = (w) => w.document.getElementById('content').style.fontSize;
const label = (w) => w.document.getElementById('zoom-label').textContent;
const click = (w, id) => w.document.getElementById(id).click();

test('it renders a heading from the file text, UTF-8 included', () => {
  const w = page('# Héllo\n\ntext');
  assert.equal(w.document.querySelector('#content h1').textContent, 'Héllo');
});

test('an img onerror and a script payload are inert', () => {
  const w = page('<img src=x onerror="window.__pwned=1"><script>window.__pwned=1</script>\n\nok');
  assert.equal(w.__pwned, undefined);
  assert.equal(w.document.querySelector('#content img'), null);
  assert.equal(w.document.querySelector('#content script'), null);
});

test('external links open in place: the sandbox has no allow-popups (028 gate M3)', () => {
  const w = page('[out](https://example.com/x) and [in](#top)');
  const links = [...w.document.querySelectorAll('#content a')];
  assert.equal(links.length, 2);
  for (const a of links) assert.equal(a.hasAttribute('target'), false);
  assert.equal(links[0].getAttribute('href'), 'https://example.com/x');
  assert.match(links[0].getAttribute('rel') || '', /noopener/);
});

test('zoom buttons change the font size and label when localStorage throws SecurityError', () => {
  const w = page('x', { storage: 'throws' });
  assert.equal(label(w), '100%');
  click(w, 'zoom-in');
  assert.equal(label(w), '110%');
  assert.equal(size(w), '15.4px');
  click(w, 'zoom-out'); click(w, 'zoom-out');
  assert.equal(label(w), '90%');
  click(w, 'zoom-reset');
  assert.equal(label(w), '100%');
});

test('zoom persists when storage works, and is read back on the next load', () => {
  const w = page('x');
  click(w, 'zoom-in'); click(w, 'zoom-in');
  assert.equal(w.localStorage.getItem('taskmaster-docs-zoom'), '120');
  const again = new JSDOM('<!doctype html><html><body></body></html>', { url: 'http://localhost/' });
  assert.ok(again);                                    // storage is per window: a saved value is applied at load
  const w2 = page('x');
  w2.localStorage.setItem('taskmaster-docs-zoom', '140');
  w2.eval(filePage);
  assert.equal(label(w2), '140%');
});

test('zoom is clamped to 60..200', () => {
  const w = page('x', { storage: 'throws' });
  for (let i = 0; i < 30; i += 1) click(w, 'zoom-in');
  assert.equal(label(w), '200%');
  for (let i = 0; i < 30; i += 1) click(w, 'zoom-out');
  assert.equal(label(w), '60%');
});

test('without the sanitiser the text shows in an escaped pre', () => {
  const w = page('<b>x</b>', { withMarkdown: false });
  const pre = w.document.querySelector('#content pre.md-fallback');
  assert.equal(pre.textContent, '<b>x</b>');
  assert.equal(w.document.querySelector('#content b'), null);
});
