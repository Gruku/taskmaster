// User intent: one status language for the whole viewer — every status and priority is the same shape plus the same
// word wherever it appears, and a value the viewer has never seen still renders as plain text instead of breaking.
import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const dom = new JSDOM('<!doctype html><html><body></body></html>');
globalThis.window = dom.window;
globalThis.document = dom.window.document;

const { TASK_STATUS, PRIORITY, BUG_STATUS, IDEA_STATUS, ISSUE_STATUS, SEVERITY, severityKey, severityMeta, severityMarker, statusMeta, priorityMeta, statusMarker, priorityMarker } = await import('../../js/components/status.js');

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
// Ideas follow the same table by meaning: one being explored is in motion, a candidate or one parked is not started, a
// promoted one has moved on into its task, a dropped one is dropped.
const IDEA = {
  exploring: ['Exploring', '◐', 'accent'],
  candidate: ['Candidate', '○', 'neutral'],
  'parking-lot': ['Parking lot', '○', 'neutral'],
  promoted: ['Promoted', '→', 'neutral'],
  dropped: ['Dropped', '✕', 'neutral'],
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

test('every idea status has the shape and tone of its meaning in the spec table (§5.1), in the order a form offers them', () => {
  assert.deepEqual(Object.keys(IDEA_STATUS), Object.keys(IDEA));
  for (const [value, [label, shape, tone]] of Object.entries(IDEA)) {
    assert.deepEqual(statusMeta('idea', value), { label, shape, tone }, value);
    assertMarker(statusMarker('idea', value), { label, shape, tone });
  }
  assert.equal(statusMeta('idea', 'parking-lot').label, 'Parking lot');
  assert.throws(() => { IDEA_STATUS.exploring.label = 'x'; }, TypeError);
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
  for (const m of [...Object.values(TASK_STATUS), ...Object.values(PRIORITY), ...Object.values(BUG_STATUS), ...Object.values(IDEA_STATUS)]) assert.ok(DRAWN[m.shape], m.shape);
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

// Issues by meaning too (§5.1): an open one is not started, one under investigation is in motion, a fixed one complete,
// a won't-fix one dropped, a duplicate has moved on into the issue it duplicates. Order = the server's ISSUE_STATUSES.
const ISSUE = {
  open: ['Open', '○', 'neutral'],
  investigating: ['Investigating', '◐', 'accent'],
  fixed: ['Fixed', '●', 'success'],
  wontfix: ["Won't fix", '✕', 'neutral'],
  duplicate: ['Duplicate', '→', 'neutral'],
};

test('every issue status has the shape and tone of its meaning in the spec table (§5.1), in the server order', () => {
  assert.deepEqual(Object.keys(ISSUE_STATUS), Object.keys(ISSUE));
  for (const [value, [label, shape, tone]] of Object.entries(ISSUE)) {
    assert.deepEqual(ISSUE_STATUS[value], { label, shape, tone }, value);
    assert.deepEqual(statusMeta('issue', value), { label, shape, tone }, value);
    assertMarker(statusMarker('issue', value), { label, shape, tone });
  }
  assert.equal(statusMeta('issue', 'wontfix').label, "Won't fix");
  assert.equal(statusMeta('issue', 'duplicate').shape, '→');
  assert.throws(() => { ISSUE_STATUS.open.label = 'x'; }, TypeError);
});

test('no two issue statuses share a shape and tone', () => {
  const looks = Object.values(ISSUE_STATUS).map((m) => `${m.shape} ${m.tone}`);
  assert.equal(new Set(looks).size, looks.length);
  for (const m of Object.values(ISSUE_STATUS)) assert.ok(TONES.includes(m.tone), m.tone);
});

test('a severity looks exactly like the priority of the same name', () => {
  assert.deepEqual(SEVERITY, PRIORITY);
  assert.deepEqual(Object.keys(SEVERITY), Object.keys(PRIORITY));
});

test('severityKey reads a severity in any form it arrives in, and nothing else', () => {
  const cases = [
    ['P0', 'critical'], ['p1', 'high'], ['P2', 'medium'], ['p3', 'low'], ['High', 'high'], [' medium ', 'medium'],
    ['critical', 'critical'], ['LOW', 'low'], ['', null], ['   ', null], [null, null], [undefined, null], ['P5', null],
    [7, null], [{}, null], ['constructor', null], ['__proto__', null],
  ];
  for (const [value, key] of cases) assert.equal(severityKey(value), key, JSON.stringify(value));
});

test('severityMeta: a known severity is a copy of its row, an unknown word stays itself, no severity is null', () => {
  const p1 = severityMeta('P1');
  assert.deepEqual(p1, { label: 'High', shape: '▲', tone: 'orange' });
  p1.label = 'Mutated';
  assert.equal(SEVERITY.high.label, 'High');
  assert.equal(severityMeta('high').label, 'High');
  assert.deepEqual(severityMeta('P5'), { label: 'P5', shape: '○', tone: 'neutral' });
  assert.deepEqual(severityMeta('  blocker '), { label: 'blocker', shape: '○', tone: 'neutral' });
  for (const none of ['', '   ', null, undefined, 7, {}]) assert.equal(severityMeta(none), null, JSON.stringify(none));
});

test('severityMarker is the marker of its meta, or null when no severity is set', () => {
  const el = severityMarker('P0');
  assertMarker(el, { label: 'Critical', shape: '◆', tone: 'critical' });
  assert.ok(el.matches('span.marker.marker--critical'));
  assert.ok(el.querySelector('.marker__shape[data-shape="diamond"][aria-hidden="true"]'));
  assert.equal(severityMarker(null), null);
  assert.equal(severityMarker(undefined), null);
  assert.equal(severityMarker(''), null);
  assert.equal(severityMarker('  '), null);
});

test('a severity that carries markup is shown as text, never parsed', () => {
  const hostile = '<img src=x onerror=alert(1)>';
  const el = severityMarker(hostile);
  assert.equal(el.querySelector('.marker__word').textContent, hostile);
  assert.equal(el.querySelector('img'), null);
  assert.equal(el.className, 'marker marker--neutral');
});
