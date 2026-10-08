// User intent: pin what the Bugs list shows — every status reachable (fixed and adopted bugs included), archived bugs
// only behind their toggle, the saved filter read in its old and new shapes, and each sort in the order a person expects.
import test from 'node:test';
import assert from 'node:assert/strict';
import { BUG_SORTS, isArchivedBug, bugPrefs, filterBugs, sortBugs, bugStatusChips } from '../../js/util/bugs-filter.js';
import { BUGS } from '../mock-fixtures.js';

const ids = (bugs) => bugs.map((b) => b.id);
const nonArchived = BUGS.filter((b) => !b.archived);

test('BUG_SORTS names the four sorts in order', () => {
  assert.deepEqual(BUG_SORTS.map((s) => s.value), ['newest', 'oldest', 'severity', 'id']);
  assert.deepEqual(BUG_SORTS.map((s) => s.label), ['Newest first', 'Oldest first', 'Severity', 'ID']);
});

test('isArchivedBug: the flag or the status', () => {
  assert.equal(isArchivedBug({ archived: true, status: 'fixed' }), true);
  assert.equal(isArchivedBug({ status: 'archived' }), true);
  assert.equal(isArchivedBug({ status: 'open' }), false);
  assert.equal(isArchivedBug({ archived: 'yes', status: 'open' }), false);
});

test('bugPrefs: nothing saved is Open and Shelved, archived off, newest first', () => {
  const want = { statuses: ['open', 'shelved'], archived: false, sort: 'newest' };
  assert.deepEqual(bugPrefs(undefined), want);
  assert.deepEqual(bugPrefs(null), want);
  assert.deepEqual(bugPrefs({}), want);
});

test('bugPrefs: the legacy filters object is read once', () => {
  assert.deepEqual(bugPrefs({ filters: { open: true, shelved: false, archive: true } }), { statuses: ['open'], archived: true, sort: 'newest' });
});

test('bugPrefs: saved statuses are kept and an unknown sort is newest', () => {
  assert.deepEqual(bugPrefs({ statuses: ['fixed'], sort: 'bogus' }), { statuses: ['fixed'], archived: false, sort: 'newest' });
  assert.deepEqual(bugPrefs({ statuses: [], archived: true, sort: 'id' }), { statuses: [], archived: true, sort: 'id' });
  // New shape wins over a legacy key left behind in the merged prefs.
  assert.deepEqual(bugPrefs({ statuses: ['adopted'], filters: { open: true } }).statuses, ['adopted']);
});

test('filterBugs: archived bugs only when asked; fixed bugs are there', () => {
  const all = ids(filterBugs(BUGS));
  assert.ok(!all.includes('B-026'));
  assert.ok(all.includes('B-029'));
  assert.ok(ids(filterBugs(BUGS, { archived: true })).includes('B-026'));
});

test('filterBugs: a bug whose status is archived matches no chip, so Show archived admits it whatever chips are pressed', () => {
  const withStatusArchived = [...BUGS, { id: 'B-040', title: 'Retired', status: 'archived' }];
  const statuses = ['open', 'shelved'];
  assert.ok(!ids(filterBugs(withStatusArchived, { statuses })).includes('B-040'));
  assert.ok(ids(filterBugs(withStatusArchived, { statuses, archived: true })).includes('B-040'));
  // The toggle widens only to archived bugs: a fixed bug still answers to the chips.
  assert.ok(!ids(filterBugs(withStatusArchived, { statuses, archived: true })).includes('B-029'));
});

test('filterBugs: statuses narrow, empty means every status', () => {
  assert.deepEqual(ids(filterBugs(BUGS, { statuses: ['open'] })), ['B-031', 'B-032', 'B-1234']);
  assert.equal(filterBugs(BUGS, { statuses: [] }).length, 7);
});

test('filterBugs: search is trimmed, case-insensitive, over id, title and components', () => {
  assert.deepEqual(ids(filterBugs(BUGS, { search: '  STORE ' })), ['B-029']);
  assert.deepEqual(ids(filterBugs(BUGS, { search: 'b-028' })), ['B-028']);
  assert.deepEqual(ids(filterBugs(BUGS, { search: 'phase strip' })), ['B-030']);
});

test('sortBugs: severity is critical → low → unset, each then newest', () => {
  assert.deepEqual(ids(sortBugs(nonArchived, 'severity')), ['B-029', 'B-031', 'B-1234', 'B-027', 'B-028', 'B-030', 'B-032']);
});

test('sortBugs: newest and oldest by discovered, undated last; returns a new array', () => {
  const bugs = [
    { id: 'B-1', discovered: '2026-10-01T00:00:00Z' },
    { id: 'B-2' },
    { id: 'B-3', discovered: '2026-10-03T00:00:00Z' },
    { id: 'B-4' },
  ];
  const out = sortBugs(bugs, 'newest');
  assert.notEqual(out, bugs);
  assert.deepEqual(ids(out), ['B-3', 'B-1', 'B-4', 'B-2']);
  assert.deepEqual(ids(sortBugs(bugs, 'oldest')), ['B-1', 'B-3', 'B-4', 'B-2']);
  assert.deepEqual(ids(bugs), ['B-1', 'B-2', 'B-3', 'B-4']);
});

test('sortBugs: id is numeric-aware', () => {
  assert.deepEqual(ids(sortBugs([{ id: 'B-10' }, { id: 'B-9' }, { id: 'B-100' }], 'id')), ['B-9', 'B-10', 'B-100']);
});

test('bugStatusChips: present statuses in table order, a pressed absent one at 0', () => {
  const chips = bugStatusChips(nonArchived, ['promoted']);
  assert.deepEqual(chips.map((c) => c.value), ['open', 'fixed', 'adopted', 'promoted', 'shelved']);
  assert.deepEqual(chips.find((c) => c.value === 'promoted'), { value: 'promoted', label: 'Promoted', count: 0, pressed: true });
  assert.deepEqual(chips.find((c) => c.value === 'open'), { value: 'open', label: 'Open', count: 3, pressed: false });
});

test('bugStatusChips: unknown statuses follow alphabetically; archived is never a chip', () => {
  const chips = bugStatusChips([{ status: 'zeta' }, { status: 'open' }, { status: 'alpha' }, { status: 'archived' }, {}], []);
  assert.deepEqual(chips.map((c) => c.value), ['open', 'alpha', 'zeta']);
  assert.equal(chips[0].count, 2);   // no status reads as open
  assert.equal(chips[1].label, 'alpha');
});
