// User intent: the stage-1 migration aliases stay gone — every stylesheet and script names the RR role it means,
// and tokens.css keeps only the variables some script still sets.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative } from 'node:path';
import { fileURLToPath } from 'node:url';

// The names spec §3.2 and stage 1 introduced as stand-ins. Not a prefix ban: --bg-page,
// --bg-recessed, --accent-cyan … are RR names and stay.
export const LEGACY = ['--bg-canvas', '--bg-shell', '--bg-panel', '--bg-card', '--bg-card-hover', '--bg-board-col',
  '--bg-deep', '--bg-issue', '--border', '--border-soft', '--ink', '--ink-1', '--ink-2', '--ink-3', '--ink-4',
  '--ink-on-accent', '--accent', '--accent-2', '--accent-soft', '--accent-blue', '--accent-edit', '--accent-green',
  '--green', '--amber', '--gold', '--red', '--purple', '--diff-add', '--diff-mod', '--diff-del', '--sev-critical',
  '--sev-high', '--sev-medium', '--sev-low', '--epic-1', '--epic-2', '--epic-3', '--epic-4', '--epic-5', '--epic-6',
  '--bundle-1', '--bundle-2', '--bundle-3', '--bundle-4', '--bundle-5', '--bundle-6', '--font-sans', '--font-mono',
  '--font-serif', '--text-xs', '--text-sm', '--text-base', '--text-md', '--text-lg', '--text-xl', '--text-2xl',
  '--text-3xl', '--sp-1', '--sp-2', '--sp-3', '--sp-4', '--sp-5', '--sp-6', '--sp-7', '--sp-8', '--sp-9', '--sp-10',
  '--sp-12', '--sp-13', '--r-sm', '--r-md', '--r-lg', '--r-xl', '--r-2xl', '--t-fast', '--t-base', '--t-slow',
  '--ease', '--page-pad', '--page-gap', '--bl', '--surface', '--surface-1', '--surface2', '--s2', '--danger-ink',
  '--danger-border', '--danger-bg', '--amber-tint-bg', '--amber-tint-border', '--muted-tint-bg', '--muted-tint-border',
  '--shell-bg-sidebar', '--shell-label-size', '--shell-hover-overlay', '--card-recent-glow', '--issues-card-bg',
  '--issues-card-border', '--issues-investigating', '--task-grid-gap', '--task-section-mb', '--task-section-h',
  '--graph-frame-bg', '--graph-frame-border', '--graph-frame-shadow', '--graph-edge-stroke',
  '--graph-edge-stroke-active', '--graph-col-guide'];

const VIEWER = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
const walk = (dir, ext) => readdirSync(dir).flatMap((n) => {
  const p = join(dir, n);
  return statSync(p).isDirectory() ? walk(p, ext) : p.endsWith(ext) ? [p] : [];
});
const read = (p) => ({ rel: relative(VIEWER, p).replace(/\\/g, '/'), text: readFileSync(p, 'utf8') });
const TOKENS = readFileSync(join(VIEWER, 'css', 'tokens.css'), 'utf8');
const CSS = walk(join(VIEWER, 'css'), '.css').map(read);
const JS = walk(join(VIEWER, 'js'), '.js').map(read).filter((f) => !f.rel.startsWith('js/_dormant/'));

// Every hit as "file: --name ×count", so a failure is the work list.
const hits = (files, pattern) => files.flatMap((f) => LEGACY.flatMap((name) => {
  const n = (f.text.match(pattern(esc(name))) || []).length;
  return n ? [`${f.rel}: ${name} ×${n}`] : [];
}));

test('tokens.css defines no legacy alias', () => {
  assert.deepEqual(hits([{ rel: 'css/tokens.css', text: TOKENS }], (n) => new RegExp(`(${n})\\s*:`, 'g')), []);
});

test('no CSS file references a legacy alias', () => {
  assert.deepEqual(hits(CSS, (n) => new RegExp(`var\\(\\s*${n}\\s*[,)]`, 'g')), []);
});

test('no script under viewer/js, nor index.html, names a legacy alias', () => {
  const files = [...JS, read(join(VIEWER, 'index.html'))];
  assert.deepEqual(hits(files, (n) => new RegExp(`(^|[^\\w-])${n}(?![\\w-])`, 'gm')), []);
});

// A script sets a token with setProperty('--x', …), through a constant holding '--x', or in a style string ('--x: …').
test('every token under "Set from JS" in tokens.css is set by some script', () => {
  const block = TOKENS.split('/* ── Set from JS ── */')[1];
  assert.ok(block, 'tokens.css has a "Set from JS" section');
  const names = [...block.split(/\/\* ── |\n}/)[0].matchAll(/(--[\w-]+)\s*:/g)].map((m) => m[1]);
  assert.ok(names.length > 0, 'the section names at least one token');
  const unset = names.filter((name) => !JS.some((f) => new RegExp(
    `['"\`]${esc(name)}['"\`]|${esc(name)}\\s*:`).test(f.text)));
  assert.deepEqual(unset, []);
});
