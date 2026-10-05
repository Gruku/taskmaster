// User intent: the topbar's per-screen controls are the shared buttons with drawn glyphs, its search clears with a
// real button, and no screen offers a control that does nothing ("coming soon").
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.Event = dom.window.Event;

const { tmAction, tmSearch, tmSegmented, claimTopbar } = await import('../../js/lib/topbar.js');

const JS_DIR = join(dirname(fileURLToPath(import.meta.url)), '..', '..', 'js');

test('tmAction: a primary button with a drawn glyph, its label and an accessible name', () => {
  const b = tmAction({ icon: 'plus', label: 'Task', variant: 'primary', title: 'Add task' });
  assert.equal(b.tagName, 'BUTTON');
  assert.equal(b.type, 'button');
  assert.equal(b.className, 'btn btn--primary');
  const svg = b.querySelector('svg.icon');
  assert.ok(svg);
  assert.equal(svg.getAttribute('aria-hidden'), 'true');
  assert.equal(b.textContent, 'Task');
  assert.equal(b.getAttribute('aria-label'), 'Add task');
});

test('tmAction: each variant is the matching button class', () => {
  assert.equal(tmAction({ label: 'A', variant: 'primary' }).className, 'btn btn--primary');
  assert.equal(tmAction({ label: 'A', variant: 'ghost' }).className, 'btn btn--ghost');
  assert.equal(tmAction({ icon: 'edit', variant: 'icon', title: 'Edit' }).className, 'btn btn--ghost btn--icon');
  assert.equal(tmAction({ label: 'A' }).className, 'btn btn--secondary');
  assert.equal(tmAction({ label: 'A' }).classList.contains('tm-action'), false);
});

test('tmAction: a glyph that is not a drawn icon is refused', () => {
  assert.throws(() => tmAction({ icon: '+', label: 'Task' }), /unknown icon/);
});

test('tmAction: a link keeps its href and no button type', () => {
  const a = tmAction({ icon: 'external', label: 'Open', href: '#/kanban' });
  assert.equal(a.tagName, 'A');
  assert.equal(a.getAttribute('href'), '#/kanban');
  assert.equal(a.hasAttribute('type'), false);
  assert.equal(a.className, 'btn btn--secondary');
});

test('tmSearch: the clear control is a real button with a drawn glyph, not a ×', () => {
  const { el, input } = tmSearch({ placeholder: 'Find…' });
  const clear = el.querySelector('.tm-search__clear');
  assert.equal(clear.tagName, 'BUTTON');
  assert.equal(clear.type, 'button');
  assert.deepEqual([...clear.classList], ['tm-search__clear', 'btn', 'btn--ghost', 'btn--icon', 'btn--sm']);
  assert.equal(clear.getAttribute('aria-label'), 'Clear search');
  assert.ok(clear.querySelector('svg.icon'));
  assert.equal(clear.textContent.includes('×'), false);
  input.value = 'abc';
  input.dispatchEvent(new dom.window.Event('input', { bubbles: true }));
  assert.equal(el.classList.contains('tm-search--has-value'), true);
  clear.click();
  assert.equal(input.value, '');
  assert.equal(el.classList.contains('tm-search--has-value'), false);
});

test('tmSegmented: pressing a segment moves aria-pressed to it', () => {
  const changes = [];
  const seg = tmSegmented([{ key: 'A', label: 'Document' }, { key: 'B', label: 'Graph' }], { value: 'A', onChange: (k) => changes.push(k) });
  assert.equal(seg.className, 'tm-segmented');
  const [a, b] = seg.querySelectorAll('button');
  assert.equal(a.getAttribute('aria-pressed'), 'true');
  assert.equal(b.getAttribute('aria-pressed'), 'false');
  b.click();
  assert.equal(a.getAttribute('aria-pressed'), 'false');
  assert.equal(b.getAttribute('aria-pressed'), 'true');
  assert.deepEqual(changes, ['B']);
});

test('no screen offers a "coming soon" control', () => {
  for (const f of ['screens/issues.js', 'screens/sessions.js']) {
    assert.doesNotMatch(readFileSync(join(JS_DIR, f), 'utf8'), /coming soon/i, f);
  }
});

test('claimTopbar: empties both rows and keeps one Filters button in row 2 for the life of the page', () => {
  document.body.innerHTML = '<span id="topbar-count">3</span><div id="topbar-primary"><button>Go</button></div><div id="topbar-actions"><span>old</span></div>';
  const row = claimTopbar();
  assert.equal(row, document.getElementById('topbar-actions'));
  assert.equal(document.getElementById('topbar-count').childNodes.length, 0);
  assert.equal(document.getElementById('topbar-primary').childNodes.length, 0);
  const [filters] = row.children;
  assert.equal(row.children.length, 1);
  assert.ok(filters.classList.contains('overflow-more'));
  assert.equal(filters.hidden, true);
  assert.match(filters.textContent, /^Filters/);
  assert.ok(filters.querySelector('svg.icon'));
  row.append(tmSearch().el, tmAction({ label: 'A' }));
  assert.equal(claimTopbar(), row);
  assert.deepEqual([...row.children], [filters]);
});
