import { test } from 'node:test';
import assert from 'node:assert/strict';
import { computeComponentLayout } from '../../js/components/component-graph-layout.js';

const linear = {
  components: {
    ingest: { title: 'Ingest', after: [] },
    thumb:  { title: 'Thumbnailer', after: ['ingest'] },
    cdn:    { title: 'CDN delivery', after: ['thumb'] },
  },
  rollup: {
    ingest: { status: 'done', total: 2, done: 2 },
    thumb:  { status: 'in-progress', total: 3, done: 1 },
    cdn:    { status: 'todo', total: 0, done: 0 },
  },
};

test('linear chain ranks left→right by dependency depth', () => {
  const out = computeComponentLayout(linear);
  const byId = Object.fromEntries(out.nodes.map(n => [n.id, n]));
  assert.equal(byId.ingest.rank, 0);
  assert.equal(byId.thumb.rank, 1);
  assert.equal(byId.cdn.rank, 2);
  assert.ok(byId.ingest.x < byId.thumb.x);
  assert.ok(byId.thumb.x < byId.cdn.x);
});

test('edges point dependency → dependent and carry an SVG bezier path', () => {
  const out = computeComponentLayout(linear);
  const e = out.edges.find(e => e.from === 'ingest' && e.to === 'thumb');
  assert.ok(e, 'ingest→thumb edge exists');
  assert.match(e.path, /^M [\d.]+ [\d.]+ C/);
  assert.equal(out.edges.length, 2);
});

test('node carries rollup status + count for coloring', () => {
  const out = computeComponentLayout(linear);
  const ingest = out.nodes.find(n => n.id === 'ingest');
  assert.equal(ingest.status, 'done');
  assert.equal(ingest.total, 2);
  assert.equal(ingest.count, 2);
});

test('diamond: two parents of a sink share a rank, sink is one rank deeper', () => {
  const out = computeComponentLayout({
    components: {
      a: { title: 'A', after: [] },
      b: { title: 'B', after: ['a'] },
      c: { title: 'C', after: ['a'] },
      d: { title: 'D', after: ['b', 'c'] },
    },
    rollup: {},
  });
  const r = Object.fromEntries(out.nodes.map(n => [n.id, n.rank]));
  assert.equal(r.a, 0);
  assert.equal(r.b, 1);
  assert.equal(r.c, 1);
  assert.equal(r.d, 2);
  const col1 = out.nodes.filter(n => n.rank === 1).sort((x, y) => x.y - y.y);
  assert.equal(col1.length, 2);
  assert.ok(col1[0].y + col1[0].h <= col1[1].y, 'same-rank siblings must not overlap');
});

test('missing rollup defaults status to todo, never throws', () => {
  const out = computeComponentLayout({
    components: { x: { title: 'X', after: [] } }, rollup: {},
  });
  assert.equal(out.nodes[0].status, 'todo');
});

test('cycle falls back gracefully (no infinite loop, all nodes placed)', () => {
  const out = computeComponentLayout({
    components: {
      p: { title: 'P', after: ['q'] },
      q: { title: 'Q', after: ['p'] },
    },
    rollup: {},
  });
  assert.equal(out.nodes.length, 2, 'both nodes still placed');
  for (const n of out.nodes) assert.ok(Number.isFinite(n.rank));
});

test('unassigned node, when present in rollup, is flagged and isolated', () => {
  const out = computeComponentLayout({
    components: { a: { title: 'A', after: [] } },
    rollup: { a: { status: 'todo' }, _unassigned: { status: 'todo', total: 3, done: 0 } },
  });
  const u = out.nodes.find(n => n.id === '_unassigned');
  assert.ok(u, '_unassigned rendered as a node');
  assert.equal(u.unassigned, true);
  assert.equal(u.isolated, true);
});

test('empty components → empty layout (no nodes, no throw)', () => {
  const out = computeComponentLayout({ components: {}, rollup: {} });
  assert.equal(out.nodes.length, 0);
  assert.equal(out.edges.length, 0);
});
