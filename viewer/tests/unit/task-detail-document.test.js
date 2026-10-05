// viewer/tests/unit/task-detail-document.test.js
// Unit tests for the task document template (page and embedded chrome) and its inline editing.
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
// clipboard stub for the copy buttons
const copied = [];
Object.defineProperty(globalThis, 'navigator', {
  value: { clipboard: { writeText: async (text) => { copied.push(text); } } },
  configurable: true,
});

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

function makeCtx(task = FAKE_TASK, extra = {}) {
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
      refreshBoard: async () => {},
    },
    api: {
      patchTask: async (id, patch) => { patches.push({ id, patch }); return {}; },
      backlog: async () => FAKE_BACKLOG,
    },
    _patches: patches,
    ...extra,
  };
}

// --- Import the module under test ---
const { mountTaskDetailDocument } = await import('../../js/components/task-detail-document.js');

function mount(task, extra) {
  const ctx = makeCtx(task, extra);
  const root = document.createElement('div');
  document.body.appendChild(root);
  const dispose = mountTaskDetailDocument(root, ctx);
  return { root, ctx, dispose, done() { dispose(); root.remove(); } };
}
const tick = (ms = 0) => new Promise((resolve) => setTimeout(resolve, ms));
const press = (el, key) => el.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }));
const ISO = /\d{4}-\d{2}-\d{2}T\d{2}:\d{2}/;

// --- Tests ---

test('a live claim banner disappears at expiry without a board refresh', async () => {
  const ctx = makeCtx();
  ctx.claim = {state: 'held', expired: false, holder: 'peer', expires_at: new Date(Date.now() + 100).toISOString()};
  const root = document.createElement('div');
  document.body.appendChild(root);
  const dispose = mountTaskDetailDocument(root, ctx);
  const banner = root.querySelector('.td-lock-banner');
  assert.ok(banner);
  assert.match(banner.textContent, /Locked by peer/);
  assert.ok(!ISO.test(banner.textContent), 'the expiry is not printed as a raw ISO string');
  await new Promise(resolve => setTimeout(resolve, 140));
  assert.equal(root.querySelector('.td-lock-banner'), null);
  dispose();
  root.remove();
});

// ── Template line 1: meta line ──
test('page chrome: the meta line is id (a copy button), Tasks, epic and phase, then the created stamp', () => {
  const t = mount({ ...FAKE_TASK, created: '2025-01-01T09:30:00Z' });
  const meta = t.root.querySelector('[data-test="meta"]');
  assert.ok(meta, 'meta line exists');
  const id = meta.querySelector('[data-test="task-id"]');
  assert.equal(id.tagName, 'BUTTON');
  assert.equal(id.getAttribute('type'), 'button');
  assert.equal(id.getAttribute('aria-label'), 'Copy task id');
  assert.match(id.textContent, /T-001/);
  assert.ok(id.querySelector('svg.icon'), 'the id carries the copy icon');
  const links = [...meta.querySelectorAll('a')];
  assert.deepEqual(links.map((a) => [a.textContent, a.getAttribute('href')]), [['Tasks', '#/kanban'], ['core', '#/epic/core']]);
  assert.match(meta.textContent, /p1/);
  const stamp = meta.querySelector('time');
  assert.ok(stamp.getAttribute('title'), 'the absolute time is the tooltip');
  assert.match(meta.textContent, /created \d+(y|mo|d|h|m) ago/);
  assert.ok(!ISO.test(meta.textContent), 'no raw ISO string in the meta line');
  t.done();
});

test('no "back" element in either chrome', () => {
  for (const chrome of ['page', 'embedded']) {
    const t = mount(FAKE_TASK, { chrome });
    assert.equal(t.root.querySelector('.td-back'), null);
    assert.ok(!t.root.textContent.includes('‹ back'));
    assert.ok(!/\bback\b/i.test(t.root.querySelector('[data-test="meta"]')?.textContent ?? ''));
    t.done();
  }
});

test('copying the id says so in words, in a live region, and reverts', async () => {
  const t = mount();
  const id = t.root.querySelector('[data-test="task-id"]');
  const status = id.querySelector('[role="status"]');
  assert.ok(status, 'a live region inside the button');
  assert.equal(status.textContent, '');
  copied.length = 0;
  id.click();
  await tick();
  assert.deepEqual(copied, ['T-001']);
  assert.equal(status.textContent, 'Copied');
  t.done();
});

// ── Template line 2: title ──
test('page chrome: the title is an h1 holding the inline field', () => {
  const t = mount();
  const titleEl = t.root.querySelector('[data-test="title"]');
  assert.equal(titleEl.tagName, 'H1');
  assert.ok(titleEl.querySelector('.if-wrap[data-key="title"]'), 'title contains inline-field wrap (.if-wrap)');
  assert.equal(titleEl.querySelector('.ef-text').textContent, 'Test task');
  t.done();
});

test('embedded chrome: no meta line and no title in the document; the title field mounts in the host it is given', () => {
  const titleHost = document.createElement('span');
  document.body.appendChild(titleHost);
  const t = mount(FAKE_TASK, { chrome: 'embedded', titleHost });
  assert.equal(t.root.querySelector('[data-test="meta"]'), null);
  assert.equal(t.root.querySelector('[data-test="title"]'), null);
  assert.equal(t.root.querySelector('h1, h2'), null, 'the dialog owns the h2; the document starts below it');
  assert.ok(!t.root.textContent.includes('Test task'), 'the title text is not repeated in the body');
  assert.equal(titleHost.querySelector('.if-wrap[data-key="title"] .ef-text').textContent, 'Test task');
  // The body starts at the marker row.
  assert.equal(t.root.querySelector('.td-body').firstElementChild.dataset.test, 'chips');
  // The phase has no meta line to sit in, so it joins the tags.
  assert.match(t.root.querySelector('[data-tag="phase"]').textContent, /p1/);
  t.dispose();
  assert.equal(titleHost.children.length, 0, 'unmount takes the title field out of the host');
  t.root.remove();
  titleHost.remove();
});

test('section headings sit one level under the title: h2 on the page, h3 in the dialog', () => {
  const page = mount();
  assert.equal(page.root.querySelector('[data-test="sec-spec"] .td-section-h').tagName, 'H2');
  page.done();
  const modal = mount(FAKE_TASK, { chrome: 'embedded' });
  assert.equal(modal.root.querySelector('[data-test="sec-spec"] .td-section-h').tagName, 'H3');
  modal.done();
});

// ── Template line 3: marker row ──
test('status and priority are markers: a shape plus a word, and a different shape for a different status', () => {
  const todo = mount();
  const row = todo.root.querySelector('[data-test="chips"]');
  const status = row.querySelector('[data-field="status"]');
  assert.ok(status.querySelector('.if-wrap[data-key="status"]'), 'status is an inline field');
  assert.equal(status.querySelector('.marker__word').textContent, 'Todo');
  const priority = row.querySelector('[data-field="priority"]');
  assert.ok(priority.querySelector('.if-wrap[data-key="priority"]'), 'priority is an inline field');
  assert.equal(priority.querySelector('.marker__word').textContent, 'Medium');
  todo.done();

  const shapes = {};
  for (const value of ['done', 'in-progress']) {
    const t = mount({ ...FAKE_TASK, status: value });
    const m = t.root.querySelector('[data-field="status"] .marker');
    shapes[value] = { word: m.querySelector('.marker__word').textContent, shape: m.querySelector('.marker__shape').dataset.shape, tone: m.className };
    t.done();
  }
  assert.deepEqual(shapes.done, { word: 'Done', shape: 'dot', tone: 'marker marker--success' });
  assert.equal(shapes['in-progress'].word, 'In progress');
  assert.notEqual(shapes['in-progress'].shape, shapes.done.shape);
});

test('marker row: estimate, epic, then branch / worktree / release / sub-repo as tags; branch and worktree copy', async () => {
  const t = mount({ ...FAKE_TASK, branch: 'feat/x', worktree: '.worktrees/x', release: '7.1.0', sub_repo: 'viewer' });
  const row = t.root.querySelector('[data-test="chips"]');
  const order = [...row.children].map((el) => el.dataset.field || el.dataset.tag);
  assert.deepEqual(order, ['status', 'priority', 'estimate', 'epic', 'branch', 'worktree', 'release', 'sub_repo']);
  assert.match(row.querySelector('[data-tag="estimate"]').textContent, /M/);
  const epic = row.querySelector('[data-tag="epic"]');
  assert.equal(epic.tagName, 'A');
  assert.equal(epic.getAttribute('href'), '#/epic/core');
  assert.ok(epic.querySelector('.td-swatch'));
  for (const [tag, value] of [['branch', 'feat/x'], ['worktree', '.worktrees/x']]) {
    const btn = row.querySelector(`[data-tag="${tag}"]`);
    assert.equal(btn.tagName, 'BUTTON');
    assert.ok(btn.querySelector('svg.icon'), `${tag} carries the copy icon`);
    assert.match(btn.textContent, new RegExp(value.replace(/[.]/g, '\\.')));
    copied.length = 0;
    btn.click();
    await tick();
    assert.deepEqual(copied, [value]);
    assert.equal(btn.querySelector('[role="status"]').textContent, 'Copied');
  }
  assert.equal(row.querySelector('[data-test="branch"]'), row.querySelector('[data-tag="branch"]'));
  assert.equal(row.querySelector('[data-tag="release"]').tagName, 'SPAN');
  assert.match(row.querySelector('[data-tag="release"]').textContent, /7\.1\.0/);
  assert.match(row.querySelector('[data-tag="sub_repo"]').textContent, /viewer/);
  t.done();
});

test('tags with nothing to show are left out', () => {
  const t = mount({ ...FAKE_TASK, estimate: '', epic: '' });
  const order = [...t.root.querySelector('[data-test="chips"]').children].map((el) => el.dataset.field || el.dataset.tag);
  assert.deepEqual(order, ['status', 'priority']);
  t.done();
});

test('status and priority can be reached and opened from the keyboard', async () => {
  const t = mount();
  const read = t.root.querySelector('[data-field="status"] .if-wrap > *');
  assert.equal(read.getAttribute('tabindex'), '0');
  assert.equal(read.getAttribute('role'), 'button');
  assert.match(read.getAttribute('aria-label'), /^Status: Todo/);
  press(read, 'Enter');
  assert.ok(t.root.querySelector('[data-field="status"] select'), 'Enter opens the editor');
  t.done();
});

test('inline status save calls api.patchTask with new status', async () => {
  const t = mount();
  const statusHost = t.root.querySelector('[data-field="status"]');
  // Clicking the marker still opens the editor.
  statusHost.querySelector('.marker').click();
  const sel = statusHost.querySelector('select');
  assert.ok(sel, 'clicking status enters edit mode with select');

  sel.value = 'in-progress';
  sel.dispatchEvent(new dom.window.Event('change'));

  // Wait for debounced save
  await tick(700);

  assert.ok(t.ctx._patches.length > 0, 'patchTask was called');
  const statusPatch = t.ctx._patches.find(p => p.patch.status === 'in-progress');
  assert.ok(statusPatch, 'patchTask called with status: in-progress');
  assert.equal(statusPatch.id, 'T-001');
  t.done();
});

// ── Template line 5: sections ──
for (const [name, key, dataTest] of [['spec', 'specification', 'sec-spec'], ['plan', 'plan', 'sec-plan'], ['notes', 'notes', 'sec-notes']]) {
  test(`${name} section renders as editable inline-field host`, () => {
    const t = mount();
    const sec = t.root.querySelector(`[data-test="${dataTest}"]`);
    assert.ok(sec, `${dataTest} section exists`);
    assert.ok(sec.querySelector(`.if-wrap[data-key="${key}"]`), `section contains inline-field for ${key}`);
    assert.ok(sec.querySelector('.md-body'), 'the body is rendered markdown');
    // A document is opened for editing from a real button, not only by clicking the text.
    const edit = sec.querySelector('button.td-section-edit');
    assert.ok(edit, 'an Edit button in the heading row');
    assert.match(edit.getAttribute('aria-label'), /^Edit /);
    edit.click();
    assert.ok(sec.querySelector('textarea'), 'the button opens the editor');
    t.done();
  });
}

test('sections keep their order: specification, plan, notes, review instructions, activity, patchnote, dates', () => {
  const t = mount({ ...FAKE_TASK, status: 'in-review', review_instructions: 'Open both themes.', activity: ['10:00 started', '11:00 pushed'] });
  const order = [...t.root.querySelectorAll('.td-body > [data-test]')].map((el) => el.dataset.test);
  assert.deepEqual(order, ['chips', 'sec-spec', 'sec-plan', 'sec-notes', 'sec-review-instructions', 'sec-activity', 'dates']);
  assert.deepEqual([...t.root.querySelectorAll('[data-test="sec-activity"] li')].map((li) => li.textContent), ['10:00 started', '11:00 pushed']);
  t.done();
});

test('review instructions show only in review; the patchnote only when done', () => {
  const idle = mount({ ...FAKE_TASK, review_instructions: 'Open both themes.', patchnote: 'Shipped.' });
  assert.equal(idle.root.querySelector('[data-test="sec-review-instructions"]'), null);
  assert.equal(idle.root.querySelector('[data-test="sec-patchnote"]'), null);
  idle.done();
  const done = mount({ ...FAKE_TASK, status: 'done', patchnote: 'Shipped.' });
  assert.match(done.root.querySelector('[data-test="sec-patchnote"] .md-body').textContent, /Shipped\./);
  done.done();
});

test('empty editable sections collapse into one line that lists exactly them', () => {
  const t = mount({ ...FAKE_TASK, specification: '', plan: null, notes: 'Notes body' });
  assert.equal(t.root.querySelector('[data-test="sec-spec"]'), null);
  assert.equal(t.root.querySelector('[data-test="sec-plan"]'), null);
  assert.ok(t.root.querySelector('[data-test="sec-notes"]'), 'a section with content is never collapsed');
  assert.ok(!t.root.textContent.includes('no content'));
  const line = t.root.querySelector('[data-test="empty-sections"]');
  assert.equal(line.textContent.replace(/\s+/g, ' ').trim(), 'Empty: Specification · Plan');
  const names = [...line.querySelectorAll('button')];
  assert.deepEqual(names.map((b) => b.textContent), ['Specification', 'Plan']);
  assert.ok(names.every((b) => b.getAttribute('type') === 'button'));
  // The line closes the list of sections: after the last section, before the dates.
  const order = [...t.root.querySelectorAll('.td-body > [data-test]')].map((el) => el.dataset.test);
  assert.deepEqual(order, ['chips', 'sec-notes', 'empty-sections', 'dates']);
  t.done();
});

test('in review, an empty Review instructions section joins the line; out of review it is not offered', () => {
  const inReview = mount({ ...FAKE_TASK, status: 'in-review', review_instructions: '' });
  assert.equal(inReview.root.querySelector('[data-test="empty-sections"]').textContent.replace(/\s+/g, ' ').trim(), 'Empty: Review instructions');
  inReview.done();
  const full = mount();
  assert.equal(full.root.querySelector('[data-test="empty-sections"]'), null, 'nothing empty, no line');
  full.done();
});

test('a name on the Empty line opens that section straight into its editor, in its place', async () => {
  const t = mount({ ...FAKE_TASK, specification: 'Spec body', plan: '', notes: '' });
  const line = t.root.querySelector('[data-test="empty-sections"]');
  line.querySelector('button[data-section="plan"]').click();
  await tick();
  const sec = t.root.querySelector('[data-test="sec-plan"]');
  assert.ok(sec, 'the section is now in the document');
  const editor = sec.querySelector('textarea');
  assert.ok(editor, 'opened in edit mode');
  assert.equal(document.activeElement, editor, 'focus is in the editor');
  assert.equal(sec.previousElementSibling.dataset.test, 'sec-spec', 'between Specification and the Empty line');
  assert.equal(line.textContent.replace(/\s+/g, ' ').trim(), 'Empty: Notes');
  t.done();
});

test('cancelling an untouched editor collapses the section back and returns focus to its name', async () => {
  const t = mount({ ...FAKE_TASK, plan: '', notes: '' });
  const line = t.root.querySelector('[data-test="empty-sections"]');
  line.querySelector('button[data-section="plan"]').click();
  await tick();
  const editor = t.root.querySelector('[data-test="sec-plan"] textarea');
  press(editor, 'Escape');
  await tick();
  assert.equal(t.root.querySelector('[data-test="sec-plan"]'), null, 'collapsed again');
  assert.equal(line.textContent.replace(/\s+/g, ' ').trim(), 'Empty: Plan · Notes', 'back in its place on the line');
  assert.equal(document.activeElement, line.querySelector('button[data-section="plan"]'));
  assert.equal(t.ctx._patches.length, 0, 'nothing was saved');
  t.done();
});

test('text saved into an opened section keeps it open as a document', async () => {
  const t = mount({ ...FAKE_TASK, plan: '' });
  t.root.querySelector('[data-test="empty-sections"] button[data-section="plan"]').click();
  await tick();
  const editor = t.root.querySelector('[data-test="sec-plan"] textarea');
  editor.value = '1. Tokens';
  editor.dispatchEvent(new dom.window.Event('input'));
  editor.dispatchEvent(new dom.window.KeyboardEvent('keydown', { key: 'Enter', ctrlKey: true, bubbles: true, cancelable: true }));
  await tick(20);
  assert.deepEqual(t.ctx._patches.map((p) => p.patch), [{ plan: '1. Tokens' }]);
  const sec = t.root.querySelector('[data-test="sec-plan"]');
  assert.ok(sec.querySelector('.md-body'), 'shown as a rendered document');
  assert.equal(t.root.querySelector('[data-test="empty-sections"]'), null, 'nothing left on the line');
  t.done();
});

test('docs are listed once — in the rail — not also as a body section', () => {
  const t = mount({ ...FAKE_TASK, docs: { spec: 'docs/specs/a.md' } });
  assert.equal(t.root.querySelectorAll('a[href="/file/docs/specs/a.md"]').length, 1);
  assert.ok(t.root.querySelector('[data-test="rail"] [data-panel="docs"]'));
  t.done();
});

test('a description is shown when the task has one, and is not offered on the Empty line', () => {
  const withIt = mount({ ...FAKE_TASK, description: 'Why this exists.' });
  assert.match(withIt.root.querySelector('[data-test="sec-description"] .md-body').textContent, /Why this exists\./);
  assert.equal(withIt.root.querySelector('.td-body > section').dataset.test, 'sec-description');
  withIt.done();
  const without = mount({ ...FAKE_TASK, plan: '' });
  assert.equal(without.root.querySelector('[data-test="sec-description"]'), null);
  assert.ok(!without.root.querySelector('[data-test="empty-sections"]').textContent.includes('Description'));
  without.done();
});

test('spec review: the verdict is a shape and a word; a note opens from a real button', () => {
  const t = mount({ ...FAKE_TASK, spec_review: { verdict: 'warn', codex_note: 'Two requirements are untestable.' } });
  const block = t.root.querySelector('[data-test="spec-review"]');
  assert.match(block.querySelector('.marker__word').textContent, /warn/i);
  assert.ok(block.querySelector('.marker__shape'));
  const toggle = block.querySelector('button');
  assert.equal(toggle.getAttribute('aria-expanded'), 'false');
  const note = block.querySelector('.td-codex-note');
  assert.ok(note.hidden);
  assert.ok(!note.classList.contains('serif'));
  toggle.click();
  assert.equal(toggle.getAttribute('aria-expanded'), 'true');
  assert.ok(!note.hidden);
  assert.equal(note.textContent, 'Two requirements are untestable.');
  t.done();
  const plain = mount({ ...FAKE_TASK, spec_review: { verdict: 'pass' } });
  assert.equal(plain.root.querySelector('[data-test="spec-review"] button'), null, 'no note, nothing to open');
  plain.done();
});

// ── Template line 6: dates ──
test('dates read relative, carry the absolute time as a tooltip, and never print an ISO string', () => {
  const t = mount({ ...FAKE_TASK, created: '2025-01-01T09:00:00Z', started: '2025-01-03T10:00:00Z', completed: '' });
  const dates = t.root.querySelector('[data-test="dates"]');
  const cells = [...dates.querySelectorAll('.td-date')];
  assert.deepEqual(cells.map((c) => c.querySelector('dt').textContent), ['Created', 'Started']);
  for (const cell of cells) {
    const stamp = cell.querySelector('time');
    assert.match(stamp.textContent, /^\d+(y|mo|d|h|m) ago$|^now$/);
    assert.ok(stamp.getAttribute('title').length > 0);
  }
  assert.ok(!ISO.test(dates.textContent));
  assert.ok(!ISO.test(t.root.textContent), 'no raw ISO string anywhere in the document');
  t.done();
});

test('system-managed fields (id, created) have NO editable affordance', () => {
  const t = mount();
  const datesEl = t.root.querySelector('[data-test="dates"]');
  assert.ok(datesEl, 'dates section exists');
  assert.equal(datesEl.querySelectorAll('.if-wrap').length, 0, 'dates row has no inline-field wraps (system-managed)');
  t.done();
});

// ── Template line 7: rail ──
test('with nothing related there is no rail and the body takes the width', () => {
  const emptyTask = {
    id: 'T-EMPTY', title: 'Empty task', status: 'todo', priority: 'low', epic: '',
    phase: '', estimate: '', branch: '', worktree: '', release: '', sub_repo: '',
    specification: '', plan: '', notes: '', review_instructions: '', patchnote: '',
    docs: {}, anchors: [], depends_on: [], blockers: [],
    created: '2026-01-01', started: '', completed: '',
  };
  const t = mount(emptyTask, { related: { handovers: [], issues: [], dependencies: [], unblocks: [] } });
  assert.equal(t.root.querySelector('[data-test="rail"]'), null);
  assert.equal(t.root.querySelector('aside'), null);
  assert.ok(t.root.querySelector('.td-grid').classList.contains('td-grid--solo'));
  assert.equal(t.root.querySelector('[data-test="empty-sections"]').textContent.replace(/\s+/g, ' ').trim(), 'Empty: Specification · Plan · Notes');
  t.done();
});

test('the rail mounts synchronously when there is something related, with Relations first', () => {
  // Regression guard for v3-bugs-004: the rail must be populated on mount (no queueMicrotask deferral).
  const t = mount(FAKE_TASK, { related: {
    dependencies: [{ id: 'T-000', title: 'Before', status: 'done' }],
    unblocks: [{ id: 'T-002', title: 'After', status: 'todo' }],
  } });
  const rail = t.root.querySelector('[data-test="rail"]');
  assert.ok(rail, 'rail aside exists');
  assert.equal(rail.tagName, 'ASIDE');
  assert.ok(!t.root.querySelector('.td-grid').classList.contains('td-grid--solo'));
  assert.deepEqual([...rail.querySelectorAll('.td-panel')].map((p) => p.dataset.panel), ['relations']);
  assert.deepEqual([...rail.querySelectorAll('[data-sub]')].map((s) => s.dataset.sub), ['depends', 'unblocks']);
  assert.equal(rail.querySelector('.td-rail-h').tagName, 'H2');
  t.done();
});

// ── Template line 8: landmarks ──
test('the document produces no <main>: the body container is a div', () => {
  for (const chrome of ['page', 'embedded']) {
    const t = mount(FAKE_TASK, { chrome, related: { dependencies: [{ id: 'T-000', title: 'Before', status: 'done' }] } });
    assert.equal(t.root.querySelector('main'), null);
    assert.equal(t.root.querySelector('.td-body').tagName, 'DIV');
    t.done();
  }
});

// ── Review focus 4: hostile shapes ──
test('a task whose fields are null, missing or of the wrong type renders without throwing', () => {
  const shapes = [
    { id: 'T-N' },
    { id: 'T-N', title: null, status: null, priority: null, epic: null, phase: null, estimate: null, depends_on: null, docs: null,
      links: null, blockers: null, activity: null, spec_review: null, gates: null, merge_status: null, created: null },
    { id: 'T-W', title: 42, status: 'someday', priority: 7, epic: { id: 'x' }, phase: ['p'], estimate: 3, depends_on: 'T-1',
      docs: 'docs/a.md', links: 'T-2', blockers: 'Waiting', activity: 'one line', spec_review: 'pass', gates: 'none', lane: 'full',
      merge_status: 'develop', created: 'not a date', started: 12, completed: {}, branch: 9, worktree: {}, release: [], sub_repo: 0,
      specification: 5, plan: ['a'], notes: { a: 1 }, description: false },
    { id: 'T-A', status: 'done', activity: [null, { at: 'x' }, 3], spec_review: { verdict: 9 }, docs: { spec: null, plan: 4 },
      patchnote: 7, lane: 'unknown', gates: { 'review-gate': null } },
  ];
  for (const task of shapes) {
    for (const chrome of ['page', 'embedded']) {
      let t;
      assert.doesNotThrow(() => { t = mount(task, { chrome, related: null, claim: 'held' }); }, `${task.id} / ${chrome}`);
      assert.ok(t.root.querySelector('[data-test="chips"]'));
      assert.ok(!t.root.textContent.includes('[object Object]'));
      t.done();
    }
  }
  // An unknown status is shown as it is, neutral; a bare number of days reads as days.
  const odd = mount(shapes[2]);
  assert.equal(odd.root.querySelector('[data-field="status"] .marker__word').textContent, 'someday');
  assert.match(odd.root.querySelector('[data-tag="estimate"]').textContent, /3d/);
  assert.equal(odd.root.querySelector('[data-test="dates"]'), null, 'no readable date, no dates section');
  odd.done();
});

test('unmount cleanup does not throw', () => {
  const ctx = makeCtx();
  const root = document.createElement('div');
  document.body.appendChild(root);
  const unmount = mountTaskDetailDocument(root, ctx);
  assert.doesNotThrow(() => unmount(), 'unmount function does not throw');
  assert.equal(root.children.length, 0);
  root.remove();
});

test('linked bugs come from the listBugs the caller hands over, after the sections and before the dates', async () => {
  const asked = [];
  const listBugs = async (q) => { asked.push(q); return [{ id: 'B-031', title: 'Card edge vanishes', status: 'open' }, { id: 'B-032', title: 'Fixed one', status: 'fixed' }, null]; };
  const m = mount(FAKE_TASK, { listBugs });
  await tick();
  assert.deepEqual(asked, [{ found_in: 'T-001' }]);
  const section = m.root.querySelector('[data-test="linked-bugs"]');
  assert.ok(section, 'the linked bugs section is shown');
  assert.deepEqual([...section.querySelectorAll('a')].map((a) => a.getAttribute('href')), ['#/bug/B-031', '#/bug/B-032']);
  assert.match(section.querySelector('.marker__word').textContent, /1 open bug blocking close/);
  const order = [...m.root.querySelector('.td-body').children].map((el) => el.dataset.test).filter(Boolean);
  assert.ok(order.indexOf('linked-bugs') < order.indexOf('dates'), order.join(','));
  m.done();
});
