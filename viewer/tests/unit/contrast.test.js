// User intent: text must stay readable in both themes — contrast is computed from the token values themselves,
// so a token change that drops a pair below WCAG AA fails here instead of in front of the user.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const css = readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../../css/tokens.css'), 'utf8')
  .replace(/\/\*[\s\S]*?\*\//g, '');
const declsOf = (re) => Object.fromEntries(
  [...(css.match(re)?.[1] ?? '').matchAll(/(--[a-z0-9-]+)\s*:\s*([^;]+);/gi)].map((m) => [m[1], m[2].trim()]));
const root = declsOf(/:root\s*\{([^}]*)\}/);
const THEMES = { dark: root, light: { ...root, ...declsOf(/\[data-theme="light"\]\s*\{([^}]*)\}/) } };

// A token's value in a theme, with var() aliases followed to the literal.
export function resolve(theme, name, seen = []) {
  const v = THEMES[theme][name];
  assert.ok(v !== undefined, `${name} is not defined (${theme})`);
  assert.ok(!seen.includes(name), `alias cycle at ${name}`);
  const alias = v.match(/^var\(\s*(--[a-z0-9-]+)\s*\)$/i);
  return alias ? resolve(theme, alias[1], [...seen, name]) : v;
}

function luminance(hex) {
  assert.match(hex, /^#[0-9a-f]{6}$/i, `opaque #rrggbb expected, got ${hex}`);
  const [r, g, b] = [1, 3, 5].map((i) => parseInt(hex.slice(i, i + 2), 16) / 255)
    .map((c) => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}
const lum = (theme, name) => luminance(resolve(theme, name));
export function ratio(theme, fg, bg) {
  const [a, b] = [lum(theme, fg), lum(theme, bg)];
  return (Math.max(a, b) + 0.05) / (Math.min(a, b) + 0.05);
}

// Every failing pair is listed with its ratio, so one run shows the whole picture.
function assertAA(pairs) {
  const bad = [];
  for (const [themes, fg, bgs] of pairs) {
    for (const theme of themes) for (const bg of bgs) {
      const r = ratio(theme, fg, bg);
      if (r < 4.5) bad.push(`${theme}: ${fg} on ${bg} = ${r.toFixed(2)}`);
    }
  }
  assert.deepEqual(bad, []);
}
const BOTH = ['dark', 'light'];

test('selftest: the contrast maths and the per-theme alias resolution', () => {
  assert.equal(resolve('dark', '--foreground-bold'), '#f5f3ed');
  assert.equal(resolve('light', '--foreground-bold'), '#0d0d0c');
  assert.equal(ratio('dark', '--ground-100', '--ground-100'), 1);
  assert.ok(Math.abs(luminance('#ffffff') - 1) < 1e-9 && luminance('#000000') === 0);
  assert.ok(Math.abs(ratio('dark', '--ground-100', '--ground-0') - 17.52) < 0.01);
});

test('body text is AA on every surface it sits on, in both themes', () => {
  const surfaces = ['--bg-page', '--card-bg', '--card-bg-hover', '--surface-ground', '--col-bg'];
  assertAA(['--foreground-bold', '--foreground-default', '--foreground-subtle'].map((fg) => [BOTH, fg, surfaces]));
});

test('light theme: cards are the lightest surface, columns the darkest (no shadows to carry elevation)', () => {
  assert.ok(lum('light', '--card-bg') > lum('light', '--bg-page'), 'card is lighter than the page');
  assert.ok(lum('light', '--bg-page') > lum('light', '--col-bg'), 'column is darker than the page');
  assert.ok(lum('light', '--card-bg') > lum('light', '--card-bg-hover'), 'hover is a visible step');
  assert.ok(lum('light', '--card-bg-hover') > lum('light', '--col-bg'), 'a hovered card still stands off its column');
});

test('sticky notes: both inks are AA on both papers, and the paper is the same in both themes', () => {
  const papers = ['--note-paper-user', '--note-paper-claude'];
  assertAA([[BOTH, '--note-ink', papers], [BOTH, '--note-ink-soft', papers]]);
  for (const t of [...papers, '--note-ink', '--note-ink-soft', '--note-edge-user', '--note-edge-claude']) {
    assert.equal(resolve('dark', t), resolve('light', t), `${t} is theme-independent`);
  }
});
