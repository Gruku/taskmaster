import test from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';
import { RightRail, mountRightRail } from '../../js/components/right-rail.js';

test('open() injects rendered content; close() removes it', () => {
  const dom = new JSDOM('<!doctype html><body></body>');
  global.document = dom.window.document;
  const rail = new RightRail({ width: 480 });
  rail.open({ render: () => '<div id="x">hi</div>' });
  assert.ok(document.querySelector('.right-rail'));
  assert.ok(document.querySelector('#x'));
  rail.close();
  assert.equal(document.querySelector('.right-rail'), null);
});

test('open() twice swaps the content', () => {
  const dom = new JSDOM('<!doctype html><body></body>');
  global.document = dom.window.document;
  const rail = new RightRail();
  rail.open({ render: () => '<div id="a">first</div>' });
  rail.open({ render: () => '<div id="b">second</div>' });
  assert.equal(document.querySelector('#a'), null);
  assert.ok(document.querySelector('#b'));
  rail.close();
});

test('mountRightRail: all-empty task mounts exactly 7 panels synchronously', () => {
  const dom = new JSDOM('<!doctype html><body><aside id="rail"></aside></body>');
  global.document = dom.window.document;
  const aside = document.getElementById('rail');
  const emptyTask = { docs: {}, blockers: [] };
  const emptyRelated = { lessons: [], handovers: [], issues: [], dependencies: [], unblocks: [] };
  mountRightRail(aside, { task: emptyTask, related: emptyRelated, onNavigate: () => {} });
  // All 7 panels must be present immediately (no queueMicrotask delay)
  // Panels: Docs · Links · Lessons · Handovers · Issues · Dependencies + Unblocks · Blockers
  assert.equal(aside.children.length, 7, 'aside must have exactly 7 panel children');
  const panels = aside.querySelectorAll('.td-panel');
  assert.equal(panels.length, 7, 'all 7 children must carry the .td-panel class');
});

test('mountRightRail: each empty panel contains its header and an empty-state element', () => {
  const dom = new JSDOM('<!doctype html><body><aside id="rail2"></aside></body>');
  global.document = dom.window.document;
  const aside = document.getElementById('rail2');
  const emptyTask = { docs: {}, blockers: [] };
  const emptyRelated = { lessons: [], handovers: [], issues: [], dependencies: [], unblocks: [] };
  mountRightRail(aside, { task: emptyTask, related: emptyRelated, onNavigate: () => {} });
  const panels = [...aside.querySelectorAll('.td-panel')];
  for (const panel of panels) {
    assert.ok(panel.querySelector('.td-rail-h'), `panel "${panel.className}" must have a header`);
    assert.ok(panel.querySelector('.td-empty'), `panel "${panel.className}" must have a .td-empty placeholder`);
  }
});

test('mountRightRail: null/undefined task does not throw', () => {
  const dom = new JSDOM('<!doctype html><body><aside id="rail3"></aside></body>');
  global.document = dom.window.document;
  const aside = document.getElementById('rail3');
  assert.doesNotThrow(() => {
    mountRightRail(aside, { task: null, related: null, onNavigate: () => {} });
  });
  assert.equal(aside.children.length, 7, 'rail still mounts all 7 panels for null task');
});
