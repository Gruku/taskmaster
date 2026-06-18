import { test } from 'node:test';
import assert from 'node:assert/strict';

import { clusterBundles } from '../../js/lib/filters.js';

// Helper to quickly build minimal task objects
function t(id, bundle) { return { id, bundle: bundle || null }; }

test('mixed tasks: card/bundle/card/bundle → ordered output', () => {
  const a = t('a', null);
  const b = t('b', 'ux');
  const c = t('c', null);
  const d = t('d', 'ux');
  const result = clusterBundles([a, b, c, d]);
  assert.equal(result.length, 3);
  assert.deepEqual(result[0], { type: 'card', task: a });
  assert.equal(result[1].type, 'bundle');
  assert.equal(result[1].slug, 'ux');
  assert.deepEqual(result[1].tasks, [b, d]);
  assert.deepEqual(result[2], { type: 'card', task: c });
});

test('all tasks share one bundle slug → single bundle item', () => {
  const tasks = [t('a', 'x'), t('b', 'x'), t('c', 'x')];
  const result = clusterBundles(tasks);
  assert.equal(result.length, 1);
  assert.equal(result[0].type, 'bundle');
  assert.equal(result[0].slug, 'x');
  assert.deepEqual(result[0].tasks, tasks);
});

test('no bundled tasks → all card items in order', () => {
  const tasks = [t('a'), t('b'), t('c')];
  const result = clusterBundles(tasks);
  assert.equal(result.length, 3);
  assert.deepEqual(result, [
    { type: 'card', task: tasks[0] },
    { type: 'card', task: tasks[1] },
    { type: 'card', task: tasks[2] },
  ]);
});

test('single-member bundle → bundle item with 1 task', () => {
  const a = t('a', 'solo');
  const result = clusterBundles([a]);
  assert.equal(result.length, 1);
  assert.equal(result[0].type, 'bundle');
  assert.equal(result[0].slug, 'solo');
  assert.deepEqual(result[0].tasks, [a]);
});

test('two bundles interleaved [a(x), b(y), c(x)] → [bundle x:[a,c], bundle y:[b]]', () => {
  const a = t('a', 'x');
  const b = t('b', 'y');
  const c = t('c', 'x');
  const result = clusterBundles([a, b, c]);
  assert.equal(result.length, 2);
  assert.equal(result[0].type, 'bundle');
  assert.equal(result[0].slug, 'x');
  assert.deepEqual(result[0].tasks, [a, c]);
  assert.equal(result[1].type, 'bundle');
  assert.equal(result[1].slug, 'y');
  assert.deepEqual(result[1].tasks, [b]);
});

test('bundle="" is treated as non-bundle', () => {
  const tasks = [t('a', ''), t('b', undefined), t('c', null)];
  const result = clusterBundles(tasks);
  assert.equal(result.length, 3);
  result.forEach(r => assert.equal(r.type, 'card'));
});

test('empty input → empty output', () => {
  assert.deepEqual(clusterBundles([]), []);
});
