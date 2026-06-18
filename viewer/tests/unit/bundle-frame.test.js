import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
const { window } = new JSDOM('<!DOCTYPE html><body></body>');
globalThis.document = window.document;

import { renderBundleFrame } from '../../js/components/bundle-frame.js';

function makeTask(id, lane) {
  return { id, title: `Task ${id}`, status: 'todo', bundle: 'test-slug', lane: lane || null };
}

test('renders header with slug text and ⬢ glyph', () => {
  const el = renderBundleFrame(
    { slug: 'asset-ux', tasks: [makeTask('t-1')], total: 1 },
    { density: 'full', epicColors: {}, groupBy: 'status' }
  );
  const head = el.querySelector('.bundle-frame-head');
  assert.ok(head, 'bundle-frame-head missing');
  assert.match(head.textContent, /⬢/);
  assert.match(head.textContent, /asset-ux/);
});

test('tasks.length < total → "N of M here" count text', () => {
  const tasks = [makeTask('t-1'), makeTask('t-2')];
  const el = renderBundleFrame(
    { slug: 'my-bundle', tasks, total: 4 },
    { density: 'full', epicColors: {}, groupBy: 'status' }
  );
  assert.match(el.outerHTML, /2 of 4 here/);
});

test('tasks.length === total → "N tasks" count text', () => {
  const tasks = [makeTask('t-1'), makeTask('t-2'), makeTask('t-3'), makeTask('t-4')];
  const el = renderBundleFrame(
    { slug: 'my-bundle', tasks, total: 4 },
    { density: 'full', epicColors: {}, groupBy: 'status' }
  );
  assert.match(el.outerHTML, /4 tasks/);
});

test('total falsy → "N tasks" count text', () => {
  const tasks = [makeTask('t-1'), makeTask('t-2')];
  const el = renderBundleFrame(
    { slug: 'my-bundle', tasks, total: 0 },
    { density: 'full', epicColors: {}, groupBy: 'status' }
  );
  assert.match(el.outerHTML, /2 tasks/);
});

test('strictest lane shown: [express, full] → FULL in header', () => {
  const tasks = [
    { id: 't-1', title: 'A', status: 'todo', bundle: 'b', lane: 'express' },
    { id: 't-2', title: 'B', status: 'todo', bundle: 'b', lane: 'full' },
  ];
  const el = renderBundleFrame(
    { slug: 'b', tasks, total: 2 },
    { density: 'full', epicColors: {}, groupBy: 'status' }
  );
  const head = el.querySelector('.bundle-frame-head');
  assert.match(head.textContent, /FULL/);
});

test('no lane on any member → no lane element in header', () => {
  const tasks = [makeTask('t-1'), makeTask('t-2')];
  const el = renderBundleFrame(
    { slug: 'b', tasks, total: 2 },
    { density: 'full', epicColors: {}, groupBy: 'status' }
  );
  const laneEl = el.querySelector('.bundle-frame-head .lane');
  assert.equal(laneEl, null, 'lane element should not exist when no lane');
});

test('contains one .card-task per member task', () => {
  const tasks = [makeTask('t-1'), makeTask('t-2'), makeTask('t-3')];
  const el = renderBundleFrame(
    { slug: 'b', tasks, total: 3 },
    { density: 'full', epicColors: {}, groupBy: 'status' }
  );
  const cards = el.querySelectorAll('.card-task');
  assert.equal(cards.length, 3);
});

test('same slug yields same bh- class across two calls (stable hue)', () => {
  const opts = { density: 'full', epicColors: {}, groupBy: 'status' };
  const el1 = renderBundleFrame({ slug: 'my-slug', tasks: [makeTask('t-1')], total: 1 }, opts);
  const el2 = renderBundleFrame({ slug: 'my-slug', tasks: [makeTask('t-2')], total: 1 }, opts);
  const bh1 = [...el1.classList].find(c => c.startsWith('bh-'));
  const bh2 = [...el2.classList].find(c => c.startsWith('bh-'));
  assert.ok(bh1, 'no bh- class on el1');
  assert.equal(bh1, bh2, 'bh- class differs across calls with same slug');
});

test('bh- class is in range bh-1..bh-6', () => {
  const opts = { density: 'full', epicColors: {}, groupBy: 'status' };
  const el = renderBundleFrame({ slug: 'test', tasks: [makeTask('t-1')], total: 1 }, opts);
  const bh = [...el.classList].find(c => c.startsWith('bh-'));
  assert.ok(/^bh-[1-6]$/.test(bh), `bh class out of range: ${bh}`);
});

test('cards inside bundle frame do NOT contain .card-bundle-chip (hideBundleChip propagated)', () => {
  const tasks = [makeTask('t-1'), makeTask('t-2')];
  const el = renderBundleFrame(
    { slug: 'b', tasks, total: 2 },
    { density: 'full', epicColors: {}, groupBy: 'status' }
  );
  const chips = el.querySelectorAll('.card-bundle-chip');
  assert.equal(chips.length, 0, 'bundle chips should be hidden inside bundle frame');
});
