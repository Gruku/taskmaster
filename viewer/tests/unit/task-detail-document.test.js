// viewer/tests/unit/task-detail-document.test.js
// Unit tests for inline-edit retrofit on Task Detail (v3-edit-014).
// Uses JSDOM + node:test — no Playwright needed.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

// --- JSDOM env setup ---
const dom = new JSDOM('<!doctype html><html><body></body></html>', {
  url: 'http://localhost/',
});
globalThis.document  = dom.window.document;
globalThis.window    = dom.window;
globalThis.HTMLElement = dom.window.HTMLElement;
globalThis.queueMicrotask = queueMicrotask;
globalThis.history   = dom.window.history;
// clipboard stub for copyToChip utility in task-detail-document
if (!globalThis.navigator) {
  Object.defineProperty(globalThis, 'navigator', {
    value: { clipboard: { writeText: async () => {} } },
    configurable: true,
  });
}

// --- Stubs ---
const FAKE_TASK = {
  id: 'T-001', title: 'Test task', status: 'todo', priority: 'medium', epic: 'core',
  phase: 'p1', estimate: 'M', branch: '', worktree: '', release: '', sub_repo: '',
  specification: 'Spec body', plan: 'Plan body', notes: 'Notes body',
  review_instructions: '', patchnote: '', docs: {}, anchors: [], depends_on: [],
  created: '2025-01-01', started: '', completed: '',
};

const FAKE_BACKLOG = {
  tasks: [FAKE_TASK],
  epics: [{ id: 'core', label: 'Core' }],
  phases: [{ id: 'p1', label: 'Phase 1' }],
};

function makeCtx(task = FAKE_TASK) {
  const patches = [];
  return {
    task,
    related: {},
    prefs: { screens: { task_detail: { view: 'A' } } },
    onNavigate: () => {},
    onToggleVariant: () => {},
    store: {
      getBacklog: () => FAKE_BACKLOG,
      setBacklog: () => {},
    },
    api: {
      patchTask: async (id, patch) => { patches.push({ id, patch }); return {}; },
      backlog: async () => FAKE_BACKLOG,
    },
    _patches: patches,
  };
}

function mount(ctx) {
  const root = document.createElement('div');
  document.body.appendChild(root);
  // Import and call mountTaskDetailDocument
  return { root };
}

// --- Import the module under test ---
const { mountTaskDetailDocument } = await import('../../js/components/task-detail-document.js');

// --- Tests ---

test('title renders as inline-field host (has .if-wrap)', () => {
  const ctx = makeCtx();
  const root = document.createElement('div');
  document.body.appendChild(root);
  mountTaskDetailDocument(root, ctx);

  const titleEl = root.querySelector('[data-test="title"]');
  assert.ok(titleEl, 'title element exists');
  // After retrofit, title should contain an if-wrap span (from mountInlineField)
  const ifWrap = titleEl.querySelector('.if-wrap');
  assert.ok(ifWrap, 'title contains inline-field wrap (.if-wrap)');
  root.remove();
});

test('title inline-field shows task title text in read mode', () => {
  const ctx = makeCtx();
  const root = document.createElement('div');
  document.body.appendChild(root);
  mountTaskDetailDocument(root, ctx);

  const titleEl = root.querySelector('[data-test="title"]');
  // ef-text is the read-mode span from TextField renderer
  const efText = titleEl.querySelector('.ef-text');
  assert.ok(efText, '.ef-text present in title');
  assert.equal(efText.textContent, 'Test task');
  root.remove();
});

test('status chip renders as inline-field host', () => {
  const ctx = makeCtx();
  const root = document.createElement('div');
  document.body.appendChild(root);
  mountTaskDetailDocument(root, ctx);

  const statusPill = root.querySelector('.td-status-pill');
  assert.ok(statusPill, '.td-status-pill exists');
  const ifWrap = statusPill.querySelector('.if-wrap');
  assert.ok(ifWrap, 'status pill contains .if-wrap from inline-field');
  root.remove();
});

test('priority chip renders as inline-field host', () => {
  const ctx = makeCtx();
  const root = document.createElement('div');
  document.body.appendChild(root);
  mountTaskDetailDocument(root, ctx);

  const priPill = root.querySelector('.td-pri-pill');
  assert.ok(priPill, '.td-pri-pill exists');
  const ifWrap = priPill.querySelector('.if-wrap');
  assert.ok(ifWrap, 'priority pill contains .if-wrap from inline-field');
  root.remove();
});

test('spec section renders as editable inline-field host', () => {
  const ctx = makeCtx();
  const root = document.createElement('div');
  document.body.appendChild(root);
  mountTaskDetailDocument(root, ctx);

  const sec = root.querySelector('[data-test="sec-spec"]');
  assert.ok(sec, 'sec-spec section exists');
  const ifWrap = sec.querySelector('.if-wrap[data-key="specification"]');
  assert.ok(ifWrap, 'spec section contains inline-field for specification');
  root.remove();
});

test('plan section renders as editable inline-field host', () => {
  const ctx = makeCtx();
  const root = document.createElement('div');
  document.body.appendChild(root);
  mountTaskDetailDocument(root, ctx);

  const sec = root.querySelector('[data-test="sec-plan"]');
  assert.ok(sec, 'sec-plan section exists');
  const ifWrap = sec.querySelector('.if-wrap[data-key="plan"]');
  assert.ok(ifWrap, 'plan section contains inline-field for plan');
  root.remove();
});

test('notes section renders as editable inline-field host', () => {
  const ctx = makeCtx();
  const root = document.createElement('div');
  document.body.appendChild(root);
  mountTaskDetailDocument(root, ctx);

  const sec = root.querySelector('[data-test="sec-notes"]');
  assert.ok(sec, 'sec-notes section exists');
  const ifWrap = sec.querySelector('.if-wrap[data-key="notes"]');
  assert.ok(ifWrap, 'notes section contains inline-field for notes');
  root.remove();
});

test('system-managed fields (id, created) have NO editable affordance', () => {
  const ctx = makeCtx();
  const root = document.createElement('div');
  document.body.appendChild(root);
  mountTaskDetailDocument(root, ctx);

  // The dates row renders created as plain text, no inline-field wiring
  const datesEl = root.querySelector('[data-test="dates"]');
  assert.ok(datesEl, 'dates section exists');
  // Dates row should NOT have any .if-wrap (they are system-managed)
  const ifWraps = datesEl.querySelectorAll('.if-wrap');
  assert.equal(ifWraps.length, 0, 'dates row has no inline-field wraps (system-managed)');
  root.remove();
});

test('inline status save calls api.patchTask with new status', async () => {
  const ctx = makeCtx();
  const root = document.createElement('div');
  document.body.appendChild(root);
  mountTaskDetailDocument(root, ctx);

  // Find status pill and click into edit mode
  const statusPill = root.querySelector('.td-status-pill');
  const efText = statusPill.querySelector('.ef-text, [class*="ef-"]');
  assert.ok(efText, 'status has editable read element');

  efText.click();
  // Should now show a select element (EnumSelect edit mode)
  const sel = statusPill.querySelector('select');
  assert.ok(sel, 'clicking status enters edit mode with select');

  // Change value to 'in-progress'
  sel.value = 'in-progress';
  sel.dispatchEvent(new dom.window.Event('change'));

  // Wait for debounced save
  await new Promise(r => setTimeout(r, 700));

  assert.ok(ctx._patches.length > 0, 'patchTask was called');
  const statusPatch = ctx._patches.find(p => p.patch.status === 'in-progress');
  assert.ok(statusPatch, 'patchTask called with status: in-progress');
  assert.equal(statusPatch.id, 'T-001');
  root.remove();
});

test('unmount cleanup does not throw', () => {
  const ctx = makeCtx();
  const root = document.createElement('div');
  document.body.appendChild(root);
  const unmount = mountTaskDetailDocument(root, ctx);
  assert.doesNotThrow(() => unmount(), 'unmount function does not throw');
  root.remove();
});

test('right rail mounts 6 panels synchronously for an all-empty task', () => {
  // Regression guard for v3-bugs-004: rail must be populated immediately
  // on mount (no queueMicrotask deferral) so navigation-before-microtask
  // cannot leave the aside detached with 0 children.
  const emptyTask = {
    id: 'T-EMPTY', title: 'Empty task', status: 'todo', priority: 'low', epic: '',
    phase: '', estimate: '', branch: '', worktree: '', release: '', sub_repo: '',
    specification: '', plan: '', notes: '', review_instructions: '', patchnote: '',
    docs: {}, anchors: [], depends_on: [], blockers: [],
    created: '2026-01-01', started: '', completed: '',
  };
  const ctx = makeCtx(emptyTask);
  ctx.related = { lessons: [], handovers: [], issues: [], dependencies: [], unblocks: [] };
  const root = document.createElement('div');
  document.body.appendChild(root);
  mountTaskDetailDocument(root, ctx);

  const rail = root.querySelector('[data-test="rail"]');
  assert.ok(rail, 'rail aside exists');
  // Must be synchronous — no await/microtask flush needed
  assert.equal(rail.children.length, 6, 'rail has exactly 6 panel children synchronously');
  const panels = rail.querySelectorAll('.td-panel');
  assert.equal(panels.length, 6, 'all 6 children carry .td-panel class');
  root.remove();
});
