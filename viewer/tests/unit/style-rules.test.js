// User intent: keep the re-skin from decaying — banned patterns and off-token values fail the build, file by file.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const CSS_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'css');

// Files that must satisfy every rule. Append as files are converted; plan 4 replaces this with "all".
export const ENFORCED = ['shell.css', 'screens/desk.css', 'components/modal.css', 'components/button.css', 'components/status.css',
  'components/edit-fields.css', 'components/markdown.css', 'components/entity-modal.css',
  'screens/task-detail.css', 'components/state.css', 'components/handover-status.css',
  'components/rows.css', 'components/popover.css',
  'components/conflict-banner.css',
  'components/chips.css',
  'components/overflow-row.css',
  'components/toolbar.css',
  'components/right-rail.css',
  'components/column-tabs.css',
  'components/list-toolbar.css',
  'screens/table.css',
  'components/issue-card.css',
  'screens/epics.css',
  'screens/bugs.css',
  'screens/continuity.css'];

function walk(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : p.endsWith('.css') ? [p] : [];
  });
}
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '');

// Every block that carries declarations, as `{ selector, body }`. Native nesting is parsed rather than
// refused: a block's body is its own declarations on both sides of any child block, and its selector is
// the chain of enclosing style-rule selectors, so `.a:hover { .b { … } }` is still a hover rule.
// Grouping at-rules (@media, @supports, @layer) add nothing to the selector; a declaration block with no
// style selector around it (@font-face) is reported under its at-rule name.
// Not handled: `{`, `}` or `;` inside a quoted string.
export function rules(css) {
  const out = [];
  const stack = [];
  let buf = '';
  for (const ch of strip(css)) {
    if (ch === '{') {
      const cut = buf.lastIndexOf(';') + 1;
      if (stack.length) stack.at(-1).body += buf.slice(0, cut);
      stack.push({ prelude: buf.slice(cut).trim(), body: '' });
      buf = '';
    } else if (ch === '}') {
      const frame = stack.pop();
      if (!frame) { buf = ''; continue; }
      frame.body += buf;
      buf = '';
      if (!frame.body.trim()) continue;
      const chain = [...stack, frame].map((f) => f.prelude);
      const selector = chain.filter((p) => !p.startsWith('@')).join(' ') || frame.prelude;
      out.push({ selector, body: frame.body, at: chain.filter((p) => p.startsWith('@')) });
    } else buf += ch;
  }
  return out;
}

// `prop: value` pairs of a rule body; `;` inside parentheses (data URLs) does not split.
function decls(body) {
  const out = [];
  let depth = 0;
  let cur = '';
  for (const ch of body + ';') {
    if (ch === '(') depth++;
    else if (ch === ')') depth--;
    if (ch === ';' && depth <= 0) {
      const i = cur.indexOf(':');
      if (i > 0) out.push({ prop: cur.slice(0, i).trim().toLowerCase(), value: cur.slice(i + 1).trim() });
      cur = '';
      depth = 0;
    } else cur += ch;
  }
  return out;
}

const words = (value) => value.replace(/!important/gi, '').match(/[^\s(]+(?:\([^)]*\))?/g) ?? [];

const HOVER_MOTION = new Set(['transform', 'translate', 'scale', 'rotate']);
const LEFT_BORDER = new Set(['border-left', 'border-left-color', 'border-inline-start', 'border-inline-start-color']);
// A left rail can also be drawn with `border-width: 0 0 0 3px` plus `border-color`, or a four-value
// `border-color`. Those are not checked: the two halves usually sit in different rules, and a uniform
// `border` shorthand is a legitimate full-perimeter border, so any check here would misfire.
const BORDER_NEUTRAL = /^(0[a-z%]*|[\d.]+[a-z%]+|thin|medium|thick|none|hidden|solid|dashed|dotted|double|groove|ridge|inset|outset|transparent|inherit|initial|unset|revert|var\(--border-[a-z0-9-]+\))$/i;
const COLOR_FN = /\b(rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(/i;
const COLOR_NAME = /(?<![\w-])(white|black|red|green|blue|gr[ae]y|orange|yellow|purple|pink)(?![\w-])/i;
// What is left of a value once token references and url(...) are taken out — the only place a literal can hide.
const bare = (value) => value.replace(/url\([^)]*\)/gi, '').replace(/var\(\s*--[a-z0-9-]+/gi, 'var(');
const hasColorLiteral = (value) => {
  const v = bare(value);
  return /#[0-9a-f]{3,8}\b/i.test(v) || COLOR_FN.test(v) || COLOR_NAME.test(v);
};

export function violations(f) {
  const v = [];
  const isTokens = f.rel === 'tokens.css';
  // tokens.css is the one place literals belong, but only where the generator put them.
  const css = isTokens ? f.css.replace(/\/\* @generated:[a-z-]+ \*\/[\s\S]*?\/\* @generated:end \*\//g, '') : f.css;
  for (const { selector, body, at } of rules(css)) {
    if (isTokens && at.some((p) => p.startsWith('@font-face'))) continue;
    const where = `${f.rel} { ${selector.slice(0, 60)} }`;
    const hover = /:hover/.test(selector.replace(/:not\([^()]*\)/g, ''));
    for (const { prop, value } of decls(body)) {
      const w = words(value);
      if (prop === 'box-shadow') v.push(`${where}: box-shadow`);
      if (hover && HOVER_MOTION.has(prop)) v.push(`${where}: transform on hover (${prop})`);
      if ((prop === 'outline' && w.some((x) => /^(none|0|0px)$/i.test(x)))
        || (prop === 'outline-style' && /^none\b/i.test(value))
        || (prop === 'outline-width' && /^0(px)?\b/i.test(value))) v.push(`${where}: outline none`);
      // Strict on purpose: anything in a left border that is not a width, a style or var(--border-*) counts as a color.
      if (LEFT_BORDER.has(prop) && !w.every((x) => BORDER_NEUTRAL.test(x))) v.push(`${where}: colored border-left (${value})`);
      if ((prop === 'font-style' || prop === 'font') && w.some((x) => /^(italic|oblique)$/i.test(x))) v.push(`${where}: italic`);
      if (/var\(\s*--size-section-label\b/.test(value)) v.push(`${where}: section-label size (10px, below the 11px floor)`);
      if (hasColorLiteral(value)) v.push(`${where}: color literal (${prop})`);
      // The signature hue fails AA as text on raised surfaces; --text-accent is the one token allowed to carry it.
      if (prop === 'color' && /var\(\s*--signature\b/.test(value)) v.push(`${where}: signature hue as text (use --text-accent)`);
      if (!isTokens) {
        if (prop === 'font-size' && !/var\(--/.test(value) && !/^(inherit|1em)$/.test(value)) v.push(`${where}: raw font-size (${value})`);
        if (prop.startsWith('--') && !/style\s*=/.test(selector)) v.push(`${where}: custom property defined outside tokens.css`);
      }
    }
  }
  return v;
}

const files = walk(CSS_DIR).map((p) => ({
  path: p, rel: relative(CSS_DIR, p).replaceAll('\\', '/'), css: readFileSync(p, 'utf8'),
}));
const tokens = files.find((f) => f.rel === 'tokens.css');
const defined = new Set([...strip(tokens.css).matchAll(/(--[a-z0-9-]+)\s*:/gi)].map((m) => m[1]));

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
  assert.deepEqual(ENFORCED.filter((rel) => !files.some((f) => f.rel === rel)), [], 'every enforced file exists');
  const bad = files.filter((f) => ENFORCED.includes(f.rel)).flatMap(violations);
  assert.deepEqual(bad, []);
});

test('tokens.css hand-written blocks satisfy the style rules (literals live only in generated blocks)', () => {
  assert.deepEqual(violations(tokens), []);
});

test('report: violations in files not yet enforced', () => {
  const rest = files.filter((f) => !ENFORCED.includes(f.rel) && f.rel !== 'tokens.css');
  const counts = Object.fromEntries(rest.map((f) => [f.rel, violations(f).length]).filter(([, n]) => n));
  console.log('style-rules backlog:', JSON.stringify(counts, null, 1));
});

// ── Self-tests: the ratchet is only worth trusting if each rule is shown to bite and to spare its neighbours. ──
const kinds = (css, rel = 'screens/x.css') => violations({ rel, css }).map((v) => v.slice(v.lastIndexOf(' }: ') + 4));
const caught = (css, kind, rel) => assert.ok(kinds(css, rel).some((k) => k.startsWith(kind)), `expected "${kind}" for: ${css}\n got: ${JSON.stringify(kinds(css, rel))}`);
const clean = (css, rel) => assert.deepEqual(kinds(css, rel), [], css);

test('selftest: hover motion', () => {
  for (const p of ['transform: translateY(-1px)', 'translate: 0 -1px', 'scale: 1.02', 'rotate: 2deg']) {
    caught(`.a:hover { ${p}; }`, 'transform on hover');
  }
  caught('.a:hover .b { transform: none; }', 'transform on hover');
  caught('.a:not(:hover):hover { scale: 1.1; }', 'transform on hover');
  clean('.a:not(:hover) { transform: rotate(2deg); }');
  clean('.a:hover { text-transform: uppercase; transition: transform var(--dur-micro); }');
  clean('.a { transform: rotate(2deg); }');
});

test('selftest: colored left border', () => {
  for (const d of [
    'border-left: 3px solid red', 'border-left: 3px solid #5e79e6', 'border-left: 3px solid rgb(1, 2, 3)',
    'border-left: 3px solid hsl(210 50% 50%)', 'border-left-color: oklch(70% 0.1 200)', 'border-left: 2px solid currentColor',
    'border-left: 3px solid var(--signature)', 'border-left-color: var(--color-critical)',
    'border-inline-start: 3px solid var(--accent-pink)', 'border-inline-start-color: tomato',
  ]) caught(`.a { ${d}; }`, 'colored border-left');
  for (const d of [
    'border-left: 1px solid var(--border-subtle)', 'border-left-color: var(--border-default)', 'border-left: 0', 'border-left: none',
    'border-left: 2px dashed transparent', 'border-left: 1px solid', 'border-inline-start: 1px solid var(--border-strong)',
    'border-left-width: 3px', 'border-left-style: solid',
  ]) clean(`.a { ${d}; }`);
});

test('selftest: outline removal', () => {
  for (const d of ['outline: none', 'outline: 0', 'outline: 0px', 'outline: 0 none', 'outline-style: none', 'outline-width: 0', 'outline-width: 0px']) {
    caught(`.a:focus { ${d}; }`, 'outline none');
  }
  clean('.a:focus-visible { outline: 2px solid var(--border-focus); outline-offset: 2px; }');
  clean('.a { outline-offset: 0; }');
});

test('selftest: color literals', () => {
  for (const d of [
    'color: #fff', 'color: #5e79e6', 'background: rgba(0, 0, 0, 0.4)', 'color: rgb(1 2 3)', 'color: hsl(210 50% 50%)', 'color: oklch(70% 0.1 200)',
    'color: white', 'background: black', 'border: 1px solid red', 'color: green', 'color: blue', 'color: gray', 'color: grey',
    'color: orange', 'color: yellow', 'color: purple', 'color: pink', 'background: color-mix(in srgb, var(--paper) 94%, white)',
    'background: url(x.svg) #fff',
  ]) caught(`.a { ${d}; }`, 'color literal');
  for (const d of [
    'color: var(--accent-pink)', 'background: var(--pastel-orange-subtle)', 'fill: url(#fade)', 'mask: url(#abc123)',
    'background: url(img/white-black.png)', 'white-space: nowrap', 'color: transparent', 'color: currentColor', 'color: inherit',
    'background: color-mix(in oklch, var(--cat-1) 14%, transparent)',
  ]) clean(`.a { ${d}; }`);
});

test('selftest: box-shadow, font-size, italic, section label, stray custom property', () => {
  caught('.a { box-shadow: 0 1px 2px var(--border-subtle); }', 'box-shadow');
  caught('.a { font-size: 13px; }', 'raw font-size');
  clean('.a { font-size: var(--size-narrator-small); line-height: inherit; }');
  clean('.a { font-size: inherit; }');
  caught('.a { font-style: italic; }', 'italic');
  caught('.a { font: italic 14px var(--font-narrator); }', 'italic');
  clean('.a { font-style: normal; }');
  caught('.a { font-size: var(--size-section-label); }', 'section-label size');
  clean('.a { font-size: var(--size-technical-label); }');
  caught('.a { --local: 4px; }', 'custom property defined outside tokens.css');
  clean('.a { padding: var(--space-md); }');
});

test('selftest: signature hue as text', () => {
  for (const t of ['--signature', '--signature-text', '--signature-vivid', '--signature-fill']) {
    caught(`.a { color: var(${t}); }`, 'signature hue as text');
  }
  clean('.a { color: var(--text-accent); background: var(--signature-fill); border-color: var(--signature-dim); }');
  clean('.a { color: var(--on-accent-fill); outline: 2px solid var(--signature); }');
});

test('selftest: at-rules and nesting', () => {
  caught('@media (max-width: 768px) { .a { color: #fff; } }', 'color literal');
  caught('@supports (display: grid) { @media (min-width: 1px) { .a:hover { scale: 1.1; } } }', 'transform on hover');
  clean('@media (max-width: 768px) { .a { color: var(--foreground-default); } }');
  clean('@keyframes rise { from { transform: translateY(16px); } to { transform: none; } }');
  // Native nesting: the parent's own declarations on either side of a nested rule are checked…
  caught('.a { color: #fff; .b { color: var(--foreground-bold); } }', 'color literal');
  caught('.a { .b { color: var(--foreground-bold); } box-shadow: none; }', 'box-shadow');
  // …and a nested rule inherits the parent's selector, so hover state carries down.
  caught('.a:hover { .b { transform: scale(1.1); } }', 'transform on hover');
  caught('.a { &:hover { translate: 0 -1px; } }', 'transform on hover');
  caught('.a { @media (max-width: 768px) { color: red; } }', 'color literal');
  clean('.a { color: var(--foreground-bold); .b { padding: 0; } }');
  assert.deepEqual(rules('.a { x: 1; .b { y: 2; } z: 3; }').map((r) => [r.selector, r.body.replace(/\s+/g, ' ').trim()]),
    [['.a .b', 'y: 2;'], ['.a', 'x: 1; z: 3;']]);
});

test('selftest: functional pseudo-classes in a selector', () => {
  // :where()/:is()/:not() carry parentheses and brackets; the rule under them is still found and checked.
  assert.deepEqual(rules(':where(a[href]) { color: var(--text-accent); }').map((r) => [r.selector, r.body.trim()]),
    [[':where(a[href])', 'color: var(--text-accent);']]);
  clean(':where(a[href]) { color: var(--text-accent); }');
  caught(':where(a[href]) { color: #5e79e6; }', 'color literal');
  caught(':where(.a:hover) { transform: scale(1.1); }', 'transform on hover');
  caught(':is(.a, .b):hover { translate: 0 -1px; }', 'transform on hover');
  caught(':where(.a) { box-shadow: none; }', 'box-shadow');
});

test('selftest: tokens.css is checked for literals outside generated and @font-face blocks', () => {
  const t = (body) => `@font-face { font-family: 'X'; src: url('x.woff2#abc'); }\n:root {\n  /* @generated:rr-dark */\n  --signature: #5e79e6;\n  /* @generated:end */\n${body}\n}`;
  clean(t('  --card-bg: var(--surface-raised); --tilt: 0deg;'), 'tokens.css');
  caught(t('  --paper: #e8d98a;'), 'color literal', 'tokens.css');
  caught(t('  --scrim: rgba(0, 0, 0, 0.5);'), 'color literal', 'tokens.css');
  caught(t('  --ink: white;'), 'color literal', 'tokens.css');
});

// handover-status.css owns the pill; the legacy block left behind must not still brighten it on hover or push its
// neighbour away (M-8).
test('no legacy rule outside handover-status.css filters the handover pill or gives it a margin', () => {
  const stray = files.filter((f) => f.rel !== 'components/handover-status.css').flatMap((f) => rules(f.css)
    .filter(({ selector }) => /\.ho-status-pill(?![\w-])/.test(selector))
    .filter(({ body }) => /(^|;)\s*(filter|margin(-right|-left)?)\s*:/.test(body))
    .map(({ selector }) => `${f.rel}: ${selector}`));
  assert.deepEqual(stray, []);
});
