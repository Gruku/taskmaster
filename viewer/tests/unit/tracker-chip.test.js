import test from 'node:test';
import assert from 'node:assert/strict';
import { parseTrackerId } from '../../js/components/card.js';

test('parseTrackerId — linear with single-segment key', () => {
  assert.deepEqual(parseTrackerId('linear-cm-eng42'), { system: 'linear', alias: 'cm', key: 'ENG42' });
});

test('parseTrackerId — linear with hyphenated key (ENG-42)', () => {
  assert.deepEqual(parseTrackerId('linear-cm-eng-42'), { system: 'linear', alias: 'cm', key: 'ENG-42' });
});

test('parseTrackerId — jira with multi-segment alias and key', () => {
  assert.deepEqual(parseTrackerId('jira-acme-proj-123'), { system: 'jira', alias: 'acme', key: 'PROJ-123' });
});

test('parseTrackerId — uppercases the display key', () => {
  const out = parseTrackerId('linear-cm-foo-bar-baz');
  assert.equal(out.key, 'FOO-BAR-BAZ');
});

test('parseTrackerId — null / empty / non-string → null', () => {
  assert.equal(parseTrackerId(null), null);
  assert.equal(parseTrackerId(''), null);
  assert.equal(parseTrackerId(undefined), null);
  assert.equal(parseTrackerId(42), null);
});

test('parseTrackerId — fewer than three segments → null', () => {
  assert.equal(parseTrackerId('linear'), null);
  assert.equal(parseTrackerId('linear-cm'), null);
});

test('parseTrackerId — empty middle component → null', () => {
  assert.equal(parseTrackerId('linear--eng-1'), null);
});
