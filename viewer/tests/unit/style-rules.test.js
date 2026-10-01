// User intent: keep the re-skin from decaying — banned patterns and off-token values fail the build, file by file.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const CSS_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'css');

// Files that must satisfy every rule. Append as files are converted; plan 4 replaces this with "all".
export const ENFORCED = [];

function walk(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : p.endsWith('.css') ? [p] : [];
  });
}
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '');

// Innermost `selector { body }` blocks, with any enclosing at-rule preludes ignored.
function rules(css) {
  const out = [];
  const stack = [];
  let buf = '';
  for (const ch of strip(css)) {
    if (ch === '{') { stack.push(buf.trim()); buf = ''; }
    else if (ch === '}') {
      const selector = stack.pop() ?? '';
      if (!buf.includes('{')) out.push({ selector, body: buf });
      buf = '';
    } else buf += ch;
  }
  return out;
}

const files = walk(CSS_DIR).map((p) => ({
  path: p, rel: relative(CSS_DIR, p).replaceAll('\\', '/'), css: readFileSync(p, 'utf8'),
}));
const tokens = files.find((f) => f.rel === 'tokens.css');
const defined = new Set([...strip(tokens.css).matchAll(/(--[a-z0-9-]+)\s*:/gi)].map((m) => m[1]));

function violations(f) {
  const v = [];
  const isTokens = f.rel === 'tokens.css';
  for (const { selector, body } of rules(f.css)) {
    const where = `${f.rel} { ${selector.slice(0, 60)} }`;
    if (/\bbox-shadow\s*:/.test(body)) v.push(`${where}: box-shadow`);
    if (/:hover/.test(selector) && /\btransform\s*:/.test(body)) v.push(`${where}: transform on hover`);
    if (/\boutline\s*:\s*(none|0)\b/.test(body)) v.push(`${where}: outline none`);
    for (const m of body.matchAll(/\bborder-left(?:-color)?\s*:\s*([^;]+)/g)) {
      const val = m[1].trim();
      const neutral = /^(0|none|transparent)\b/.test(val) || /var\(--border-[a-z]+\)/.test(val) || !/#|rgb|var\(/.test(val);
      if (!neutral) v.push(`${where}: colored border-left (${val})`);
    }
    if (!isTokens) {
      if (/#[0-9a-f]{3,8}\b/i.test(body) || /\brgba?\(/.test(body)) v.push(`${where}: color literal`);
      for (const m of body.matchAll(/\bfont-size\s*:\s*([^;]+)/g)) {
        if (!/var\(--/.test(m[1]) && !/^(inherit|1em)\s*$/.test(m[1].trim())) v.push(`${where}: raw font-size (${m[1].trim()})`);
      }
      if (/(^|[;\s])--[a-z0-9-]+\s*:/i.test(body) && !/style\s*=/.test(selector)) v.push(`${where}: custom property defined outside tokens.css`);
    }
  }
  return v;
}

test('every var(--x) used in any CSS file is defined in tokens.css', () => {
  const missing = [];
  for (const f of files) {
    for (const m of strip(f.css).matchAll(/var\(\s*(--[a-z0-9-]+)\s*(,)?/gi)) {
      // Variables set from JS as inline style are declared in tokens.css with a default.
      if (!defined.has(m[1])) missing.push(`${f.rel}: ${m[1]}`);
    }
  }
  assert.deepEqual([...new Set(missing)], []);
});

test('enforced files satisfy every style rule', () => {
  const bad = files.filter((f) => ENFORCED.includes(f.rel)).flatMap(violations);
  assert.deepEqual(bad, []);
});

test('report: violations in files not yet enforced', () => {
  const rest = files.filter((f) => !ENFORCED.includes(f.rel));
  const counts = Object.fromEntries(rest.map((f) => [f.rel, violations(f).length]).filter(([, n]) => n));
  console.log('style-rules backlog:', JSON.stringify(counts, null, 1));
});
