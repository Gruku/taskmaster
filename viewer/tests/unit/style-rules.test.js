// User intent: keep the re-skin from decaying — banned patterns and off-token values fail the build, in every stylesheet.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, relative, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const CSS_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'css');

function walk(dir) {
  return readdirSync(dir).flatMap((n) => {
    const p = join(dir, n);
    return statSync(p).isDirectory() ? walk(p) : p.endsWith('.css') ? [p] : [];
  });
}
const strip = (css) => css.replace(/\/\*[\s\S]*?\*\//g, '');

// Comments out, and every quoted string's contents replaced by `x` (an empty string stays empty): a `{`, `}` or `;`
// in `content: "…"` cannot break the parse, and a quoted name (`'Snow Sans'`, `content: "red"`) is never a colour.
function scrub(css) {
  let out = '';
  for (let i = 0; i < css.length; i++) {
    const ch = css[i];
    if (ch === '/' && css[i + 1] === '*') {
      const end = css.indexOf('*/', i + 2);
      i = end < 0 ? css.length : end + 1;
    } else if (ch === '"' || ch === "'") {
      let j = i + 1;
      while (j < css.length && css[j] !== ch && css[j] !== '\n') j += css[j] === '\\' ? 2 : 1;
      out += j > i + 1 ? `${ch}x${ch}` : ch + ch;
      i = j;
    } else out += ch;
  }
  return out;
}

// `text` cut at each top-level `sep` character (never inside parentheses or brackets); pieces trimmed, empties dropped.
function splitTop(text, sep) {
  const out = [];
  let depth = 0;
  let cur = '';
  for (const ch of text) {
    if (ch === '(' || ch === '[') depth++;
    else if (ch === ')' || ch === ']') depth--;
    if (depth <= 0 && sep.test(ch)) {
      out.push(cur);
      cur = '';
    } else cur += ch;
  }
  out.push(cur);
  return out.map((s) => s.trim()).filter(Boolean);
}
// A selector list as its selectors; the commas inside `:is()`/`:where()`/`:not()` do not split.
export const selectors = (selector) => splitTop(selector, /,/);

// Every block that carries declarations, as `{ selector, body }`. Native nesting is parsed rather than
// refused: a block's body is its own declarations on both sides of any child block, and its selector is
// the chain of enclosing style-rule selectors, so `.a:hover { .b { … } }` is still a hover rule (a parent
// that is a list is wrapped in `:is()`, so the chain is still one selector per child selector).
// Grouping at-rules (@media, @supports, @layer) add nothing to the selector; a declaration block with no
// style selector around it (@font-face) is reported under its at-rule name. Quoted strings are scrubbed first.
export function rules(css) {
  const out = [];
  const stack = [];
  let buf = '';
  for (const ch of scrub(css)) {
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
      const style = chain.filter((p) => !p.startsWith('@'));
      const selector = style.map((p, i) => (i < style.length - 1 && selectors(p).length > 1 ? `:is(${p})` : p)).join(' ')
        || frame.prelude;
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
const BORDER_NEUTRAL = /^(0[a-z%]*|[\d.]+[a-z%]+|thin|medium|thick|none|hidden|solid|dashed|dotted|double|groove|ridge|inset|outset|transparent|inherit|initial|unset|revert|var\(--border-[a-z0-9-]+\))$/i;
// What a rail may be painted with and still be a structural line rather than a coloured accent.
const NEUTRAL_PAINT = /^(transparent|var\(--border-[a-z0-9-]+\))$/i;
const COLOR_FN = /\b(rgba?|hsla?|hwb|lab|lch|oklab|oklch)\(/i;
// CSS Color 4 named colours.
const COLOR_NAMES = new Set(`aliceblue antiquewhite aqua aquamarine azure beige bisque black blanchedalmond blue blueviolet
  brown burlywood cadetblue chartreuse chocolate coral cornflowerblue cornsilk crimson cyan darkblue darkcyan darkgoldenrod
  darkgray darkgreen darkgrey darkkhaki darkmagenta darkolivegreen darkorange darkorchid darkred darksalmon darkseagreen
  darkslateblue darkslategray darkslategrey darkturquoise darkviolet deeppink deepskyblue dimgray dimgrey dodgerblue firebrick
  floralwhite forestgreen fuchsia gainsboro ghostwhite gold goldenrod gray green greenyellow grey honeydew hotpink indianred
  indigo ivory khaki lavender lavenderblush lawngreen lemonchiffon lightblue lightcoral lightcyan lightgoldenrodyellow lightgray
  lightgreen lightgrey lightpink lightsalmon lightseagreen lightskyblue lightslategray lightslategrey lightsteelblue lightyellow
  lime limegreen linen magenta maroon mediumaquamarine mediumblue mediumorchid mediumpurple mediumseagreen mediumslateblue
  mediumspringgreen mediumturquoise mediumvioletred midnightblue mintcream mistyrose moccasin navajowhite navy oldlace olive
  olivedrab orange orangered orchid palegoldenrod palegreen paleturquoise palevioletred papayawhip peachpuff peru pink plum
  powderblue purple rebeccapurple red rosybrown royalblue saddlebrown salmon sandybrown seagreen seashell sienna silver skyblue
  slateblue slategray slategrey snow springgreen steelblue tan teal thistle tomato turquoise violet wheat white whitesmoke
  yellow yellowgreen`.split(/\s+/));
// What is left of a value once token references and url(...) are taken out — the only place a literal can hide.
const bare = (value) => value.replace(/url\([^)]*\)/gi, '').replace(/var\(\s*--[a-z0-9-]+/gi, 'var(');
const hasColorLiteral = (value) => {
  const v = bare(value);
  return /#[0-9a-f]{3,8}\b/i.test(v) || COLOR_FN.test(v)
    || [...v.matchAll(/(?<![\w-])[a-z]+(?![\w(-])/gi)].some((m) => COLOR_NAMES.has(m[0].toLowerCase()));
};

const RADIUS_PROP = /^border(-[a-z]+)*-radius$/;
const RADIUS_WORD = /^(0|\/|inherit|initial|unset|revert|var\(--radius-(xs|sm|md|lg|xl|full)\))$/;
const SIZE_PROP = new Set(['height', 'min-height', 'width', 'min-width', 'block-size', 'min-block-size', 'inline-size', 'min-inline-size']);
const PSEUDO = /::?(before|after)\b/i;
const isZero = (w) => /^0(px)?$/.test(w ?? '');
// A rail's width: 1px–4px, or the 4px space step.
const thin = (w) => w === 'var(--space-micro)' || (/^[\d.]+px$/.test(w) && parseFloat(w) > 0 && parseFloat(w) <= 4);
// The four sides (top right bottom left) a one- to four-value box shorthand sets.
const sides = (w) => [w[0], w[1] ?? w[0], w[2] ?? w[0], w[3] ?? w[1] ?? w[0]];
const BORDER_STYLE = /^(none|hidden|solid|dashed|dotted|double|groove|ridge|inset|outset)$/i;
const NAMED_WIDTH = { thin: 1, medium: 3, thick: 5 };
const borderWidth = (w) => (w in NAMED_WIDTH ? NAMED_WIDTH[w] : /^[\d.]+(px)?$/.test(w) ? parseFloat(w) : null);
const isWidth = (w) => borderWidth(w) !== null || /^[\d.]+[a-z]+$/i.test(w);

// The left inset a pseudo-element's declarations end on (the last one wins).
function leftOf(ds) {
  let left = null;
  for (const { prop, value } of ds) {
    const w = words(value);
    if (prop === 'left' || prop === 'inset-inline-start') left = w[0];
    else if (prop === 'inset-inline') left = w[0];
    else if (prop === 'inset') left = sides(w)[3];
  }
  return left;
}
const last = (ds, props) => ds.filter((d) => props.includes(d.prop)).at(-1)?.value.replace(/!important/gi, '').trim();

// A horizontal linear-gradient with a non-neutral colour stop at 4px or less is a left rail painted as a background.
function gradientRail(value) {
  for (const m of value.matchAll(/linear-gradient\(/gi)) {
    let depth = 1;
    let i = m.index + m[0].length;
    const start = i;
    while (i < value.length && depth) {
      if (value[i] === '(') depth++;
      else if (value[i] === ')') depth--;
      i++;
    }
    const [dir = '', ...stops] = splitTop(value.slice(start, i - 1), /,/);
    if (!/^(to right|90deg)$/i.test(dir.replace(/\s+/g, ' '))) continue;
    for (const stop of stops) {
      const parts = splitTop(stop, /\s/);
      const colour = parts.filter((p) => !/^(-?[\d.]+[a-z%]*|var\(--space-[a-z0-9-]+\)|calc\(.*\))$/i.test(p)).join(' ');
      if (parts.some(thin) && !NEUTRAL_PAINT.test(colour)) return true;
    }
  }
  return false;
}

// A rule that makes its left border the only or the widest side, in a colour that is not a structural line.
function oneSidedBorder(ds) {
  const width = [null, null, null, null];
  let colour = null;
  const SIDE = { top: 0, right: 1, bottom: 2, left: 3, 'inline-start': 3 };
  for (const { prop, value } of ds) {
    const w = words(value);
    const shorthandColour = () => w.filter((x) => !isWidth(x) && !BORDER_STYLE.test(x)).join(' ') || null;
    const shorthandWidth = () => (w.some((x) => /^(none|hidden)$/i.test(x)) ? 0 : borderWidth(w.find(isWidth) ?? 'medium'));
    let m;
    if (prop === 'border') {
      width.fill(shorthandWidth());
      colour = shorthandColour() ?? colour;
    } else if (prop === 'border-width') sides(w).forEach((x, i) => { width[i] = borderWidth(x); });
    else if (prop === 'border-color') colour = sides(w)[3];
    else if ((m = /^border-(top|right|bottom|left|inline-start)-width$/.exec(prop))) width[SIDE[m[1]]] = borderWidth(w[0]);
    else if ((m = /^border-(top|right|bottom|left|inline-start)$/.exec(prop))) {
      width[SIDE[m[1]]] = shorthandWidth();
      if (SIDE[m[1]] === 3) colour = shorthandColour() ?? colour;
    } else if (prop === 'border-left-color' || prop === 'border-inline-start-color') colour = w[0];
  }
  const [top, right, bottom, left] = width.map((x) => x ?? 0);
  return left > 0 && left > Math.max(top, right, bottom) && colour !== null && !NEUTRAL_PAINT.test(colour);
}

export function violations(f) {
  const v = [];
  const isTokens = f.rel === 'tokens.css';
  // tokens.css is the one place literals belong, but only where the generator put them.
  const css = isTokens ? f.css.replace(/\/\* @generated:[a-z-]+ \*\/[\s\S]*?\/\* @generated:end \*\//g, '') : f.css;
  for (const { selector, body, at } of rules(css)) {
    if (isTokens && at.some((p) => p.startsWith('@font-face'))) continue;
    const place = (s) => `${f.rel} { ${s.replace(/\s+/g, ' ').slice(0, 60)} }`;
    const where = place(selector);
    const ds = decls(body);
    // The checks that depend on the selector judge each selector of a list alone, and name it.
    for (const sel of selectors(selector)) {
      if (/:hover/.test(sel.replace(/:not\([^()]*\)/g, ''))) {
        for (const { prop } of ds) if (HOVER_MOTION.has(prop)) v.push(`${place(sel)}: transform on hover (${prop})`);
      }
      if (PSEUDO.test(sel)) {
        const bg = last(ds, ['background', 'background-color']);
        if (isZero(leftOf(ds)) && thin(last(ds, ['width', 'inline-size']) ?? '') && bg && !/^none$/i.test(bg) && !NEUTRAL_PAINT.test(bg)) {
          v.push(`${place(sel)}: left rail via pseudo-element`);
        }
      }
      if (oneSidedBorder(ds)) v.push(`${place(sel)}: colored border-left via border-width`);
    }
    for (const { prop, value } of ds) {
      const w = words(value);
      if (prop === 'box-shadow') v.push(`${where}: box-shadow`);
      if ((prop === 'outline' && w.some((x) => /^(none|0|0px)$/i.test(x)))
        || (prop === 'outline-style' && /^none\b/i.test(value))
        || (prop === 'outline-width' && /^0(px)?\b/i.test(value))) v.push(`${where}: outline none`);
      // Strict on purpose: anything in a left border that is not a width, a style or var(--border-*) counts as a color.
      // A rail drawn as `border-width: 0 0 0 3px` plus a colour is caught by oneSidedBorder() when both halves sit in
      // one rule; split across rules they are not checked, since a uniform border in one rule and a widened left side
      // in another cannot be told apart from a legitimate override without resolving the cascade.
      if (LEFT_BORDER.has(prop) && !w.every((x) => BORDER_NEUTRAL.test(x))) v.push(`${where}: colored border-left (${value})`);
      if ((prop === 'font-style' || prop === 'font') && w.some((x) => /^(italic|oblique)$/i.test(x))) v.push(`${where}: italic`);
      if (/var\(\s*--size-section-label\b/.test(value)) v.push(`${where}: section-label size (10px, below the 11px floor)`);
      if (hasColorLiteral(value)) v.push(`${where}: color literal (${prop})`);
      // The signature hue fails AA as text on raised surfaces; --text-accent is the one token allowed to carry it.
      if (prop === 'color' && /var\(\s*--signature\b/.test(value)) v.push(`${where}: signature hue as text (use --text-accent)`);
      if (RADIUS_PROP.test(prop) && !w.every((x) => RADIUS_WORD.test(x))) v.push(`${where}: radius off the RR scale (${value})`);
      if (SIZE_PROP.has(prop) && /(?<![\w.-])44px\b/.test(value)) v.push(`${where}: touch target literal (use --touch-target)`);
      if ((prop === 'background' || prop === 'background-image') && gradientRail(value)) v.push(`${where}: left rail via gradient`);
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

test('every CSS file satisfies every style rule', () => {
  const rest = files.filter((f) => f.rel !== 'tokens.css');
  assert.ok(rest.some((f) => f.rel === 'shell.css') && rest.some((f) => f.rel.startsWith('screens/')), 'the walk found the stylesheets');
  assert.deepEqual(rest.flatMap(violations), []);
});

test('tokens.css hand-written blocks satisfy the style rules (literals live only in generated blocks)', () => {
  assert.deepEqual(violations(tokens), []);
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
  caught('.a, .b:hover { .c { scale: 1.1; } }', 'transform on hover');
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

test('selftest: quoted strings never break the parse or read as colours', () => {
  const found = violations({ rel: 'screens/x.css', css: ".a::before { content: '}'; } .b { color: #fff; }" });
  assert.deepEqual(found, ['screens/x.css { .b }: color literal (color)']);
  clean(".a { font-family: 'Archivo Black', var(--font-narrator); }");
  clean('.a::after { content: "red"; }');
  clean('.a::after { content: "a \\" { b: #fff; }"; color: var(--foreground-default); }');
  assert.deepEqual(rules('.a { content: ";{}"; } .b { color: var(--foreground-bold); }').map((r) => r.selector), ['.a', '.b']);
});

test('selftest: radius on the RR scale', () => {
  for (const d of ['border-radius: 6px', 'border-radius: 50%', 'border-radius: 999px', 'border-radius: var(--r-md)',
    'border-top-left-radius: 2px', 'border-start-end-radius: 1em', 'border-radius: calc(var(--radius-lg) - 1px)']) {
    caught(`.a { ${d}; }`, 'radius off the RR scale');
  }
  for (const d of ['border-radius: 0 var(--radius-md) var(--radius-md) 0', 'border-radius: var(--radius-full)',
    'border-radius: var(--radius-xs) / var(--radius-sm)', 'border-top-right-radius: var(--radius-xl)', 'border-radius: inherit']) {
    clean(`.a { ${d}; }`);
  }
});

test('selftest: the touch target is one token', () => {
  for (const p of ['min-height', 'height', 'width', 'min-width', 'block-size', 'min-block-size', 'inline-size', 'min-inline-size']) {
    caught(`.a { ${p}: 44px; }`, 'touch target literal');
  }
  caught('.a { min-height: max(44px, 2em); }', 'touch target literal');
  clean('.a { min-height: var(--touch-target); }');
  clean('.a { min-height: 440px; }');
  clean('.a { min-height: 144px; padding: 44px; }');
});

test('selftest: no rail drawn by a pseudo-element', () => {
  const rail = (bg, extra = 'left: 0; top: 0; bottom: 0; width: 3px;') => `.a::before { content: ''; position: absolute; ${extra} background: ${bg}; }`;
  caught(rail('var(--signature)'), 'left rail via pseudo-element');
  caught(rail('var(--tone-critical)', 'inset: 0 auto 0 0; width: var(--space-micro);'), 'left rail via pseudo-element');
  caught(rail('var(--signature)', 'inset-inline-start: 0; width: 2px;'), 'left rail via pseudo-element');
  caught('.a:after { left: 0; width: 4px; background-color: var(--signature-fill); }', 'left rail via pseudo-element');
  clean(rail('var(--border-default)'));
  clean(rail('transparent'));
  clean(rail('var(--signature)', 'left: 0; width: 8px;'));
  clean(rail('var(--signature)', 'right: 0; width: 3px;'));
  clean('.dot::before { left: 0; width: 8px; background: var(--tone-success); }');
  clean('.a { left: 0; width: 3px; background: var(--signature); }');
});

test('selftest: every CSS named colour', () => {
  assert.equal(COLOR_NAMES.size, 148);
  for (const d of ['color: tomato', 'border-color: rebeccapurple', 'background: linear-gradient(to bottom, ivory, var(--card-bg))',
    'color: Snow', 'outline: 2px solid navy']) caught(`.a { ${d}; }`, 'color literal');
  for (const d of ['white-space: nowrap', 'animation-name: rise', 'color: transparent', 'color: currentColor',
    "font-family: 'Snow Sans', var(--font-narrator)", 'transition: color var(--dur-micro)', 'grid-area: tan-box',
    'width: calc(tan(45deg) * 1px)']) clean(`.a { ${d}; }`);
});

test('selftest: selector lists are judged one selector at a time', () => {
  const found = violations({ rel: 'screens/x.css', css: '.a, .b:hover { transform: scale(1.1); }' });
  assert.deepEqual(found, ['screens/x.css { .b:hover }: transform on hover (transform)']);
  clean('.a:hover, .b { color: var(--foreground-bold); }');
  const rail = violations({ rel: 'screens/x.css', css: '.a::before, .b { left: 0; width: 3px; background: var(--signature); }' });
  assert.deepEqual(rail, ['screens/x.css { .a::before }: left rail via pseudo-element']);
  assert.deepEqual(kinds(':is(.a, .b):hover { translate: 0 -1px; }'), ['transform on hover (translate)']);
  assert.deepEqual(selectors(':is(.a, .b):hover, :where(.c, .d) .e, .f[data-x="g, h"]'), [':is(.a, .b):hover', ':where(.c, .d) .e', '.f[data-x="g, h"]']);
});

test('selftest: no rail drawn with a gradient or a one-sided border', () => {
  caught('.a { background: linear-gradient(to right, var(--signature) 3px, transparent 3px); }', 'left rail via gradient');
  caught('.a { background-image: linear-gradient(90deg, var(--tone-critical) 0 4px, var(--card-bg) 4px); }', 'left rail via gradient');
  caught('.a { background: var(--card-bg) linear-gradient(to right, var(--signature) var(--space-micro), transparent 0); }', 'left rail via gradient');
  caught('.a { border-width: 0 0 0 3px; border-style: solid; border-color: var(--signature); }', 'colored border-left via border-width');
  caught('.a { border: 1px solid; border-left-width: 3px; border-color: var(--tone-critical); }', 'colored border-left via border-width');
  caught('.a { border: 1px solid var(--signature); border-inline-start-width: 4px; }', 'colored border-left via border-width');
  caught('.a { border-width: 1px 1px 1px 3px; border-color: var(--tone-critical); }', 'colored border-left via border-width');
  clean('.a { background: linear-gradient(to right, var(--border-default) 1px, transparent 1px); }');
  clean('.a { background: linear-gradient(to bottom, var(--signature-glow) 2px, transparent); }');
  clean('.a { background: linear-gradient(to right, transparent, var(--card-bg)); }');
  clean('.a { background: linear-gradient(to right, var(--card-bg) 0, transparent 100%); }');
  clean('.a { border-width: 1px; border-color: var(--tone-critical); }');
  clean('.a { border-width: 0 0 0 1px; border-color: var(--border-default); }');
  clean('.a { border: 1px solid var(--tone-critical); }');
  clean('.a { border-bottom: 2px solid var(--signature); }');
  clean('.a { border-left-width: 3px; }');
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
