// User intent: a sortable column header is a real button that says which way the table is sorted — to the eye with
// an arrow and to a screen reader with aria-sort on the one sorted column.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const { nextSort, sortHeader } = await import('../../js/components/sort-header.js');
const { icon } = await import('../../js/components/icon.js');

const CSS = readFileSync(new URL('../../css/components/rows.css', import.meta.url), 'utf8').replace(/\/\*[\s\S]*?\*\//g, '')
  .replace(/@media[^{]*\{(?:[^{}]*\{[^{}]*\})*[^{}]*\}/g, '');   // top level only: media rules are checked in the browser
function declsOf(selector) {
  const out = {};
  for (const m of CSS.matchAll(/([^{}]+)\{([^{}]*)\}/g)) {
    if (!m[1].split(',').map((s) => s.trim().replace(/\s+/g, ' ')).includes(selector)) continue;
    for (const d of m[2].split(';')) {
      const i = d.indexOf(':');
      if (i > 0) out[d.slice(0, i).trim()] = d.slice(i + 1).trim();
    }
  }
  return out;
}
const inner = (svg) => svg.innerHTML;

test('nextSort: the same key flips the direction', () => {
  assert.deepEqual(nextSort({ by: 'title', dir: 'asc' }, 'title'), { by: 'title', dir: 'desc' });
  assert.deepEqual(nextSort({ by: 'title', dir: 'desc' }, 'title'), { by: 'title', dir: 'asc' });
});

test('nextSort: another key starts ascending', () => {
  assert.deepEqual(nextSort({ by: 'title', dir: 'desc' }, 'id'), { by: 'id', dir: 'asc' });
  assert.deepEqual(nextSort(null, 'id'), { by: 'id', dir: 'asc' });
  assert.deepEqual(nextSort(undefined, 'id'), { by: 'id', dir: 'asc' });
});

test('a header is a column-scoped th of class sort-th', () => {
  const th = sortHeader({ key: 'id', label: 'ID', sort: { by: 'id', dir: 'asc' }, onSort() {} });
  assert.equal(th.tagName, 'TH');
  assert.equal(th.getAttribute('scope'), 'col');
  assert.ok(th.classList.contains('sort-th'));
});

test('sortable: a type=button holding the label and the direction icon', () => {
  const th = sortHeader({ key: 'title', label: 'Title', sort: { by: 'id', dir: 'asc' }, onSort() {} });
  const btn = th.firstElementChild;
  assert.equal(th.children.length, 1);
  assert.equal(btn.tagName, 'BUTTON');
  assert.ok(btn.classList.contains('sort-header'));
  assert.equal(btn.getAttribute('type'), 'button');
  const [label, dir] = btn.children;
  assert.ok(label.classList.contains('sort-header__label'));
  assert.equal(label.textContent, 'Title');
  assert.equal(btn.textContent, 'Title', 'the button is named by its label alone');
  assert.ok(dir.classList.contains('sort-header__dir'));
});

test('the active column shows a 12px chevron in --asc or --desc; others show the 12px sort glyph in --none', () => {
  const asc = sortHeader({ key: 'id', label: 'ID', sort: { by: 'id', dir: 'asc' }, onSort() {} }).querySelector('.sort-header__dir');
  assert.deepEqual([...asc.classList], ['sort-header__dir', 'sort-header__dir--asc']);
  assert.equal(inner(asc.querySelector('svg')), inner(icon('chevron')));
  assert.equal(asc.querySelector('svg').getAttribute('width'), '12');

  const desc = sortHeader({ key: 'id', label: 'ID', sort: { by: 'id', dir: 'desc' }, onSort() {} }).querySelector('.sort-header__dir');
  assert.deepEqual([...desc.classList], ['sort-header__dir', 'sort-header__dir--desc']);
  assert.equal(inner(desc.querySelector('svg')), inner(icon('chevron')));

  const none = sortHeader({ key: 'title', label: 'Title', sort: { by: 'id', dir: 'desc' }, onSort() {} }).querySelector('.sort-header__dir');
  assert.deepEqual([...none.classList], ['sort-header__dir', 'sort-header__dir--none']);
  assert.equal(inner(none.querySelector('svg')), inner(icon('sort')));
  assert.equal(none.querySelector('svg').getAttribute('height'), '12');
});

test('only the active column carries aria-sort', () => {
  const sort = { by: 'id', dir: 'asc' };
  assert.equal(sortHeader({ key: 'id', label: 'ID', sort, onSort() {} }).getAttribute('aria-sort'), 'ascending');
  assert.equal(sortHeader({ key: 'id', label: 'ID', sort: { by: 'id', dir: 'desc' }, onSort() {} }).getAttribute('aria-sort'), 'descending');
  assert.equal(sortHeader({ key: 'title', label: 'Title', sort, onSort() {} }).hasAttribute('aria-sort'), false);
  assert.equal(sortHeader({ key: 'title', label: 'Title', sort: null, onSort() {} }).hasAttribute('aria-sort'), false);
});

test('a click calls onSort with the next sort', () => {
  const got = [];
  const onSort = (s) => got.push(s);
  sortHeader({ key: 'title', label: 'Title', sort: { by: 'id', dir: 'asc' }, onSort }).querySelector('button').click();
  sortHeader({ key: 'id', label: 'ID', sort: { by: 'id', dir: 'asc' }, onSort }).querySelector('button').click();
  assert.deepEqual(got, [{ by: 'title', dir: 'asc' }, { by: 'id', dir: 'desc' }]);
});

test('a sortable header without onSort is refused when built, not when clicked', () => {
  for (const onSort of [undefined, null, 'sort']) {
    assert.throws(() => sortHeader({ key: 'id', label: 'ID', sort: null, onSort }), TypeError, String(onSort));
  }
  assert.doesNotThrow(() => sortHeader({ key: 'notes', label: 'Notes', sortable: false }));
});

test('not sortable: the th holds the label span only, and no aria-sort', () => {
  const th = sortHeader({ key: 'notes', label: 'Notes', sortable: false, sort: { by: 'notes', dir: 'asc' }, onSort() { throw new Error('no'); } });
  assert.equal(th.getAttribute('scope'), 'col');
  assert.equal(th.children.length, 1);
  assert.ok(th.firstElementChild.classList.contains('sort-header__label'));
  assert.equal(th.firstElementChild.tagName, 'SPAN');
  assert.equal(th.textContent, 'Notes');
  assert.equal(th.querySelector('button'), null);
  assert.equal(th.hasAttribute('aria-sort'), false);
});

test('rows.css: the header button takes its look from the header cell; the arrow turns statically', () => {
  assert.deepEqual(declsOf('.sort-header'), {
    appearance: 'none', display: 'inline-flex', 'align-items': 'center', gap: 'var(--space-micro)', width: '100%',
    padding: '0', border: '0', background: 'transparent', color: 'inherit', font: 'inherit', 'letter-spacing': 'inherit',
    'text-transform': 'inherit', 'text-align': 'left', cursor: 'pointer',
  });
  assert.deepEqual(declsOf('.sort-header:hover'), { color: 'var(--foreground-bold)' });
  assert.equal(declsOf('.sort-header__dir--asc .icon').transform, 'rotate(-90deg)');
  assert.equal(declsOf('.sort-header__dir--desc .icon').transform, 'rotate(90deg)');
  assert.equal(declsOf('.sort-header__dir--none').color, 'var(--foreground-subtle)');
});
