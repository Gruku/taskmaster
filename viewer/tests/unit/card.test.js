// User intent: a Kanban card is one real link named by its id and title, with its copy and doc buttons beside the link
// and never inside it; states read as markers, the epic as swatch + name, and no glyph or inline style is left over.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const { window } = new JSDOM('<!DOCTYPE html><body></body>');
globalThis.document = window.document;

import { renderCard } from '../../js/components/card.js';

const GLYPHS = ['⧉', '⎇', '⬢', '⛔', '↳', '▤', '▦', '‹', '›', '⌫', '⚲', '★', '☆', '↗', '▾', '📄'];
const NOW = Date.parse('2026-10-06T12:00:00Z');
const TASK = {
  id: 'T-102', title: 'Re-skin the board', status: 'in-review', priority: 'critical', epic: 'viewer',
  branch: 'feat/x', docs: { spec: 'a.md' }, human_action: 'add the key',
};
const INDEX = new Map([['viewer', { name: 'Viewer re-skin', swatch: 1 }]]);
const card = (task = {}, opts = {}) => renderCard({ task: { ...TASK, ...task }, epicIndex: INDEX, now: NOW, ...opts });
const link = (el) => el.querySelector(':scope > a.link-row__link');

test('the root is a link row: no role, no tabindex, no style; it carries the task id and density', () => {
  const el = card();
  assert.ok(el.matches('div.card-task.link-row.full[data-task-id="T-102"]'));
  for (const attr of ['role', 'tabindex', 'style']) assert.equal(el.hasAttribute(attr), false, attr);
  assert.ok(card({}, { density: 'minimal' }).matches('div.card-task.link-row.minimal'));
});

test('the link opens the task and is named "<id> <title>", with the full title in its title', () => {
  const a = link(card());
  assert.equal(a.getAttribute('href'), '#/task/T-102');
  assert.equal(a.textContent.replace(/\s+/g, ' ').trim(), 'T-102 Re-skin the board');
  assert.equal(a.querySelector('.card-sr').textContent, 'T-102 ');
  assert.equal(a.title.split('\n')[0], 'Re-skin the board');
  assert.equal(a.dataset.focus, 'link');
  assert.equal(a.querySelector('a, button, input, [tabindex]'), null);
});

test('content: priority marker, epic swatch + name, the note with its marker', () => {
  const el = card();
  assert.equal(el.querySelector('.card-pri .marker__word').textContent, 'Critical');
  assert.equal(el.querySelector('.card-epic').textContent, 'Viewer re-skin');
  assert.ok(el.querySelector('.card-epic .card-swatch--cat-1'));
  const note = el.querySelector('.card-note');
  assert.match(note.textContent, /Waiting on you/);
  assert.match(note.textContent, /add the key/);
});

test('controls are buttons beside the link: copy id, copy branch, open doc', () => {
  const el = card();
  const id = el.querySelector('button.card-id[type="button"][data-focus="copy-id"]');
  assert.equal(id.getAttribute('aria-label'), 'Copy id T-102');
  assert.ok(id.querySelector('.truncate') && id.querySelector('svg.icon'));
  const branch = el.querySelector('button.card-branch[data-focus="copy-branch"]');
  assert.equal(branch.getAttribute('aria-label'), 'Copy branch feat/x');
  const docs = el.querySelector('button.btn.btn--ghost.btn--icon.btn--sm.card-docs[data-focus="docs"]');
  assert.equal(docs.getAttribute('aria-label'), 'Open primary doc');
  assert.ok(docs.querySelector('svg.icon'));
  for (const b of [id, branch, docs]) assert.equal(link(el).contains(b), false);
});

test('an epic the index does not know shows its id and a neutral swatch', () => {
  const el = card({ epic: 'zz' });
  assert.equal(el.querySelector('.card-epic').textContent, 'zz');
  assert.ok(el.querySelector('.card-epic .card-swatch--none'));
});

test('no priority → no priority slot; no branch → no branch button', () => {
  assert.equal(card({ priority: undefined }).querySelector('.card-pri'), null);
  assert.equal(card({ branch: undefined }).querySelector('.card-branch'), null);
});

test('grouped by phase the card names its status with a marker', () => {
  const words = [...card({}, { groupBy: 'phase' }).querySelectorAll('.marker__word')].map((w) => w.textContent);
  assert.ok(words.includes('In review'), words.join());
  const minimal = [...card({}, { groupBy: 'phase', density: 'minimal' }).querySelectorAll('.marker__word')].map((w) => w.textContent);
  assert.ok(minimal.includes('In review'), minimal.join());
  const byStatus = [...card().querySelectorAll('.marker__word')].map((w) => w.textContent);
  assert.equal(byStatus.includes('In review'), false);
});

test('a 120-character title is clamped to three lines with the full text in its title', () => {
  const long = 'x'.repeat(60) + ' ' + 'y'.repeat(59);
  assert.equal(long.length, 120);
  const t = card({ title: long }).querySelector('.card-title');
  assert.ok(t.classList.contains('truncate--3'));
  assert.equal(t.title, long);
});

test('no glyph from the 3a list is in the card', () => {
  const html = card({ bundle: 'asset-ux', depends_on: ['T-1'], status: 'blocked', blockers_count: 2, spec_review: 'pass' }).outerHTML;
  for (const g of GLYPHS) assert.equal(html.includes(g), false, g);
});

test('a task started 2 hours ago is recent with a "New" tag in both densities; 2 days ago neither (KB-11)', () => {
  const hours = (h) => new Date(NOW - h * 3_600_000).toISOString();
  for (const density of ['full', 'minimal']) {
    const fresh = card({ started: hours(2) }, { density });
    assert.ok(fresh.classList.contains('recent'), density);
    assert.equal(fresh.querySelector('.card-new')?.textContent, 'New', density);
    const old = card({ started: hours(48) }, { density });
    assert.equal(old.classList.contains('recent'), false, density);
    assert.equal(old.querySelector('.card-new'), null, density);
  }
});

test('age: Technical slot with its anchor in title; stale after four days', () => {
  const age = card({ started: new Date(NOW - 5 * 86_400_000).toISOString() }).querySelector('.card-age');
  assert.equal(age.textContent, '5d');
  assert.match(age.title, /^Since /);
  assert.ok(age.classList.contains('card-age--stale'));
});

test('a blocked task names how many block it', () => {
  const note = card({ status: 'blocked', blockers_count: 2, human_action: undefined }).querySelector('.card-note');
  assert.equal(note.querySelector('.marker__word').textContent, 'Blocked by 2');
});

test('the spec review is a marker word', () => {
  const words = (v) => [...card({ spec_review: { verdict: v } }).querySelectorAll('.card-tags .marker__word')].map((w) => w.textContent);
  assert.deepEqual(words('pass'), ['Spec passed']);
  assert.deepEqual(words('warn'), ['Spec warning']);
  assert.deepEqual(words('fail'), ['Spec failed']);
});

test('the link carries the titles its hit area hides: the full title, the epic name and the age\'s date', () => {
  const long = 'An epic name long enough to be cut on any card in any column';
  const el = renderCard({
    task: { ...TASK, epic: 'big', started: new Date(NOW - 5 * 86_400_000).toISOString(), tracker_id: 'linear-cm-eng-42' },
    epicIndex: new Map([['big', { name: long, swatch: 2 }]]), now: NOW,
  });
  const lines = link(el).title.split('\n');
  assert.equal(lines[0], 'Re-skin the board');
  assert.ok(lines.includes(long), lines.join(' | '));
  assert.ok(lines.includes(el.querySelector('.card-age').title), lines.join(' | '));
  assert.match(lines.join('\n'), /^Since /m);
  assert.ok(lines.includes('linear-cm-eng-42'), lines.join(' | '));
});

test('gate_state is said in words, never as the raw string; a malformed one shows nothing', () => {
  const pending = card({ gate_state: 'review-gate:pending' });
  assert.equal(pending.querySelector('.card-gate').textContent, 'Review gate — pending');
  assert.equal(pending.outerHTML.includes('review-gate:pending'), false);
  assert.equal(card({ gate_state: 'blocked@plan-review' }).querySelector('.card-gate').textContent, 'Blocked at Plan review');
  assert.equal(card({ gate_state: 'whatever' }).querySelector('.card-gate'), null);
});

test('a blocked card says "Blocked by n" once, with no second unmet count', () => {
  const el = card({ status: 'blocked', blockers_count: 1, depends_on_unmet_count: 1, depends_on: ['T-1'], human_action: undefined });
  assert.equal(el.querySelector('.card-note .marker__word').textContent, 'Blocked by 1');
  assert.equal(el.querySelector('.card-deps'), null);
  assert.equal(card({ status: 'todo', depends_on_unmet_count: 2 }).querySelector('.card-deps').textContent, '2 unmet');
});

test('epic and estimate share a non-wrapping group', () => {
  const lead = card({ estimate: 'M' }).querySelector('.card-tags__lead');
  assert.deepEqual([...lead.children].map((c) => c.className), ['card-tag card-epic', 'card-tag card-estimate']);
});

test('the merge dots say the rung reached in words for assistive tech', () => {
  const ladder = card({ merge_gate_state: 'stage' }).querySelector('.ml-compact');
  assert.equal(ladder.querySelector('.card-sr').textContent, 'Merged to stage, 2 of 3');
  for (const d of ladder.querySelectorAll('.ml-dot')) assert.equal(d.getAttribute('aria-hidden'), 'true');
});
