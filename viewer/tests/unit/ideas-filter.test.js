// User intent: what the Ideas list shows and in what order — "UX" and "ux" are one tag, search reaches id, title, body,
// status and tags, archived ideas only on request, and a status chip only for statuses present (or pressed).
import test from 'node:test';
import assert from 'node:assert/strict';
import { ideaMatchesSearch, applyIdeasFilters, ideaStatusChips } from '../../js/util/ideas-filter.js';
import { LIST_IDEAS } from '../mock-fixtures.js';

const ids = (list) => list.map((i) => i.id);
const live = LIST_IDEAS.filter((i) => !i.archived);

test('tags compare by key, AND across tags, newest created first', () => {
  assert.deepEqual(ids(applyIdeasFilters(LIST_IDEAS, { tags: ['ux'] })), ['IDEA-3', 'IDEA-1']);
  assert.deepEqual(ids(applyIdeasFilters(LIST_IDEAS, { tags: ['UX'] })), ['IDEA-3', 'IDEA-1']);
  assert.deepEqual(ids(applyIdeasFilters(LIST_IDEAS, { tags: ['ux', 'board'] })), ['IDEA-1']);
});

test('search is case-insensitive over id, title, body, status and tags; empty matches all', () => {
  assert.deepEqual(ids(applyIdeasFilters(LIST_IDEAS, { search: 'SWIMLANES' })), ['IDEA-1']);
  assert.deepEqual(ids(applyIdeasFilters(LIST_IDEAS, { search: 'fold a lane' })), ['IDEA-1']);
  assert.deepEqual(ids(applyIdeasFilters(LIST_IDEAS, { search: 'parking' })), ['IDEA-3']);
  assert.deepEqual(ids(applyIdeasFilters(LIST_IDEAS, { search: 'idea-2' })), ['IDEA-2']);
  assert.deepEqual(ids(applyIdeasFilters(LIST_IDEAS, { search: 'MOBILE' })), ['IDEA-3']);
  assert.equal(ideaMatchesSearch(LIST_IDEAS[3], ''), true);
  assert.equal(ideaMatchesSearch({ id: 'X' }, 'nothing'), false);
});

test('an archived idea shows only with includeArchived; statuses OR', () => {
  assert.ok(!ids(applyIdeasFilters(LIST_IDEAS)).includes('IDEA-5'));
  assert.deepEqual(ids(applyIdeasFilters(LIST_IDEAS)), ['IDEA-4', 'IDEA-3', 'IDEA-2', 'IDEA-1']);
  assert.ok(ids(applyIdeasFilters(LIST_IDEAS, { includeArchived: true })).includes('IDEA-5'));
  assert.deepEqual(ids(applyIdeasFilters(LIST_IDEAS, { statuses: ['candidate', 'exploring'] })), ['IDEA-2', 'IDEA-1']);
});

test('status chips: statuses present in IDEA_STATUS order; no status adds no chip; a pressed one absent shows at 0', () => {
  assert.deepEqual(ideaStatusChips(live, []).map((c) => c.value), ['exploring', 'candidate', 'parking-lot']);
  const chips = ideaStatusChips(live, ['dropped']);
  assert.deepEqual(chips.at(-1), { value: 'dropped', label: 'Dropped', count: 0, pressed: true });
  assert.deepEqual(chips[0], { value: 'exploring', label: 'Exploring', count: 1, pressed: false });
});

test('status chips: unknown statuses follow, alphabetically', () => {
  const chips = ideaStatusChips([{ status: 'zeta' }, { status: 'alpha' }, { status: 'candidate' }], []);
  assert.deepEqual(chips.map((c) => c.value), ['candidate', 'alpha', 'zeta']);
});
