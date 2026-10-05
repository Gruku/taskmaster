// User intent: one status language for the whole viewer — every status and priority is the same shape plus the same
// word wherever it appears, and a value the viewer has never seen still renders as plain text instead of breaking.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const { TASK_STATUS, PRIORITY, BUG_STATUS, statusMeta, priorityMeta, statusMarker, priorityMarker } = await import('../../js/components/status.js');

const TASK = {
  todo: ['Todo', '○', 'neutral'],
  'in-progress': ['In progress', '◐', 'accent'],
  'in-review': ['In review', '▲', 'warning'],
  blocked: ['Blocked', '◆', 'critical'],
  done: ['Done', '●', 'success'],
  archived: ['Archived', '✕', 'neutral'],
};
const PRIO = {
  critical: ['Critical', '◆', 'critical'],
  high: ['High', '▲', 'orange'],
  medium: ['Medium', '●', 'warning'],
  low: ['Low', '○', 'neutral'],
};
// Spec §5.1, by meaning: not started ○ subtle, complete ● success, moved on → subtle, dropped ✕ subtle. An open bug
// is not started; it does not borrow the blocked tone — the "N open bugs blocking close" line carries the alarm.
// "adopted" (taken into a task) has moved on into that task, as "promoted" has into an issue.
const BUG = {
  open: ['Open', '○', 'neutral'],
  fixed: ['Fixed', '●', 'success'],
  adopted: ['Adopted', '→', 'neutral'],
  promoted: ['Promoted', '→', 'neutral'],
  shelved: ['Shelved', '✕', 'neutral'],
  archived: ['Archived', '✕', 'neutral'],
};
const TONES = ['neutral', 'accent', 'warning', 'critical', 'success', 'orange'];
// None of the viewer's local fonts carries these glyphs, so each one is also named for the stylesheet to draw.
const DRAWN = { '○': 'ring', '◐': 'half', '▲': 'triangle', '◆': 'diamond', '●': 'dot', '→': 'arrow', '✕': 'cross' };
const HOSTILE = '<img src=x onerror=x>';

test('every task status has the label, shape and tone of the spec table', () => {
  assert.deepEqual(Object.keys(TASK_STATUS), Object.keys(TASK));
  for (const [value, [label, shape, tone]] of Object.entries(TASK)) {
    assert.deepEqual(statusMeta('task', value), { label, shape, tone }, value);
    assert.deepEqual(TASK_STATUS[value], { label, shape, tone }, value);
  }
});

test('every bug status has the shape and tone of its meaning in the spec table (§5.1)', () => {
  assert.deepEqual(Object.keys(BUG_STATUS).sort(), Object.keys(BUG).sort());
  for (const [value, [label, shape, tone]] of Object.entries(BUG)) {
    assert.deepEqual(statusMeta('bug', value), { label, shape, tone }, value);
    assertMarker(statusMarker('bug', value), { label, shape, tone });
  }
});

test('every priority has its full word, shape and tone', () => {
  assert.deepEqual(Object.keys(PRIORITY), Object.keys(PRIO));
  for (const [value, [label, shape, tone]] of Object.entries(PRIO)) {
    assert.deepEqual(priorityMeta(value), { label, shape, tone }, value);
    assert.deepEqual(PRIORITY[value], { label, shape, tone }, value);
  }
});

test('no two task statuses and no two priorities share a shape and tone: Done never looks like In progress', () => {
  for (const table of [TASK_STATUS, PRIORITY]) {
    const looks = Object.values(table).map((m) => `${m.shape} ${m.tone}`);
    assert.equal(new Set(looks).size, looks.length);
    for (const m of Object.values(table)) assert.ok(TONES.includes(m.tone), m.tone);
  }
});

test('an unknown value falls back to a neutral marker with the value as its label, without throwing', () => {
  const neutral = (label) => ({ label, shape: '○', tone: 'neutral' });
  for (const meta of [(v) => statusMeta('task', v), priorityMeta]) {
    assert.deepEqual(meta('someday'), neutral('someday'));
    assert.deepEqual(meta(null), neutral('—'));
    assert.deepEqual(meta(undefined), neutral('—'));
    assert.deepEqual(meta(''), neutral('—'));
    assert.deepEqual(meta(7), neutral('7'));
    assert.deepEqual(meta({ a: 1 }), neutral('[object Object]'));
    // Names every object carries are still unknown statuses.
    for (const inherited of ['constructor', 'toString', '__proto__', 'hasOwnProperty']) {
      assert.deepEqual(meta(inherited), neutral(inherited));
    }
  }
  assert.deepEqual(statusMeta('no-such-kind', 'done'), neutral('done'), 'an unknown kind knows no statuses');
  assert.deepEqual(statusMeta(undefined, 'done'), neutral('done'));
});

test('lookups return a copy: a caller cannot rewrite the table', () => {
  statusMeta('task', 'done').label = 'Mutated';
  priorityMeta('high').label = 'Mutated';
  assert.equal(statusMeta('task', 'done').label, 'Done');
  assert.equal(priorityMeta('high').label, 'High');
  assert.throws(() => { TASK_STATUS.done.label = 'x'; }, TypeError);
  assert.throws(() => { PRIORITY.high = null; }, TypeError);
});

function assertMarker(el, { label, shape, tone }) {
  assert.equal(el.tagName, 'SPAN');
  assert.equal(el.className, `marker marker--${tone}`);
  assert.equal(el.children.length, 2);
  const [shapeEl, wordEl] = el.children;
  assert.equal(shapeEl.tagName, 'SPAN');
  assert.equal(shapeEl.className, 'marker__shape');
  assert.equal(shapeEl.getAttribute('aria-hidden'), 'true');
  assert.equal(shapeEl.textContent, shape);
  assert.equal(shapeEl.dataset.shape, DRAWN[shape], 'the shape is named so CSS can draw it without a font');
  assert.equal(wordEl.tagName, 'SPAN');
  assert.equal(wordEl.className, 'marker__word');
  assert.equal(wordEl.textContent, label);
}

test('statusMarker builds a shape hidden from assistive tech plus the word', () => {
  for (const [value, [label, shape, tone]] of Object.entries(TASK)) {
    assertMarker(statusMarker('task', value), { label, shape, tone });
  }
  assertMarker(statusMarker('task', null), { label: '—', shape: '○', tone: 'neutral' });
  assertMarker(statusMarker('task', 3), { label: '3', shape: '○', tone: 'neutral' });
});

test('priorityMarker builds the same structure', () => {
  for (const [value, [label, shape, tone]] of Object.entries(PRIO)) {
    assertMarker(priorityMarker(value), { label, shape, tone });
  }
  assertMarker(priorityMarker(undefined), { label: '—', shape: '○', tone: 'neutral' });
});

test('every shape in the tables has a drawn form', () => {
  for (const m of [...Object.values(TASK_STATUS), ...Object.values(PRIORITY), ...Object.values(BUG_STATUS)]) assert.ok(DRAWN[m.shape], m.shape);
});

test('a status that carries markup is shown as text, never parsed', () => {
  for (const el of [statusMarker('task', HOSTILE), priorityMarker(HOSTILE)]) {
    assert.equal(el.querySelector('.marker__word').textContent, HOSTILE);
    assert.equal(el.querySelector('img'), null);
    assert.equal(el.querySelectorAll('*').length, 2, 'only the shape and the word');
    assert.equal(el.className, 'marker marker--neutral', 'the value never reaches a class name');
  }
});

test('the stylesheet draws every named shape, the arrow included', async () => {
  const { readFileSync } = await import('node:fs');
  const css = readFileSync(new URL('../../css/components/status.css', import.meta.url), 'utf8');
  for (const name of Object.values(DRAWN)) {
    assert.ok(css.includes(`[data-shape="${name}"]`), name);
  }
});
