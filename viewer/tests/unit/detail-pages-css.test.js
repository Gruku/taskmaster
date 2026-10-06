// User intent: the issue and bug detail rules live only in detail-pages.css so the list screens' files can be enforced without touching 3c's pages.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..', '..');
const CSS_DIR = join(ROOT, 'css');
const read = (p) => readFileSync(join(CSS_DIR, p), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '');
const walk = (d) => readdirSync(d).flatMap((n) => {
  const p = join(d, n);
  return statSync(p).isDirectory() ? walk(p) : p.endsWith('.css') ? [p] : [];
});

const SELECTORS = ['.id-empty', '.id-head', '.id-meta', '.id-sev', '.id-status',
  '.id-title', '.id-location', '.id-grid', '.id-main', '.id-side', '.id-h', '.id-body', '.id-repro-list', '.id-side-block',
  '.id-aging', '.id-dl', '.id-rel-pill', '.bug-detail', '.bug-detail__sev',
  '.bug-detail__actions', '.bug-detail__action-btn'];
const re = (s) => new RegExp('(^|[},\\s])' + s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + '(?![\\w-])');

test('detail selectors live in detail-pages.css only', () => {
  const dp = read('screens/detail-pages.css');
  const old = read('screens/issues.css') + read('screens/bugs.css');
  for (const s of SELECTORS) {
    assert.ok(re(s).test(dp), `${s} missing from detail-pages.css`);
    assert.ok(!re(s).test(old), `${s} still in issues.css/bugs.css`);
  }
});

test('the dead italic body rule is gone everywhere', () => {
  for (const f of walk(CSS_DIR)) assert.ok(!readFileSync(f, 'utf8').includes('id-body--italic'), f);
});

test('index.html links detail-pages.css right after ideas.css', () => {
  const links = [...readFileSync(join(ROOT, 'index.html'), 'utf8').matchAll(/<link rel="stylesheet" href="([^"]+)"/g)].map((m) => m[1]);
  const i = links.indexOf('css/screens/ideas.css');
  assert.ok(i >= 0);
  assert.equal(links[i + 1], 'css/screens/detail-pages.css');
});
