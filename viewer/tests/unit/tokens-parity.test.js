// User intent: tokens.css must carry the design system's exact values — a drifted hex is a silent brand bug.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const rr = JSON.parse(readFileSync(join(here, '../../../docs/specs/assets/reality-reprojection-2026-10-01/tokens.json'), 'utf8'));
const css = readFileSync(join(here, '../../css/tokens.css'), 'utf8');
const block = (re) => (css.match(re) || [, ''])[1];
const dark = block(/@generated:rr-dark \*\/([\s\S]*?)\/\* @generated:end/);
const light = block(/@generated:rr-light \*\/([\s\S]*?)\/\* @generated:end/);
const cssVal = (v) => String(v).replace(/^\{(.+)\}$/, 'var(--$1)');
const has = (src, name, v) => new RegExp(`--${name}:\\s*${cssVal(v).replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*;`).test(src);

test('every RR color token is present with its dark and light value', () => {
  const miss = [];
  for (const t of rr.color.tokens) {
    const d = typeof t.value === 'string' ? t.value : t.value.dark;
    if (!has(dark, t.name, d)) miss.push(`dark ${t.name}`);
    if (typeof t.value === 'object' && t.value.light && !has(light, t.name, t.value.light)) miss.push(`light ${t.name}`);
  }
  assert.deepEqual(miss, []);
});

test('scalar families are present', () => {
  const miss = [];
  for (const fam of ['spacing', 'radius', 'typescale', 'leading', 'tracking', 'weight', 'blur', 'easing', 'duration', 'stagger']) {
    for (const t of rr[fam].tokens) if (!has(dark, t.name, t.value)) miss.push(`${fam} ${t.name}`);
  }
  assert.deepEqual(miss, []);
});

test('shadow and grain families are not imported', () => {
  for (const t of [...rr.shadow.tokens, ...rr.grain.tokens]) assert.ok(!css.includes(`--${t.name}:`), t.name);
});

test('no survivalist theme, no banned declarations', () => {
  assert.ok(!/survivalist/.test(css.replace(/\/\*[\s\S]*?\*\//g, '')));
  assert.ok(!/box-shadow/.test(css));
});
