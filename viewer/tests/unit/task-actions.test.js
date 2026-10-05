// User intent: what the task form sends is exactly what the user changed — a new task carries its defaults and what was
// typed, an edit carries only the changed fields, and a write that lost a race is resolved without losing either side.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

const PAGE = '<div class="shell"><section id="screen-mount"></section></div><div id="modal-host"></div><div id="conflict-banner-host"></div>';
const dom = new JSDOM(`<!doctype html><html><body>${PAGE}</body></html>`);
globalThis.document = dom.window.document;
globalThis.window = dom.window;
globalThis.HTMLElement = dom.window.HTMLElement;

const { openModalCount } = await import('../../js/components/modal.js');
const { openTaskCreateModal, openTaskEditModal } = await import('../../js/components/edit/task-actions.js');

const FAKE_BACKLOG = {
  tasks: [{ id: 't-001', title: 'Existing', status: 'todo', priority: 'medium', epic: 'core' }],
  epics: [{ id: 'core', label: 'Core' }, { id: 'ui', label: 'UI' }],
  phases: [{ id: 'P1' }],
  context: { active_epic: 'core' },
};
const TASK = {
  id: 't-001', title: 'Old title', status: 'todo', priority: 'medium', epic: 'core', phase: null, estimate: 3, stage: '',
  branch: '', depends_on: null, docs: { spec: 'docs/spec.md' }, anchors: [], description: 'Body\n', notes: null,
  created: '2026-09-01', last_referenced: '2026-09-30T10:00:00Z',
};

function makeStore() {
  const log = [];
  return {
    log,
    getBacklog: () => FAKE_BACKLOG,
    refreshBoard: async () => { log.push('refresh'); },
    beginEdit: (id) => log.push(`begin ${id}`),
    endEdit: (id) => log.push(`end ${id}`),
    setEtag: (key, etag) => log.push(`etag ${key} ${etag}`),
  };
}
// `patch` answers are taken in order: a function is called with (id, body), an Error is thrown, anything else returned.
function makeApi({ create, patch = [] } = {}) {
  const calls = [];
  const answers = [...patch];
  return {
    calls,
    createTask: async (payload) => { calls.push({ op: 'create', payload }); if (create) return create(payload); return { id: 't-new', ...payload }; },
    patchTask: async (id, body, opts) => {
      calls.push({ op: 'patch', id, body, ...(opts ? { opts } : {}) });
      const next = answers.shift();
      if (next instanceof Error) throw next;
      return typeof next === 'function' ? next(id, body) : (next ?? {});
    },
  };
}
const stale = (current, etag = 't1:fresh') => Object.assign(new Error('stale'), { code: 409, current, current_etag: etag });
// A 409 that names no revision: the server refusing the write (gates still open, a legacy layout), not a lost race.
const refusal = (reason) => Object.assign(new Error(reason), { code: 409 });

const tick = (ms = 0) => new Promise((ok) => setTimeout(ok, ms));
// The banner module is loaded on first use, which takes as long as the machine is busy.
async function until(probe, what) {
  for (let i = 0; i < 400; i++) { if (probe()) return; await tick(5); }
  assert.fail(`timed out waiting for ${what}`);
}
const fire = (el, type, init = {}) => el.dispatchEvent(Object.assign(new dom.window.Event(type, { bubbles: true, cancelable: true }), init));
const form = () => document.querySelector('.modal');
const field = (key) => form().querySelector(`[data-key="${key}"]`);
const control = (key) => document.getElementById(field(key).querySelector('label').getAttribute('for'));
const type = (key, text) => { const c = control(key); c.value = text; fire(c, 'input'); };
const saveBtn = () => form().querySelector('[data-save]');
const alertText = () => form().querySelector('.modal-footer [role="alert"]').textContent;
const banner = () => document.querySelector('#conflict-banner-host .cb-banner');
const bannerKeys = () => [...banner().querySelectorAll('.cb-multi-row .cb-key')].map((e) => e.textContent);
const closeForm = () => { form()?.querySelector('[data-cancel]').click(); document.querySelector('.modal--confirm [data-confirm]')?.click(); };

test.beforeEach(() => {
  assert.equal(openModalCount(), 0, 'the previous test left a modal open');
  document.body.innerHTML = PAGE;
});

test('create opens on the shared modal with its defaults, and those defaults are not a change', async () => {
  const store = makeStore();
  openTaskCreateModal({ store, api: makeApi() });
  await tick();
  assert.equal(form().querySelector('.modal-title').textContent, 'Create task');
  assert.equal(control('status').value, 'todo');
  assert.equal(control('priority').value, 'medium');
  assert.equal(control('epic').value, 'core', 'the active epic is prefilled');
  assert.equal(saveBtn().disabled, true, 'untouched: nothing to save');
  fire(document.activeElement, 'keydown', { key: 'Escape' });
  await tick();
  assert.equal(form(), null, 'closes without a confirm');
});

test('create honours an epic handed in by the caller', async () => {
  openTaskCreateModal({ store: makeStore(), api: makeApi(), prefillEpic: 'ui' });
  await tick();
  assert.equal(control('epic').value, 'ui');
  closeForm();
});

test('create saves one task with the defaults and the typed values, refreshes the board and closes', async () => {
  const store = makeStore();
  const api = makeApi();
  openTaskCreateModal({ store, api });
  await tick();
  for (const c of form().querySelectorAll('.modal-body input')) c.focus();
  type('title', 'New task from test');
  saveBtn().click();
  await tick(120);
  assert.deepEqual(api.calls, [{ op: 'create', payload: { epic: 'core', status: 'todo', priority: 'medium', title: 'New task from test' } }]);
  assert.deepEqual(store.log, ['refresh']);
  assert.equal(form(), null, 'closes on success');
});

test('create: a board refresh that fails after the task was made still closes the form, so it is never created twice (M4)', async () => {
  const store = makeStore();
  store.refreshBoard = async () => { store.log.push('refresh'); throw new Error('GET /api/board → 503'); };
  const api = makeApi();
  openTaskCreateModal({ store, api });
  await tick();
  type('title', 'Made once');
  saveBtn().click();
  await tick(50);
  assert.equal(form(), null, 'the task exists: the form closes');
  assert.equal(api.calls.length, 1);
  assert.deepEqual(store.log, ['refresh']);
});

test('create shows a server error and stays open; a 422 names its fields', async () => {
  let n = 0;
  const api = makeApi({
    create: () => { throw n++ ? Object.assign(new Error('validation failed'), { code: 422, errors: { epic: 'unknown epic', title: 'too long' } }) : new Error('Server down'); },
  });
  openTaskCreateModal({ store: makeStore(), api });
  await tick();
  type('title', 'Will fail');
  saveBtn().click();
  await tick();
  assert.ok(form(), 'stays open');
  assert.equal(alertText(), 'Server down');
  saveBtn().click();
  await tick();
  assert.equal(alertText(), 'epic: unknown epic · title: too long');
  closeForm();
});

test('edit opens prefilled, marks the task as being edited, and releases it on close', async () => {
  const store = makeStore();
  openTaskEditModal({ store, api: makeApi(), task: TASK });
  await tick();
  assert.equal(form().querySelector('.modal-title').textContent, 'Edit task');
  assert.equal(form().querySelector('.modal-eyebrow').textContent, 't-001');
  assert.equal(control('title').value, 'Old title');
  assert.equal(control('status').value, 'todo');
  assert.deepEqual(store.log, ['begin t-001']);
  closeForm();
  await tick();
  assert.deepEqual(store.log, ['begin t-001', 'end t-001']);
});

test('edit patches exactly the changed field, whatever shape the untouched ones are stored in', async () => {
  const store = makeStore();
  const api = makeApi();
  openTaskEditModal({ store, api, task: TASK });
  await tick();
  // Visit every field first: a blur must not turn null, '', [] or a bare number into a change.
  for (const toggle of form().querySelectorAll('.eform-section button[aria-expanded="false"]')) toggle.click();
  for (const c of form().querySelectorAll('.modal-body input, .modal-body select, .modal-body textarea')) c.focus();
  type('title', 'New title');
  saveBtn().click();
  await tick(120);
  assert.deepEqual(api.calls, [{ op: 'patch', id: 't-001', body: { title: 'New title' } }]);
  assert.deepEqual(store.log, ['begin t-001', 'refresh', 'end t-001']);
  assert.equal(form(), null, 'closes after the save');
});

test('edit never sends docs as a list: one edited row goes out as a map', async () => {
  const api = makeApi();
  openTaskEditModal({ store: makeStore(), api, task: TASK });
  await tick();
  const value = field('docs').querySelector('.ef-kv-value');
  value.value = 'docs/spec-v2.md';
  fire(value, 'input');
  saveBtn().click();
  await tick();
  assert.deepEqual(api.calls, [{ op: 'patch', id: 't-001', body: { docs: { spec: 'docs/spec-v2.md' } } }]);
});

// ── A write that lost a race ──
const SERVER = { ...TASK, title: 'Their title', priority: 'high', last_referenced: '2026-10-01T09:00:00Z' };

async function conflicted({ patch }) {
  const store = makeStore();
  const api = makeApi({ patch });
  openTaskEditModal({ store, api, task: TASK });
  await tick();
  type('title', 'My title');
  type('branch', 'feat/mine');
  saveBtn().click();
  await until(banner, 'the conflict banner');
  return { store, api };
}

test('409: the banner lists only the fields the user changed, the form is held, and focus moves to the banner', async () => {
  await conflicted({ patch: [stale(SERVER)] });
  assert.ok(banner());
  assert.deepEqual(bannerKeys(), ['title', 'branch'], 'not priority or last_referenced: the user never touched them');
  assert.equal(alertText(), 'Conflict — see banner');
  assert.equal(control('title').disabled, true);
  assert.equal(saveBtn().textContent, 'Save', 'not stuck on "Saving…"');
  assert.ok(banner().contains(document.activeElement), 'the banner is where the next key goes');
  banner().querySelector('.cb-dismiss').click();
  await tick();
  closeForm();
});

test('409 → "Apply choices" keeping mine: one more PATCH with only my fields, against the fresh revision, then the form closes', async () => {
  const { store, api } = await conflicted({ patch: [stale(SERVER), {}] });
  banner().querySelector('.cb-resolve').click();
  await tick();
  assert.deepEqual(api.calls.map((c) => c.body), [
    { title: 'My title', branch: 'feat/mine' },
    { title: 'My title', branch: 'feat/mine' },
  ]);
  assert.deepEqual(api.calls[1].opts, { ifMatch: 't1:fresh' }, 'the merged write names the revision the banner showed');
  // The write brings back the revision it made; the one it lost to is never stored over that (M2).
  assert.equal(store.log.some((l) => l.startsWith('etag')), false);
  assert.equal(banner(), null);
  assert.equal(form(), null, 'the merged save closed the form');
  assert.equal(store.log.at(-1), 'end t-001');
});

test('409 → taking the server\'s value for one field sends only the other', async () => {
  const { api } = await conflicted({ patch: [stale(SERVER), {}] });
  const row = [...banner().querySelectorAll('.cb-multi-row')].find((r) => r.querySelector('.cb-key').textContent === 'title');
  const useServer = row.querySelector('input[value="server"]');
  useServer.checked = true;
  fire(useServer, 'change');
  banner().querySelector('.cb-resolve').click();
  await tick();
  assert.deepEqual(api.calls[1].body, { branch: 'feat/mine' });
  assert.equal(form(), null);
});

test('409 → taking the server\'s value everywhere writes nothing and closes', async () => {
  const { store, api } = await conflicted({ patch: [stale(SERVER)] });
  for (const input of banner().querySelectorAll('input[value="server"]')) { input.checked = true; fire(input, 'change'); }
  banner().querySelector('.cb-resolve').click();
  await tick();
  assert.equal(api.calls.length, 1, 'no second PATCH');
  assert.equal(store.log.filter((l) => l === 'refresh').length, 1);
  assert.ok(store.log.includes('etag task:t-001 t1:fresh'), "the server's state was accepted: its revision is the one to write against next");
  assert.equal(form(), null);
});

test('409 → dismissing the banner returns to the form: open, editable, still dirty, the edits intact', async () => {
  const { api } = await conflicted({ patch: [stale(SERVER), stale(SERVER, 't1:newer')] });
  banner().querySelector('.cb-dismiss').click();
  await tick();
  assert.equal(banner(), null);
  assert.ok(form());
  assert.equal(control('title').disabled, false);
  assert.equal(control('title').value, 'My title');
  assert.equal(alertText(), '');
  assert.equal(saveBtn().disabled, false);
  assert.equal(saveBtn().textContent, 'Save');
  saveBtn().click();
  await until(banner, 'the second conflict banner');
  assert.equal(api.calls.length, 2, 'saving again asks the server again');
  banner().querySelector('.cb-dismiss').click();
  await tick();
  closeForm();
});

test('409 → a merged save that fails leaves the form open and editable with the reason', async () => {
  const { api } = await conflicted({ patch: [stale(SERVER), new Error('Server down')] });
  banner().querySelector('.cb-resolve').click();
  await tick();
  assert.equal(api.calls.length, 2);
  assert.equal(banner(), null);
  assert.ok(form());
  assert.equal(alertText(), 'Server down');
  assert.equal(control('title').disabled, false);
  assert.equal(saveBtn().disabled, false);
  closeForm();
});

test('409 → a merged save that fails never stores the fresh revision, so the next save is compared again instead of overwriting a "use server" pick (M2)', async () => {
  const { store, api } = await conflicted({ patch: [stale(SERVER), new Error('Server down'), stale(SERVER)] });
  const row = [...banner().querySelectorAll('.cb-multi-row')].find((r) => r.querySelector('.cb-key').textContent === 'title');
  const useServer = row.querySelector('input[value="server"]');
  useServer.checked = true;
  fire(useServer, 'change');
  banner().querySelector('.cb-resolve').click();
  await tick();
  assert.equal(alertText(), 'Server down');
  assert.equal(store.log.some((l) => l.startsWith('etag')), false, 'the revision the failed write named is not remembered');
  saveBtn().click();
  await until(banner, 'the banner, asked again');
  assert.equal(api.calls.length, 3);
  assert.equal(api.calls[2].opts, undefined, 'the retry goes out against the stored revision, not the fresh one');
  banner().querySelector('.cb-dismiss').click();
  await tick();
  closeForm();
});

test('409 → "Apply choices" cannot be pressed twice: the banner is held while the merged save is in flight (M5)', async () => {
  let land;
  const { api } = await conflicted({ patch: [stale(SERVER), () => new Promise((ok) => { land = ok; })] });
  const apply = banner().querySelector('.cb-resolve');
  apply.click();
  await tick();
  assert.equal(apply.disabled, true);
  assert.equal(banner().querySelector('.cb-dismiss').disabled, true, 'nor can the form be handed back mid-write');
  apply.click();
  await tick();
  assert.equal(api.calls.length, 2, 'one merged PATCH');
  land({});
  await tick();
  assert.equal(banner(), null);
  assert.equal(form(), null);
});

// ── A 409 that is not a lost race ──
test('a 409 that names no revision is the server refusing the write: its reason is shown, no banner, the stored revision untouched', async () => {
  const store = makeStore();
  const api = makeApi({ patch: [refusal('t-001: gate "review" is still open'), {}] });
  openTaskEditModal({ store, api, task: TASK });
  await tick();
  type('title', 'My title');
  saveBtn().click();
  await until(() => alertText(), 'the footer message');
  await tick(50);
  assert.equal(banner(), null, 'not "updated by another writer"');
  assert.equal(alertText(), 't-001: gate "review" is still open');
  assert.equal(store.log.some((l) => l.startsWith('etag')), false);
  assert.equal(control('title').disabled, false, 'the form is editable again');
  assert.equal(saveBtn().disabled, false);
  saveBtn().click();
  await tick();
  assert.equal(api.calls.length, 2);
  assert.equal(api.calls[1].opts, undefined, 'the retry carries the stored revision as before');
  assert.equal(form(), null);
});

test(`a refusing 409 during "Apply choices" shows the server's reason, not "changed again"`, async () => {
  const { store } = await conflicted({ patch: [stale(SERVER), refusal('t-001: gate "review" is still open')] });
  banner().querySelector('.cb-resolve').click();
  await tick();
  assert.equal(alertText(), 't-001: gate "review" is still open');
  assert.equal(store.log.some((l) => l.startsWith('etag')), false);
  assert.equal(control('title').disabled, false);
  closeForm();
});
