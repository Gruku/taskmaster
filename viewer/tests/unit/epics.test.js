import test from 'node:test';
import assert from 'node:assert/strict';
import { EPIC_PALETTE, assignEpicColors, epicColor, epicCssVar } from '../../js/lib/epics.js';

test('EPIC_PALETTE is the locked spec §5 palette', () => {
  assert.deepEqual(EPIC_PALETTE, [
    '#6ea8ff', '#b585e8', '#5fcdb8', '#e8a34d', '#e87a85', '#a8c958',
  ]);
});

test('assignEpicColors — auto-assigns palette in order, wraps after 6', () => {
  const epics = [
    { id: 'viewer-redesign', name: 'viewer-redesign' },
    { id: 'narrative-continuity', name: 'narrative-continuity' },
    { id: 'filter-bar', name: 'filter-bar' },
    { id: 'migration-tooling', name: 'migration-tooling' },
    { id: 'blast-radius', name: 'blast-radius' },
    { id: 'spec-review', name: 'spec-review' },
    { id: 'extra-7', name: 'extra-7' },
  ];
  const map = assignEpicColors(epics);
  assert.equal(map['viewer-redesign'], '#6ea8ff');
  assert.equal(map['narrative-continuity'], '#b585e8');
  assert.equal(map['spec-review'],     '#a8c958');
  assert.equal(map['extra-7'],         '#6ea8ff');
});

test('assignEpicColors — respects explicit color on epic record', () => {
  const epics = [
    { id: 'a', color: '#abcdef' },
    { id: 'b', name: 'b' },
  ];
  const map = assignEpicColors(epics);
  assert.equal(map['a'], '#abcdef');
  assert.equal(map['b'], '#6ea8ff');
});

test('epicColor — returns assigned color or fallback ink-3', () => {
  const map = { foo: '#6ea8ff' };
  assert.equal(epicColor('foo', map), '#6ea8ff');
  assert.equal(epicColor('missing', map), '#7c8290');
  assert.equal(epicColor(null, map), '#7c8290');
});

test('epicCssVar — returns inline style with --epic and --epic-soft', () => {
  const style = epicCssVar('#6ea8ff');
  assert.match(style, /--epic:\s*#6ea8ff/);
  assert.match(style, /--epic-soft:\s*rgba\(110, ?168, ?255, ?0\.14\)/);
});

test('epicCssVar — null color falls back to ink-3', () => {
  const style = epicCssVar(null);
  assert.match(style, /--epic:\s*#7c8290/);
});
