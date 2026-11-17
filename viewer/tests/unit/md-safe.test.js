// User intent: the sandboxed /file/ page and the app run ONE sanitiser. md-safe.js, evaluated as a classic script exactly as the file page loads it, must give byte-identical output to the app's module face over the whole hostile and ordinary corpus.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';
import { HOSTILE } from './fixtures/md-corpus.js';
import { ORDINARY } from './fixtures/md-ordinary.js';

const root = join(dirname(fileURLToPath(import.meta.url)), '../..');
const marked = readFileSync(join(root, 'vendor/marked.min.js'), 'utf8');
const mdSafe = readFileSync(join(root, 'js/lib/md-safe.js'), 'utf8');

// The file page: a classic marked, then a classic md-safe.js, in a window of its own.
const classic = new JSDOM('<!doctype html><html><body></body></html>', { runScripts: 'outside-only' });
classic.window.eval(marked);
classic.window.eval(mdSafe);
const face = classic.window.TaskmasterMarkdown;

// The app: the module face, in the Node globals the other unit tests use.
const app = new JSDOM('<!doctype html><html><body></body></html>', { runScripts: 'outside-only' });
app.window.eval(marked);
globalThis.window = app.window;
globalThis.document = app.window.document;
const { renderMarkdown, isSafeUrl } = await import('../../js/components/markdown.js');

test('the classic script defines a frozen TaskmasterMarkdown and leaks no other global', () => {
  assert.ok(face && typeof face.render === 'function' && typeof face.isSafeUrl === 'function');
  assert.ok(Object.isFrozen(face));
  for (const leaked of ['sanitise', 'renderMarkdown', 'ALLOWED_TAGS', 'markTasks', 'escapeHtml']) {
    assert.equal(typeof classic.window[leaked], 'undefined', leaked);
  }
});

test('both faces give byte-identical output over every input', () => {
  const inputs = [...HOSTILE, ...ORDINARY, ...HOSTILE.map((src) => `intro\n\n${src}\n\n- item ${src}\n\n> quote ${src}`)];
  assert.ok(inputs.length > 60);
  for (const src of inputs) assert.equal(face.render(src), renderMarkdown(src), src);
});

test('the classic face renders real markdown', () => {
  assert.match(face.render('# Hi'), /<h1>Hi<\/h1>/);
});

test('without marked the classic face shows escaped text', () => {
  const bare = new JSDOM('<!doctype html><html><body></body></html>', { runScripts: 'outside-only' });
  bare.window.eval(mdSafe);
  assert.equal(bare.window.TaskmasterMarkdown.render('<b>x</b>'), '<pre class="md-fallback">&lt;b&gt;x&lt;/b&gt;</pre>');
});

test('isSafeUrl refuses javascript:, tab-disguised javascript: and data:; allows relative, fragment and https', () => {
  for (const fn of [face.isSafeUrl, isSafeUrl]) {
    for (const bad of ['javascript:alert(1)', '\tjavascript:alert(1)', 'java\tscript:x', 'data:text/html,x', 'vbscript:x']) {
      assert.equal(fn(bad), false, bad);
    }
    for (const good of ['/file/x', '#a', 'https://x', 'docs/a.md', 'mailto:a@b.c']) assert.equal(fn(good), true, good);
  }
});
