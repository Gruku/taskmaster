// User intent: pin what the Issues board shows for a search and the chips — every field a person would search by is
// searched, severities read as words whatever their spelling, and the chip groups combine as AND across, OR within.
import test from 'node:test';
import assert from 'node:assert/strict';
import { issueSeverity, isResolvedIssue, issueMatchesSearch, filterIssues } from '../../js/util/issues-filter.js';
import { ISSUES } from '../mock-fixtures.js';

const ids = (issues) => issues.map((i) => i.id);

test('issueSeverity reads the label first, then the code, and is null when unset', () => {
  assert.equal(issueSeverity({ severity_label: 'High' }), 'high');
  assert.equal(issueSeverity({ severity: 'P0' }), 'critical');
  assert.equal(issueSeverity({ severity_label: 'Low', severity: 'P0' }), 'low');
  assert.equal(issueSeverity({ severity: 'medium' }), 'medium');
  assert.equal(issueSeverity({}), null);
  assert.equal(issueSeverity({ severity: 'whenever' }), null);
  assert.equal(issueSeverity(null), null);
});

test('isResolvedIssue: fixed, wontfix and duplicate are resolved; open and investigating are not', () => {
  for (const status of ['fixed', 'wontfix', 'duplicate']) assert.equal(isResolvedIssue({ status }), true, status);
  for (const status of ['open', 'investigating', undefined, 'weird']) assert.equal(isResolvedIssue({ status }), false, String(status));
});

test('issueMatchesSearch finds by every searchable field, case-insensitively', () => {
  const issue = {
    id: 'ISS-042', title: 'Writer mutex waits', evidence: 'Six seconds per write', component: 'store',
    location: ['taskmaster/store.py:88', 'viewer/js/main.js:12'], links: [{ type: 'relates_to', target: 'T-777' }],
  };
  for (const term of ['iss-042', 'MUTEX', 'six seconds', 'Store', 'main.js:12', 't-777']) {
    assert.equal(issueMatchesSearch(issue, term), true, term);
  }
  assert.equal(issueMatchesSearch(issue, 'nowhere'), false);
});

test('issueMatchesSearch reads the legacy symptom and legacy link fields', () => {
  assert.equal(issueMatchesSearch({ id: 'ISS-1', symptom: 'The board flickers' }, 'flickers'), true);
  assert.equal(issueMatchesSearch({ id: 'ISS-1', related_tasks: ['T-555'] }, 't-555'), true);
  assert.equal(issueMatchesSearch({ id: 'ISS-1', fixed_in_task: 'T-556' }, 'T-556'), true);
  // Typed links win over the legacy fields once there are any.
  assert.equal(issueMatchesSearch({ id: 'ISS-1', links: [{ type: 'relates_to', target: 'T-1' }], related_tasks: ['T-555'] }, 'T-555'), false);
});

test('issueMatchesSearch: a location given as one string is searched whole', () => {
  assert.equal(issueMatchesSearch({ id: 'ISS-1', location: 'viewer/js/main.js:121' }, 'main.js:121'), true);
});

test('issueMatchesSearch: an empty or blank term matches everything', () => {
  assert.equal(issueMatchesSearch({ id: 'ISS-1' }, ''), true);
  assert.equal(issueMatchesSearch({ id: 'ISS-1' }, '   '), true);
  assert.equal(issueMatchesSearch({}, undefined), true);
});

test('filterIssues: no filter keeps every issue in order', () => {
  assert.deepEqual(ids(filterIssues(ISSUES)), ids(ISSUES));
  assert.deepEqual(filterIssues(null), []);
});

test('filterIssues by severity, component and the promoted toggle', () => {
  assert.deepEqual(ids(filterIssues(ISSUES, { severities: ['high'] })), ['ISS-001', 'ISS-005', 'ISS-011', 'ISS-012', 'ISS-1234']);
  assert.deepEqual(ids(filterIssues(ISSUES, { components: ['store'] })), ['ISS-002']);
  assert.deepEqual(ids(filterIssues(ISSUES, { promotedOnly: true })), ['ISS-002']);
  assert.deepEqual(ids(filterIssues(ISSUES, { search: 'mutex' })), ['ISS-002', 'ISS-013']);
});

test('filterIssues: OR within a group, AND across groups', () => {
  assert.deepEqual(ids(filterIssues(ISSUES, { severities: ['high', 'medium'] })),
    ['ISS-001', 'ISS-003', 'ISS-005', 'ISS-007', 'ISS-009', 'ISS-011', 'ISS-012', 'ISS-1234']);
  assert.deepEqual(ids(filterIssues(ISSUES, { components: ['viewer', 'store'] })), ['ISS-001', 'ISS-002', 'ISS-003']);
  assert.deepEqual(ids(filterIssues(ISSUES, { severities: ['high', 'medium'], components: ['viewer'] })), ['ISS-001', 'ISS-003']);
  assert.deepEqual(ids(filterIssues(ISSUES, { severities: ['critical'], components: ['viewer'] })), []);
  assert.deepEqual(ids(filterIssues(ISSUES, { severities: ['critical'], promotedOnly: true, search: 'writer' })), ['ISS-002']);
});
