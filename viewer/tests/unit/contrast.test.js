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

test('accent text and text on the accent fill are AA', () => {
  assertAA([
    [BOTH, '--text-accent', ['--bg-page', '--surface-ground', '--card-bg']],
    [['dark'], '--text-accent', ['--surface-overlay']],
    [BOTH, '--on-accent-fill', ['--signature-fill']],
  ]);
});

test('modal and popover surfaces: body text and accent text are AA on them in both themes', () => {
  const surfaces = ['--overlay-surface', '--overlay-surface-sunken'];
  assertAA(['--foreground-bold', '--foreground-default', '--foreground-subtle', '--text-accent'].map((fg) => [BOTH, fg, surfaces]));
});

test('light theme: a modal is the lightest surface and its footer one step below it (no shadows to carry elevation)', () => {
  assert.ok(lum('light', '--overlay-surface') > lum('light', '--bg-page'), 'the dialog is lighter than the page');
  assert.equal(resolve('light', '--overlay-surface'), resolve('light', '--card-bg'), 'the dialog matches a card');
  assert.ok(lum('light', '--overlay-surface') > lum('light', '--overlay-surface-sunken'), 'the footer is a visible step down');
  assert.ok(lum('dark', '--overlay-surface') > lum('dark', '--overlay-surface-sunken'), 'and in dark');
});

test('button labels are AA on their fill in every state, in both themes', () => {
  // Where a transparent button can sit, and the fills its hover and pressed states paint.
  const grounds = ['--bg-page', '--card-bg', '--overlay-surface', '--overlay-surface-sunken'];
  const fills = ['--ground-15', '--ground-20', '--overlay-surface-hover', '--overlay-surface-active'];
  assertAA([
    [BOTH, '--on-accent-fill', ['--signature-fill', '--accent-fill-hover']],   // primary: rest and pressed, hover
    [BOTH, '--foreground-on-accent', ['--color-critical-bold']],               // critical: every state
    [BOTH, '--foreground-bold', [...grounds, ...fills]],                       // secondary; ghost hovered or pressed
    [BOTH, '--foreground-default', grounds],                                   // ghost at rest
  ]);
});

test('a hovered control on a modal surface is a visible step off that surface', () => {
  for (const theme of BOTH) {
    assert.notEqual(resolve(theme, '--overlay-surface-hover'), resolve(theme, '--overlay-surface'), theme);
    assert.notEqual(resolve(theme, '--overlay-surface-active'), resolve(theme, '--overlay-surface-hover'), theme);
  }
});

// Non-text marks (a status shape, a field's edge, an error mark) need 3:1 against what they sit on.
function assertNonText(pairs) {
  const bad = [];
  for (const [themes, fg, bgs] of pairs) {
    for (const theme of themes) for (const bg of bgs) {
      const r = ratio(theme, fg, bg);
      if (r < 3) bad.push(`${theme}: ${fg} on ${bg} = ${r.toFixed(2)}`);
    }
  }
  assert.deepEqual(bad, []);
}

test('status and priority shapes reach 3:1 on the page, a card and a modal, in both themes', () => {
  const grounds = ['--bg-page', '--card-bg', '--overlay-surface'];
  assertNonText(['--tone-neutral', '--tone-accent', '--tone-success', '--tone-warning', '--tone-critical', '--tone-orange']
    .map((tone) => [BOTH, tone, grounds]));
});

test('dark theme keeps the tone each marker was specified with', () => {
  for (const [role, token] of Object.entries({
    '--tone-neutral': '--foreground-subtle', '--tone-accent': '--text-accent', '--tone-success': '--color-success',
    '--tone-warning': '--color-warning', '--tone-critical': '--color-critical', '--tone-orange': '--accent-orange',
  })) assert.equal(THEMES.dark[role], `var(${token})`, role);
});

test('form fields: value and placeholder are AA on the field ground, in both themes', () => {
  assertAA([
    [BOTH, '--foreground-bold', ['--bg-recessed']],      // the typed value; code in rendered markdown
    [BOTH, '--foreground-subtle', ['--bg-recessed']],    // placeholder
    [BOTH, '--foreground-default', ['--bg-recessed']],   // an unpressed estimate size
  ]);
});

test('form fields: chips, the suggestion list and the estimate picker are AA in every state', () => {
  assertAA([
    [BOTH, '--foreground-default', ['--overlay-surface']],                               // chip; suggestion row
    [BOTH, '--foreground-bold', ['--overlay-surface-hover', '--ground-15']],             // hovered row, chip remove, estimate size
    [BOTH, '--foreground-subtle', ['--overlay-surface', '--overlay-surface-hover']],     // row hint; chip remove at rest
    [BOTH, '--on-accent-fill', ['--signature-fill', '--accent-fill-hover']],             // the pressed estimate size
  ]);
});

test('form fields: the error message is body text, and its mark and the invalid edge reach 3:1', () => {
  const grounds = ['--bg-page', '--card-bg', '--overlay-surface'];
  assertAA([[BOTH, '--foreground-bold', grounds]]);
  assertNonText([[BOTH, '--tone-critical', [...grounds, '--bg-recessed']]]);
});

test('read-mode fields: text stays AA on the hover ground of an editable value', () => {
  assertAA([
    [BOTH, '--foreground-bold', ['--ground-15', '--overlay-surface-hover']],
    [BOTH, '--foreground-default', ['--ground-15', '--overlay-surface-hover']],
    [BOTH, '--foreground-subtle', ['--ground-15', '--overlay-surface-hover']],     // the "no content" placeholder
  ]);
});

test('a field\'s edge reaches 3:1 against its own fill and every ground it sits on, in both themes', () => {
  assert.equal(resolve('light', '--bg-recessed'), resolve('light', '--overlay-surface'), 'light: the fill is the modal\'s own ground, so the edge alone shows the field');
  const grounds = ['--bg-recessed', '--bg-page', '--card-bg', '--overlay-surface'];
  assertNonText([[BOTH, '--field-border', grounds], [BOTH, '--field-border-hover', grounds]]);
});

test('dark theme: the field edge is the lowest ground step that reaches 3:1 on a modal', () => {
  assert.equal(THEMES.dark['--field-border'], 'var(--ground-50)');
  assert.ok(ratio('dark', '--ground-40', '--overlay-surface') < 3, 'one step lower falls short');
  assert.ok(ratio('dark', '--ground-40', '--card-bg') < 3);
});

test('a field\'s states read apart: hover is a stronger edge than rest, and focus and invalid are not ground steps at all', () => {
  for (const theme of BOTH) {
    const rest = ratio(theme, '--field-border', '--bg-recessed');
    const hover = ratio(theme, '--field-border-hover', '--bg-recessed');
    assert.ok(hover / rest >= 1.3, `${theme}: hover ${hover.toFixed(2)} vs rest ${rest.toFixed(2)}`);
    const edges = ['--field-border', '--field-border-hover', '--border-focus', '--tone-critical'].map((t) => resolve(theme, t));
    assert.equal(new Set(edges).size, 4, `${theme}: rest, hover, focus and invalid are four different colours`);
    // A disabled field steps back instead: its edge is deliberately quieter than a live one.
    assert.ok(ratio(theme, '--border-subtle', '--bg-recessed') < rest, theme);
  }
  assertNonText([[BOTH, '--border-focus', ['--bg-recessed', '--overlay-surface', '--card-bg', '--bg-page']]]);
});

test('rendered markdown: body, quote, link and table-head text are AA on every ground a document sits on', () => {
  const grounds = ['--bg-page', '--card-bg', '--overlay-surface'];
  assertAA([
    [BOTH, '--foreground-default', grounds],             // paragraphs, list items
    [BOTH, '--foreground-subtle', grounds],              // blockquote
    [BOTH, '--text-accent', grounds],                    // links
    [BOTH, '--foreground-bold', ['--bg-recessed']],      // code, pre, table head
  ]);
});

// The unsorted column's glyph is the only sign a header sorts; it is a shape, so 3:1 on every ground a table sits on.
test('the unsorted-column glyph reaches 3:1 on the page, a card and a modal, in both themes', () => {
  assertNonText([[BOTH, '--foreground-subtle', ['--bg-page', '--card-bg', '--overlay-surface']]]);
});
