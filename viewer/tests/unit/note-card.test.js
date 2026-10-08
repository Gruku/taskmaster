// User intent: a Dashboard note can be read in full, edited, pinned and archived by keyboard, and a note write the
// server refuses never throws away what the person typed — in the note editor or in the composer.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';
import { JSDOM } from 'jsdom';

// The real vendored parser, so the sanitiser runs on what marked really produces.
const dom = new JSDOM('<!doctype html><html><body></body></html>', { runScripts: 'outside-only', url: 'http://localhost/' });
dom.window.eval(readFileSync(join(dirname(fileURLToPath(import.meta.url)), '../../vendor/marked.min.js'), 'utf8'));
globalThis.window = dom.window;
globalThis.document = dom.window.document;
globalThis.requestAnimationFrame = (fn) => { fn(); return 0; };

const { createNoteCard } = await import('../../js/components/desk/note-card.js');
const { createComposer } = await import('../../js/components/desk/composer.js');

const NOTE = { id: 'NOTE-007', author: 'claude', pinned: false, created: '2026-10-05T10:00:00Z', body: 'Check the counts.' };
const tick = () => new Promise((r) => setTimeout(r, 0));
const key = (el, k, extra = {}) => el.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: k, bubbles: true, cancelable: true, ...extra }));

function mount(opts = {}) {
  document.body.replaceChildren();
  const card = createNoteCard({ note: NOTE, onPin: async () => true, onArchive: async () => true, onSave: async () => true, ...opts });
  document.body.append(card.root);
  return card;
}
function overflow(body, scrollHeight, clientHeight) {
  Object.defineProperty(body, 'scrollHeight', { configurable: true, get: () => scrollHeight });
  Object.defineProperty(body, 'clientHeight', { configurable: true, get: () => clientHeight });
}

test('the head names its author and time in words and its controls by name — no emoji, no ✕', () => {
  const { root } = mount();
  assert.equal(root.localName, 'article');
  const who = root.querySelector('.dk-note__head span.dk-note__who');
  assert.equal(who.textContent, 'Claude');
  assert.equal(root.getAttribute('aria-labelledby'), who.id);
  assert.ok(who.id);
  const when = root.querySelector('time.dk-note__when');
  assert.equal(when.getAttribute('datetime'), NOTE.created);
  assert.ok(when.hasAttribute('title') && when.getAttribute('title'));
  const edit = root.querySelector('button.dk-note__edit.btn.btn--ghost.btn--icon.btn--sm');
  assert.equal(edit.getAttribute('aria-label'), 'Edit note');
  assert.ok(edit.querySelector('svg.icon'));
  const pin = root.querySelector('button.dk-note__pin.btn.btn--ghost.btn--sm');
  assert.equal(pin.textContent, 'Pin');
  assert.equal(pin.getAttribute('aria-pressed'), 'false');
  const archive = root.querySelector('button.dk-note__archive.btn.btn--ghost.btn--icon.btn--sm');
  assert.equal(archive.getAttribute('aria-label'), 'Archive note');
  assert.ok(archive.querySelector('svg.icon'));
  assert.doesNotMatch(root.querySelector('.dk-note__head').textContent, /[✕✦📌]/u);

  document.body.replaceChildren();
  const user = createNoteCard({ note: { ...NOTE, author: 'user', pinned: true } }).root;
  assert.equal(user.querySelector('.dk-note__who').textContent, 'You');
  assert.equal(user.querySelector('.dk-note__pin').textContent, 'Unpin');
  assert.equal(user.querySelector('.dk-note__pin').getAttribute('aria-pressed'), 'true');
});

test('a body taller than its clamp gets a fade and a Show more button that expands and collapses it', () => {
  const card = mount();
  const body = card.root.querySelector('.dk-note__body');
  assert.ok(body.id);
  overflow(body, 900, 300);
  card.measure();
  assert.ok(card.root.classList.contains('is-clamped'));
  assert.equal(body.querySelector('span.dk-note__fade').getAttribute('aria-hidden'), 'true');
  const more = card.root.querySelector('button.dk-note__more.btn.btn--ghost.btn--sm');
  assert.equal(more.textContent, 'Show more');
  assert.equal(more.getAttribute('aria-expanded'), 'false');
  assert.equal(more.getAttribute('aria-controls'), body.id);
  more.click();
  assert.equal(more.textContent, 'Show less');
  assert.equal(more.getAttribute('aria-expanded'), 'true');
  assert.ok(card.root.classList.contains('is-expanded'));
  more.click();
  assert.equal(more.textContent, 'Show more');
  assert.equal(more.getAttribute('aria-expanded'), 'false');
  assert.ok(!card.root.classList.contains('is-expanded'));
});

test('a body that fits has no Show more button', () => {
  const card = mount();
  overflow(card.root.querySelector('.dk-note__body'), 100, 300);
  card.measure();
  assert.equal(card.root.querySelector('.dk-note__more'), null);
  assert.ok(!card.root.classList.contains('is-clamped'));
});

test('Edit opens a labelled, focused editor; Escape puts the body back and focus on Edit', () => {
  const { root } = mount();
  root.querySelector('.dk-note__edit').click();
  const ta = root.querySelector('textarea');
  assert.equal(ta.getAttribute('aria-label'), 'Edit note');
  assert.equal(document.activeElement, ta);
  assert.equal(ta.value, NOTE.body);
  key(ta, 'Escape');
  assert.equal(root.querySelector('textarea'), null);
  assert.ok(root.querySelector('.dk-note__body'));
  assert.equal(document.activeElement, root.querySelector('.dk-note__edit'));
});

test('Ctrl+Enter saves the changed text; a refused save re-opens the editor holding it', async () => {
  const calls = [];
  const { root } = mount({ onSave: async (note, text) => { calls.push([note.id, text]); return false; } });
  root.querySelector('.dk-note__edit').click();
  const ta = root.querySelector('textarea');
  ta.value = 'Check the counts twice.';
  key(ta, 'Enter', { ctrlKey: true });
  await tick();
  assert.deepEqual(calls, [['NOTE-007', 'Check the counts twice.']]);
  const again = root.querySelector('textarea');
  assert.ok(again, 'the editor is back');
  assert.equal(again.value, 'Check the counts twice.');
});

test('a click on a link in the body follows the link and opens no editor', () => {
  const { root } = mount({ note: { ...NOTE, body: 'See [the plan](https://example.com/plan).' } });
  document.addEventListener('click', (e) => e.preventDefault(), { once: true });
  const link = root.querySelector('.dk-note__body a');
  assert.ok(link);
  link.click();
  assert.equal(root.querySelector('textarea'), null);
  root.querySelector('.dk-note__body p').click();
  assert.ok(root.querySelector('textarea'), 'a click on the text still edits');
});

test('a note body is sanitised: an <img onerror> never reaches the DOM', () => {
  const { root } = mount({ note: { ...NOTE, body: '<img src=x onerror=alert(1)>' } });
  assert.equal(root.querySelector('[onerror]'), null);
  assert.equal(root.querySelector('img'), null);
});

test('the composer is not focused on mount, keeps its text when the create is refused and clears it when it goes through', async () => {
  document.body.replaceChildren();
  let answer = false;
  const seen = [];
  const composer = createComposer({ onCreate: async (text) => { seen.push(text); return answer; } });
  document.body.append(composer.root);
  const ta = composer.root.querySelector('textarea');
  assert.notEqual(document.activeElement, ta);
  ta.focus();
  ta.value = 'Ship it';
  key(ta, 'Enter');
  await tick();
  assert.deepEqual(seen, ['Ship it']);
  assert.equal(ta.value, 'Ship it');
  assert.equal(document.activeElement, ta);
  answer = true;
  key(ta, 'Enter');
  await tick();
  assert.equal(ta.value, '');
});
