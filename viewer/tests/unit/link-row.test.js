// User intent: a row or card that opens something is a real link whose own buttons sit beside it, never inside it —
// so the whole row is clickable, keyboard reachable, opens in a new tab like any link, and no control is nested.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const { linkRow, isInteractive } = await import('../../js/components/link-row.js');
const { truncate } = await import('../../js/lib/text.js');

const el = (html) => { const t = document.createElement('template'); t.innerHTML = html.trim(); return t.content.firstElementChild; };

// Declarations of every top-level rule in rows.css whose selector list names `selector` exactly, merged in order.
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

test('structure: the row holds the link, then the content, then the controls', () => {
  const copy = el('<button type="button" class="copy">Copy id</button>');
  const row = linkRow({ href: '#/task/T-102', name: 'T-102 · Re-skin', content: [el('<span>In progress</span>')], controls: [copy] });
  assert.equal(row.tagName, 'DIV');
  assert.ok(row.classList.contains('link-row'));
  assert.deepEqual([...row.children].map((c) => c.className), ['link-row__link', 'link-row__content', 'link-row__controls']);
  const [a, content, controls] = row.children;
  assert.equal(a.tagName, 'A');
  assert.equal(a.textContent, 'T-102 · Re-skin');
  assert.equal(a.getAttribute('aria-label'), null, 'the accessible name is the link text');
  assert.equal(content.tagName, 'DIV');
  assert.equal(content.textContent, 'In progress');
  assert.equal(controls.tagName, 'DIV');
  assert.equal(controls.firstElementChild, copy);
});

test('structure: a Node name is placed in the link as it is; tag and className are the caller\'s', () => {
  const name = el('<span class="id">T-1</span>');
  const row = linkRow({ href: '#/task/T-1', name, tag: 'li', className: 'card card--task' });
  assert.equal(row.tagName, 'LI');
  assert.deepEqual([...row.classList], ['link-row', 'card', 'card--task']);
  assert.equal(row.querySelector('.link-row__link').firstChild, name);
});

test('structure: controls are omitted when there are none; content may be empty', () => {
  const row = linkRow({ href: '#/epic/E-1', name: 'Epic' });
  assert.deepEqual([...row.children].map((c) => c.className), ['link-row__link', 'link-row__content']);
  assert.equal(row.querySelector('.link-row__content').childNodes.length, 0);
  assert.equal(linkRow({ href: '#/epic/E-1', name: 'Epic', controls: [] }).querySelector('.link-row__controls'), null);
});

test('structure: a text name stays text', () => {
  const row = linkRow({ href: '#/task/T-1', name: '<img src=x onerror=alert(1)>' });
  const a = row.querySelector('a');
  assert.equal(a.children.length, 0);
  assert.equal(a.textContent, '<img src=x onerror=alert(1)>');
});

test('an interactive name or content node is refused with a TypeError naming its tag and class', () => {
  assert.throws(() => linkRow({ href: '#/task/T-1', name: el('<button class="copy">x</button>') }),
    (e) => e instanceof TypeError && /button/.test(e.message) && /copy/.test(e.message));
  assert.throws(() => linkRow({ href: '#/task/T-1', name: 'x', content: [el('<span class="meta"><a class="epic-link" href="#/epic/E-1">E-1</a></span>')] }),
    (e) => e instanceof TypeError && /\ba\b/.test(e.message) && /epic-link/.test(e.message));
  assert.throws(() => linkRow({ href: '#/task/T-1', name: 'x', content: [el('<input class="pick">')] }),
    (e) => e instanceof TypeError && /input/.test(e.message) && /pick/.test(e.message));
});

test('isInteractive: the node is, or contains, something that takes focus or a click', () => {
  for (const html of [
    '<a href="#/x">x</a>', '<button>x</button>', '<input>', '<select></select>', '<textarea></textarea>',
    '<span tabindex="-1">x</span>', '<div contenteditable="true">x</div>', '<div><p><button>x</button></p></div>',
    '<span><a href="#">x</a></span>',
  ]) assert.equal(isInteractive(el(html)), true, html);
  for (const node of [el('<a>no href</a>'), el('<span><b>x</b></span>'), document.createTextNode('x'), null, undefined, 'text']) {
    assert.equal(isInteractive(node), false, String(node?.outerHTML ?? node));
  }
});

test('the whole row is the link\'s hit area; controls sit above it', () => {
  assert.equal(declsOf('.link-row').position, 'relative');
  const after = declsOf('.link-row__link::after');
  assert.equal(after.content, "''");
  assert.equal(after.position, 'absolute');
  assert.equal(after.inset, '0');
  const controls = declsOf('.link-row__controls');
  assert.equal(controls.position, 'relative');
  assert.equal(controls['z-index'], '1');
});

test('the focus ring is drawn on the row, the link\'s own is unpainted; hover is colour only', () => {
  const ring = declsOf('.link-row:has(> .link-row__link:focus-visible)');
  assert.equal(ring.outline, '2px solid var(--border-focus)');
  assert.equal(ring['outline-offset'], '2px');
  assert.equal(declsOf('.link-row__link:focus-visible')['outline-color'], 'transparent');
  assert.deepEqual(declsOf('.link-row:hover'), { background: 'var(--card-bg-hover)' });
});

test('a row without a name is refused: the link\'s text is its accessible name', () => {
  for (const name of [undefined, null, '', '   ', el('<span>  </span>')]) {
    assert.throws(() => linkRow({ href: '#/task/T-1', name, content: ['T-1 · Re-skin'] }),
      (e) => e instanceof TypeError && /name/.test(e.message), String(name));
  }
});

// The link's ::after covers the row, so the pointer never reaches a cut element's own title: the row carries it. On the
// link it would be read a second time, as the link's description; the controls' empty title keeps them from inheriting it.
test('the row carries the full text of what is cut inside it: the titles of name and content, in order', () => {
  const name = truncate('T-102 · Re-skin the Kanban cards and columns');
  const meta = el('<span><span class="truncate" title="Epic: Viewer re-skin">Epic: Viewer re-skin</span></span>');
  const copy = el('<button type="button">Copy id</button>');
  const row = linkRow({ href: '#/task/T-102', name, content: [meta, el('<span>In progress</span>')], controls: [copy] });
  assert.equal(row.title, 'T-102 · Re-skin the Kanban cards and columns\nEpic: Viewer re-skin');
  assert.equal(row.querySelector('a.link-row__link').hasAttribute('title'), false, 'the link is named once, by its text');
  const controls = row.querySelector('.link-row__controls');
  assert.equal(controls.hasAttribute('title'), true);
  assert.equal(controls.title, '');
});

test('a truncate()d content node alone gives the row its title', () => {
  const row = linkRow({ href: '#/task/T-1', name: 'T-1', content: [truncate('A long title in the content')] });
  assert.equal(row.title, 'A long title in the content');
  assert.equal(row.querySelector('a').hasAttribute('title'), false);
});

test('an explicit title wins; with nothing cut and no title neither the row nor the link has one', () => {
  const name = el('<span title="cut">cut</span>');
  const titled = linkRow({ href: '#/task/T-1', name, title: 'Full words' });
  assert.equal(titled.title, 'Full words');
  assert.equal(titled.querySelector('a').hasAttribute('title'), false);
  const plain = linkRow({ href: '#/task/T-1', name: 'Plain' });
  assert.equal(plain.hasAttribute('title'), false);
  assert.equal(plain.querySelector('a').hasAttribute('title'), false);
});

test('href is used verbatim', () => {
  for (const href of ['#/task/T-102', '#/epic/E%2F1', '#/issues?x=1']) {
    assert.equal(linkRow({ href, name: 'x' }).querySelector('a').getAttribute('href'), href);
  }
});
