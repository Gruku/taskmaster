// User intent: the legacy shared stylesheet and the placeholder stub stay gone — every live rule lives with its
// component, and no script reaches for a class that no longer has a style.
import test from 'node:test';
import assert from 'node:assert/strict';
import { existsSync, readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';
import { rules } from './style-rules.test.js';

const VIEWER = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const CSS_DIR = join(VIEWER, 'css');
const INDEX = readFileSync(join(VIEWER, 'index.html'), 'utf8');

const walk = (dir, ext) => readdirSync(dir).flatMap((n) => {
  const p = join(dir, n);
  return statSync(p).isDirectory() ? walk(p, ext) : p.endsWith(ext) ? [p] : [];
});
const cssFiles = walk(CSS_DIR, '.css').map((p) => ({
  rel: relative(CSS_DIR, p).replace(/\\/g, '/'), css: readFileSync(p, 'utf8'),
}));

// A selector list split on its top-level commas, so `:is(a, b)` stays one selector.
const parts = (selector) => {
  const out = [];
  let depth = 0;
  let cur = '';
  for (const ch of selector) {
    if (ch === '(') depth++;
    else if (ch === ')') depth--;
    if (ch === ',' && depth === 0) { out.push(cur.trim()); cur = ''; } else cur += ch;
  }
  return [...out, cur.trim()].filter(Boolean);
};

test('components.css and screens/_placeholders.css do not exist, and index.html links neither', () => {
  assert.equal(existsSync(join(CSS_DIR, 'components.css')), false);
  assert.equal(existsSync(join(CSS_DIR, 'screens', '_placeholders.css')), false);
  assert.doesNotMatch(INDEX, /href="css\/components\.css"/);
  assert.doesNotMatch(INDEX, /_placeholders\.css/);
});

test('no CSS file has a selector for a legacy class (.cmp-chip, .cmp-pill, .cmp-btn, .cmp-icon-btn, .tm-card, .stub)', () => {
  const legacy = /\.(cmp-chip|cmp-pill|cmp-btn|cmp-icon-btn|tm-card|stub)(?![a-z0-9])/i;
  const found = cssFiles.flatMap((f) => rules(f.css)
    .filter(({ selector }) => legacy.test(selector))
    .map(({ selector }) => `${f.rel}: ${selector}`));
  assert.deepEqual(found, []);
});

// The block's own rules — a selector that starts at the block — live in its owner. A screen placing the block
// (`.dk-continuity > .tm-empty`) or sizing it for touch in its own layout keeps that rule beside the layout.
test('the empty block and the handover pill and menu are styled only by their owning component files', () => {
  const owners = [
    { cls: /^\.tm-empty(?![a-z0-9])/, files: ['components/state.css'] },
    { cls: /^\.ho-status-(pill|menu)(?![a-z0-9])/, files: ['components/handover-status.css', 'components/popover.css'] },
  ];
  const stray = cssFiles.flatMap((f) => rules(f.css).flatMap(({ selector }) => parts(selector)
    .filter((s) => owners.some((o) => o.cls.test(s) && !o.files.includes(f.rel)))
    .map((s) => `${f.rel}: ${s}`)));
  assert.deepEqual(stray, []);
});

test('no script or index.html uses a legacy class name', () => {
  const js = walk(join(VIEWER, 'js'), '.js').filter((p) => !relative(VIEWER, p).replace(/\\/g, '/').startsWith('js/_dormant/'));
  const legacy = /(?<![\w-])(cmp-chip|cmp-pill|cmp-btn|cmp-icon-btn|tm-card|stub-meta)(?![\w-])|'stub'/;
  const found = [...js.map((p) => ({ rel: relative(VIEWER, p).replace(/\\/g, '/'), text: readFileSync(p, 'utf8') })),
    { rel: 'index.html', text: INDEX }]
    .flatMap(({ rel, text }) => text.split('\n').flatMap((line, i) => (legacy.test(line) ? [`${rel}:${i + 1}: ${line.trim()}`] : [])));
  assert.deepEqual(found, []);
});
