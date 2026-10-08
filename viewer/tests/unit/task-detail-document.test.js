// viewer/tests/unit/task-detail-document.test.js
// Unit tests for the task document template (page and embedded chrome) and its inline editing.
// Uses JSDOM + node:test — no Playwright needed.
import { test, mock } from 'node:test';
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
  epics: [{ id: 'core', name: 'Core platform' }],
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
const { mountTaskDetailDocument, rememberView, taskMeta } = await import('../../js/components/task-detail-document.js');

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
// What a screen reader reads of an element: its text without the parts hidden from it.
const spoken = (el) => { const c = el.cloneNode(true); c.querySelectorAll('[aria-hidden="true"]').forEach((n) => n.remove()); return c.textContent; };

// --- Tests ---

test('a live claim banner disappears at expiry without a board refresh', () => {
  // The clock is the test's: on a real one, a first mount slower than the claim's remaining life (a cold module under
  // a loaded suite) found the claim already expired and the banner gone before it could be looked at.
  mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.parse('2026-10-05T12:00:00Z') });
  const ctx = makeCtx();
  ctx.claim = {state: 'held', expired: false, holder: 'peer', expires_at: new Date(Date.now() + 100).toISOString()};
  const root = document.createElement('div');
  document.body.appendChild(root);
  let dispose;
  try {
    dispose = mountTaskDetailDocument(root, ctx);
    const banner = root.querySelector('.td-lock-banner');
    assert.ok(banner);
    assert.match(banner.textContent, /Locked by peer/);
    assert.ok(!ISO.test(banner.textContent), 'the expiry is not printed as a raw ISO string');
    mock.timers.tick(99);
    assert.ok(root.querySelector('.td-lock-banner'), 'still held a millisecond before expiry');
    mock.timers.tick(1);
    assert.equal(root.querySelector('.td-lock-banner'), null);
  } finally {
    // A failure here must not leave a mounted document, or a mocked clock, behind for the tests that follow.
    dispose?.();
    root.remove();
    mock.timers.reset();
  }
});

test('a claim that is already past its expiry when the document mounts shows no banner', () => {
  mock.timers.enable({ apis: ['setTimeout', 'Date'], now: Date.parse('2026-10-05T12:00:00Z') });
  try {
    const t = mount(FAKE_TASK, { claim: { state: 'held', expired: false, holder: 'peer', expires_at: '2026-10-05T11:59:59Z' } });
    assert.equal(t.root.querySelector('.td-lock-banner'), null);
    t.done();
  } finally { mock.timers.reset(); }
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
  const message = titleEl.nextElementSibling;
  assert.ok(message?.matches('div.td-title-message'), 'the title says what went wrong right after the h1, not inside it');
  assert.equal(message.parentElement.className, 'td-head');
  assert.equal(titleEl.querySelector('.if-error'), null, 'no message inside the h1');
  assert.equal(titleEl.querySelector('.if-status')?.getAttribute('aria-hidden'), 'true', 'the glyph stays beside the title, unread');
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
  // The body starts with the title's message host (empty, so not shown), then the marker row.
  const [first, second] = t.root.querySelector('.td-body').children;
  assert.ok(first.matches('div.td-title-message'));
  assert.equal(second.dataset.test, 'chips');
  assert.equal(titleHost.querySelector('.if-error'), null, 'no message in the heading');
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

test('the epic tag is a swatch and the epic\'s name, with no inline style, on the page and in the dialog', () => {
  for (const chrome of ['page', 'embedded']) {
    const t = mount(FAKE_TASK, { chrome });
    const epic = t.root.querySelector('[data-test="chips"] [data-tag="epic"]');
    assert.equal(epic.tagName, 'A', chrome);
    assert.ok(epic.classList.contains('td-tag') && epic.classList.contains('td-epic'), chrome);
    assert.equal(epic.getAttribute('href'), '#/epic/core', chrome);
    assert.equal(epic.hasAttribute('style'), false, chrome);
    const swatch = epic.querySelector('.td-swatch');
    assert.ok(swatch.classList.contains('td-swatch--cat-1'), chrome);
    assert.equal(swatch.getAttribute('aria-hidden'), 'true', chrome);
    assert.equal(epic.querySelector('.td-tag__k').textContent, 'Epic', chrome);
    assert.equal(epic.querySelector('.td-tag__v').textContent, 'Core platform', chrome);
    t.done();
  }
});

test('an epic missing from the backlog shows its id and no swatch', () => {
  const t = mount({ ...FAKE_TASK, epic: 'gone' });
  const epic = t.root.querySelector('[data-tag="epic"]');
  assert.equal(epic.querySelector('.td-swatch'), null);
  assert.equal(epic.querySelector('.td-tag__v').textContent, 'gone');
  assert.equal(epic.getAttribute('href'), '#/epic/gone');
  assert.equal(epic.hasAttribute('style'), false);
  t.done();
});

test('the gate strip says each gate and its state in words and never prints the raw gate_state', () => {
  const task = { ...FAKE_TASK, lane: 'full', gates: { 'spec-review': { verdict: 'pass' } }, gate_state: 'plan-review:pending' };
  for (const chrome of ['page', 'embedded']) {
    const t = mount(task, { chrome });
    const strip = t.root.querySelector('[data-test="gate-pipeline"]');
    assert.deepEqual([...strip.querySelectorAll('.marker__word')].map((el) => el.textContent), ['Spec review', 'Plan review', 'Review gate']);
    assert.deepEqual([...strip.querySelectorAll('.gp-gate__state')].map((el) => el.textContent), ['passed', 'pending', 'pending']);
    assert.ok(!strip.textContent.includes('plan-review:pending'), chrome);
    t.done();
  }
});

test('tags with nothing to show are left out', () => {
  const t = mount({ ...FAKE_TASK, estimate: '', epic: '' });
  const order = [...t.root.querySelector('[data-test="chips"]').children].map((el) => el.dataset.field || el.dataset.tag);
  assert.deepEqual(order, ['status', 'priority']);
  t.done();
});

test('status and priority can be reached and opened from the keyboard', async () => {
  const t = mount();
  // Not `.if-wrap > *`: jsdom's selector engine has been seen to miss that child once an earlier test left a document
  // mounted, so the element is reached by structure instead.
  const read = t.root.querySelector('[data-field="status"] .if-wrap').firstElementChild;
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

// ── A re-mount (another writer's change) keeps what the user had open and where they were ──
const REVIEWED = { ...FAKE_TASK, spec_review: { verdict: 'warn', codex_note: 'Two requirements are untestable.' } };

test('rememberView: an open reviewer note is open again after a re-mount, and focus is back on its toggle', () => {
  const t = mount(REVIEWED);
  const toggle = t.root.querySelector('[data-focus="spec-note"]');
  toggle.click();
  toggle.focus();
  const restore = rememberView(t.root);
  t.done();
  const next = mount(REVIEWED);
  assert.equal(restore(next.root), true, 'focus was restored');
  const again = next.root.querySelector('[data-focus="spec-note"]');
  assert.equal(again.getAttribute('aria-expanded'), 'true');
  assert.equal(next.root.querySelector('.td-codex-note').hidden, false);
  assert.equal(document.activeElement, again);
  next.done();
});

test('rememberView: an open disclosure is re-opened even when focus was elsewhere; a closed one stays closed', () => {
  const t = mount(REVIEWED);
  t.root.querySelector('[data-focus="spec-note"]').click();
  document.activeElement?.blur?.();
  const restore = rememberView(t.root);
  t.done();
  const next = mount(REVIEWED);
  assert.equal(restore(next.root), false, 'there was no focus to restore');
  assert.equal(next.root.querySelector('[data-focus="spec-note"]').getAttribute('aria-expanded'), 'true');
  const closed = rememberView(next.root.querySelector('[data-test="dates"]') ?? document.createElement('div'));
  next.done();
  const last = mount(REVIEWED);
  closed(last.root);
  assert.equal(last.root.querySelector('[data-focus="spec-note"]').getAttribute('aria-expanded'), 'false');
  last.done();
});

test('rememberView: a refusal still said beside a field is said again after a re-mount, without a second announcement', async () => {
  const reason = 'Completion blocked: review-gate is still open';
  const api = { ...makeCtx().api, patchTask: async () => { throw Object.assign(new Error(reason), { code: 409 }); } };
  const t = mount(FAKE_TASK, { api });
  const message = (root) => root.querySelector('[data-field="status"] .if-error');
  t.root.querySelector('[data-field="status"] .if-wrap').firstElementChild.click();
  const select = t.root.querySelector('[data-field="status"] select');
  select.value = 'done';
  select.dispatchEvent(new dom.window.Event('change'));
  for (let i = 0; i < 100 && !message(t.root).textContent; i++) await tick(5);
  select.blur();
  assert.equal(t.root.querySelector('[data-field="status"] select'), null, 'left: read mode');
  assert.equal(message(t.root).textContent, reason);
  const restore = rememberView(t.root);
  t.done();

  const next = mount(FAKE_TASK, { api });
  try {
    restore(next.root);
    assert.equal(message(next.root).textContent, reason);
    assert.ok(next.root.querySelector('[data-field="status"] .if-status-error'));
    assert.equal(message(next.root).getAttribute('aria-live'), 'off', 'already announced once; not again for the re-mount');
    assert.equal(message(next.root).getAttribute('role'), 'alert');
    next.root.querySelector('[data-field="status"] .if-wrap').firstElementChild.click();
    assert.equal(message(next.root).textContent, '', 'opening the field clears it');
    assert.equal(message(next.root).hasAttribute('aria-live'), false, 'the next refusal is announced again');
  } finally { next.done(); }
});

// The reason was about the value the user tried to replace; once another writer has replaced it, it is stale.
test('rememberView: a refusal is dropped when another writer changed that field, and kept when they changed another', async () => {
  const api = { ...makeCtx().api, patchTask: async () => { throw Object.assign(new Error('No'), { code: 409 }); } };
  const message = (root) => root.querySelector('[data-field="status"] .if-error');
  const refused = async () => {
    const t = mount(FAKE_TASK, { api });
    for (const wrap of t.root.querySelectorAll('.if-wrap')) assert.ok(wrap.hasAttribute('data-stored'), `${wrap.dataset.key} carries data-stored`);
    assert.equal(t.root.querySelector('[data-field="status"] .if-wrap').dataset.stored, JSON.stringify(FAKE_TASK.status));
    t.root.querySelector('[data-field="status"] .if-wrap').firstElementChild.click();
    const select = t.root.querySelector('[data-field="status"] select');
    select.value = 'done';
    select.dispatchEvent(new dom.window.Event('change'));
    for (let i = 0; i < 100 && !message(t.root).textContent; i++) await tick(5);
    select.blur();
    assert.equal(message(t.root).textContent, 'No');
    const restore = rememberView(t.root);
    t.done();
    return restore;
  };

  let restore = await refused();
  const changed = mount({ ...FAKE_TASK, status: 'in-review' }, { api });
  try {
    restore(changed.root);
    assert.equal(message(changed.root).textContent, '', 'the status itself changed: no reason carried');
  } finally { changed.done(); }

  restore = await refused();
  const other = mount({ ...FAKE_TASK, title: 'Renamed elsewhere' }, { api });
  try {
    restore(other.root);
    assert.equal(message(other.root).textContent, 'No', 'another field changed: the reason still stands');
  } finally { other.done(); }
});

test('taskMeta of a missing record draws a meta line without throwing', () => {
  for (const raw of [null, undefined]) {
    const meta = taskMeta(raw);
    assert.ok(meta.querySelector('a[href="#/kanban"]'), 'the way back to the tasks is still there');
  }
});

test('rememberView with no scope gives back a restore that restores nothing, called with or without a target', () => {
  const restore = rememberView(null);
  assert.equal(restore(), false);
  const t = mount();
  assert.equal(restore(t.root), false);
  t.done();
});

test('rememberView: a menu button reading expanded is never clicked open again', () => {
  const menu = (expanded) => {
    const root = document.createElement('div');
    const button = document.createElement('button');
    button.type = 'button';
    button.dataset.focus = 'status-menu';
    button.setAttribute('aria-haspopup', 'menu');
    button.setAttribute('aria-expanded', expanded);
    root.appendChild(button);
    document.body.appendChild(root);
    return { root, button };
  };
  const before = menu('true');
  const restore = rememberView(before.root);
  before.root.remove();
  const after = menu('false');
  let clicks = 0;
  after.button.addEventListener('click', () => { clicks++; });
  restore(after.root);
  assert.equal(clicks, 0);
  after.root.remove();
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

test('linked bugs come from api.listBugs, after the sections and before the dates', async () => {
  const asked = [];
  const listBugs = async (q) => { asked.push(q); return [{ id: 'B-031', title: 'Card edge vanishes', status: 'open' }, { id: 'B-032', title: 'Fixed one', status: 'fixed' }, null]; };
  const api = { ...makeCtx().api, listBugs };
  const m = mount(FAKE_TASK, { api });
  await tick();
  assert.deepEqual(asked, [{ found_in: 'T-001' }]);
  const section = m.root.querySelector('[data-test="linked-bugs"]');
  assert.ok(section, 'the linked bugs section is shown');
  assert.deepEqual([...section.querySelectorAll('a')].map((a) => a.getAttribute('href')), ['#/bug/B-031', '#/bug/B-032']);
  assert.match(section.querySelector('.marker__word').textContent, /1 open bug blocking close/);
  // Each bug's status is a shape plus a word, from the shared bug status table.
  const statuses = [...section.querySelectorAll('.td-linked-bugs__status .marker')];
  assert.deepEqual(statuses.map((m) => m.querySelector('.marker__word').textContent), ['Open', 'Fixed']);
  // An open bug is not started (§5.1); the blocking line above carries the alarm, not each row.
  assert.deepEqual(statuses.map((m) => m.className), ['marker marker--neutral', 'marker marker--success']);
  assert.deepEqual(statuses.map((m) => m.querySelector('.marker__shape').dataset.shape), ['ring', 'dot']);
  const order = [...m.root.querySelector('.td-body').children].map((el) => el.dataset.test).filter(Boolean);
  assert.ok(order.indexOf('linked-bugs') < order.indexOf('dates'), order.join(','));
  m.done();
});

test('an inline save the server refuses with a 409 is an error with its reason, not a conflict', async () => {
  const reason = 'Completion blocked: review-gate is still open';
  const host = document.createElement('div');
  host.id = 'conflict-banner-host';
  document.body.appendChild(host);
  const api = { ...makeCtx().api, patchTask: async () => { throw Object.assign(new Error(reason), { code: 409 }); } };
  const t = mount(FAKE_TASK, { api });
  try {
    t.root.querySelector('[data-field="status"] .if-wrap').firstElementChild.click();
    const select = t.root.querySelector('[data-field="status"] select');
    select.value = 'done';
    select.dispatchEvent(new dom.window.Event('change'));
    for (let i = 0; i < 100 && !t.root.querySelector('[data-field="status"] .if-status-error'); i++) await tick(5);
    await tick(100);
    assert.equal(t.root.querySelector('[data-field="status"] .if-status-error')?.title, reason);
    assert.equal(host.children.length, 0, 'no conflict banner');
  } finally { t.done(); host.remove(); }
});

test('a refused title save in the dialog says why under the heading and leaves the heading exactly the title', async () => {
  const reason = 'Titles are frozen during review';
  const h2 = document.createElement('h2');
  const titleHost = document.createElement('span');
  h2.appendChild(titleHost);
  document.body.appendChild(h2);
  const api = { ...makeCtx().api, patchTask: async () => { throw Object.assign(new Error(reason), { code: 409 }); } };
  const t = mount(FAKE_TASK, { chrome: 'embedded', titleHost, api });
  const message = () => t.root.querySelector('.td-title-message');
  try {
    titleHost.querySelector('.ef-text').click();
    const input = titleHost.querySelector('input');
    input.value = 'Renamed';
    press(input, 'Enter');
    for (let i = 0; i < 100 && !message().querySelector('.if-error').textContent; i++) await tick(5);
    assert.equal(message().querySelector('.if-error').textContent, reason);
    assert.ok(input.getAttribute('aria-describedby').split(' ').includes(message().querySelector('.if-error').id),
      'the open editor is described by the message under the heading');
    assert.equal(h2.querySelector('.if-error'), null, 'no message in the heading');
    press(input, 'Escape');
    assert.equal(spoken(h2), 'Test task', 'the heading reads exactly the title');
    assert.equal(message().querySelector('.if-error').textContent, reason, 'and it is still said after the editor closes');
  } finally { t.done(); h2.remove(); }
});

// A save that landed is never reported as failed because the board refresh after it did not (M-3).
test('an inline save that landed stays saved when the board refresh after it fails', async () => {
  const store = { ...makeCtx().store, refreshBoard: async () => { throw new Error('GET /api/board → 503: down'); } };
  const t = mount(FAKE_TASK, { store });
  try {
    t.root.querySelector('[data-field="status"] .if-wrap').firstElementChild.click();
    const select = t.root.querySelector('[data-field="status"] select');
    select.value = 'in-progress';
    select.dispatchEvent(new dom.window.Event('change'));
    for (let i = 0; i < 100 && t.root.querySelector('[data-field="status"] select'); i++) await tick(5);
    assert.deepEqual(t.ctx._patches.map((p) => p.patch), [{ status: 'in-progress' }]);
    assert.equal(t.root.querySelector('[data-field="status"] select'), null, 'back to reading');
    assert.equal(t.root.querySelector('[data-field="status"] .marker__word').textContent, 'In progress');
    assert.equal(t.root.querySelector('[data-field="status"] .if-error').textContent, '');
    assert.equal(t.root.querySelector('[data-field="status"] .if-status-error'), null);
  } finally { t.done(); }
});

// The raw request never reaches the page (I-2).
test('an inline save on a task that was removed says so in a sentence, not as the request', async () => {
  const gone = Object.assign(new Error('PATCH /api/tasks/T-001 → 404: {"ok": false, "error": "task T-001 not found"}'), { code: 404 });
  const api = { ...makeCtx().api, patchTask: async () => { throw gone; } };
  const t = mount(FAKE_TASK, { api });
  try {
    t.root.querySelector('[data-field="status"] .if-wrap').firstElementChild.click();
    const select = t.root.querySelector('[data-field="status"] select');
    select.value = 'done';
    select.dispatchEvent(new dom.window.Event('change'));
    const message = () => t.root.querySelector('[data-field="status"] .if-error').textContent;
    for (let i = 0; i < 100 && !message(); i++) await tick(5);
    assert.match(message(), /no longer exists/);
    assert.doesNotMatch(message(), /PATCH|\/api\/|404|\{/);
  } finally { t.done(); }
});

// Two presses on Edit before the form's code has loaded open one form, with one edit lease (M-2).
test('Edit pressed twice in a row opens one form', async () => {
  const { openEditForm } = await import('../../js/components/task-detail-document.js');
  const { openModalCount } = await import('../../js/components/modal.js');
  const leases = [];
  const ctx = makeCtx();
  ctx.store = { ...ctx.store, beginEdit: (id) => leases.push(id), endEdit: () => {} };
  await Promise.all([openEditForm(ctx), openEditForm(ctx)]);
  await tick();
  try {
    assert.equal(document.querySelectorAll('.modal--form').length, 1);
    assert.deepEqual(leases, ['T-001']);
  } finally {
    for (const cancel of document.querySelectorAll('.modal--form [data-cancel]')) cancel.click();
    await tick();
    assert.equal(openModalCount(), 0);
  }
});
