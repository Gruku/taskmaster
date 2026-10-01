// User intent: regenerate the design-system part of tokens.css from the pinned Reality Reprojection tokens.json, so values are copied by machine, never by hand.
// Usage: node viewer/tools/gen-tokens.mjs   (rewrites the two @generated blocks in viewer/css/tokens.css)
import { readFileSync, writeFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const SRC = join(here, '../../docs/specs/assets/reality-reprojection-2026-10-01/tokens.json');
const OUT = join(here, '../css/tokens.css');
const rr = JSON.parse(readFileSync(SRC, 'utf8'));
const v = (x) => String(x).replace(/^\{(.+)\}$/, 'var(--$1)');
const line = (name, val) => `  --${name}: ${v(val)};`;

const dark = [];
const light = [];
for (const t of rr.color.tokens) {
  if (typeof t.value === 'string') { dark.push(line(t.name, t.value)); continue; }
  dark.push(line(t.name, t.value.dark));
  if (t.value.light) light.push(line(t.name, t.value.light));
}
for (const [k, fam] of Object.entries(rr.type.families)) {
  if (k === 'declaration-alt' || k === 'survivalist') continue;
  dark.push(`  --font-${k}: ${fam};`);
}
for (const fam of ['spacing', 'radius', 'typescale', 'leading', 'tracking', 'weight', 'blur', 'easing', 'duration', 'stagger']) {
  for (const t of rr[fam].tokens) dark.push(line(t.name, t.value));
}

let css = readFileSync(OUT, 'utf8');
const put = (tag, body) => {
  const re = new RegExp(`(/\\* @generated:${tag} \\*/)[\\s\\S]*?(/\\* @generated:end \\*/)`);
  if (!re.test(css)) throw new Error(`marker @generated:${tag} missing in tokens.css`);
  css = css.replace(re, `$1\n${body.join('\n')}\n  $2`);
};
put('rr-dark', dark);
put('rr-light', light);
writeFileSync(OUT, css);
console.log(`tokens.css: ${dark.length} base tokens, ${light.length} light overrides`);
