// User intent: the Create/Edit task form must behave in a real browser the way it claims — no native dialog, no
// complaint before typing, labels that reach their fields, a save that sends exactly what was changed, and a lost race
// that can be settled from the keyboard without losing the edit.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD, RICH_TASK, taskDetail } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');
const BOARD_ACTIVE = { ...BOARD, context: { active_epic: 'viewer' } };

let nativeDialogs;
test.beforeEach(async ({ page }) => {
  nativeDialogs = [];
  page.on('dialog', (d) => { nativeDialogs.push(d.message()); d.dismiss(); });
  await page.emulateMedia({ reducedMotion: 'reduce' });
});
test.afterEach(async ({ page }) => {
  expect(nativeDialogs, 'the form never uses a native browser dialog').toEqual([]);
  // A write the mock did not expect means the page talked to an endpoint this spec never set up.
  expect(unmockedWrites(page)).toEqual([]);
});

const base = (theme, table) => ({
  '/api/viewer/prefs': { theme, ui: {}, screens: {} },
  '/api/board': BOARD_ACTIVE, '/api/backlog': BOARD_ACTIVE,
  ...table,
});

async function openCreate(page, { theme = 'dark', table = {} } = {}) {
  await mockApi(page, base(theme, table));
  await page.goto('/#/kanban');
  // Add task is row 1's primary at every width.
  await expect(page.locator('#topbar-actions [data-global-search]')).toBeVisible();
  await page.locator('#topbar-primary [aria-label="Add task"]').click();
  const dialog = page.getByRole('dialog', { name: 'Create task' });
  await expect(dialog).toBeVisible();
  await expect(ctl(dialog, 'title')).toBeFocused();
  return dialog;
}

async function openEdit(page, { theme = 'dark', table = {}, task = RICH_TASK } = {}) {
  await mockApi(page, base(theme, { [`/api/task/${task.id}/detail`]: taskDetail(task), ...table }));
  await page.goto(`/#/task/${task.id}`);
  await page.getByTitle('Edit task').click();
  const dialog = page.getByRole('dialog', { name: 'Edit task' });
  await expect(dialog).toBeVisible();
  await expect(ctl(dialog, 'title')).toBeFocused();
  return dialog;
}

const field = (dialog, key) => dialog.locator(`[data-key="${key}"]`);
// The control a field's label points at.
const ctl = (dialog, key) => field(dialog, key).locator('input, select, textarea').first();
const save = (dialog) => dialog.getByRole('button', { name: 'Save', exact: true });
const cancel = (dialog) => dialog.getByRole('button', { name: 'Cancel', exact: true });
const confirmBox = (page) => page.getByRole('alertdialog', { name: 'Discard changes?' });
const writes = (page, method, pathname) => {
  const seen = [];
  page.on('request', (r) => {
    if (r.method() === method && new URL(r.url()).pathname === pathname) seen.push({ body: r.postDataJSON(), ifMatch: r.headers()['if-match'] ?? null });
  });
  return seen;
};

// ── Closing ──
test('an untouched Create form closes on Escape with no dialog of any kind', async ({ page }) => {
  const dialog = await openCreate(page);
  await expect(save(dialog)).toBeDisabled();
  await expect(dialog.locator('.ef-error:not(:empty)')).toHaveCount(0);
  await expect(dialog.locator('[aria-invalid="true"]')).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(page.locator('.modal')).toHaveCount(0);
  // Back on Add task, row 1's primary.
  await expect(page.locator('#topbar-primary [aria-label="Add task"]')).toBeFocused();
});

test('a typed title is guarded: Escape asks in-app, "Keep editing" returns to the title, "Discard" closes', async ({ page }) => {
  const dialog = await openCreate(page);
  await page.keyboard.type('Half a thought');
  await page.keyboard.press('Escape');
  await expect(confirmBox(page)).toBeVisible();
  await expect(confirmBox(page)).toContainText('Your edits to this task will be lost.');
  await expect(confirmBox(page).getByRole('button', { name: 'Keep editing' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(confirmBox(page)).toHaveCount(0);
  await expect(ctl(dialog, 'title')).toBeFocused();
  await expect(ctl(dialog, 'title')).toHaveValue('Half a thought');
  await page.keyboard.press('Escape');
  await confirmBox(page).getByRole('button', { name: 'Discard' }).click();
  await expect(page.locator('.modal')).toHaveCount(0);
});

test('an untouched Edit form stays clean after every field was visited, and closes without asking or writing', async ({ page }) => {
  const patches = writes(page, 'PATCH', '/api/tasks/T-102');
  const dialog = await openEdit(page);
  for (let i = 0; i < 45; i++) await page.keyboard.press('Tab');
  await expect(save(dialog)).toBeDisabled();
  await expect(dialog.locator('.ef-error:not(:empty)')).toHaveCount(0);
  await cancel(dialog).click();
  await expect(page.locator('.modal')).toHaveCount(0);
  expect(patches).toEqual([]);
});

// ── Escape belongs to the innermost thing that is open ──
test('Escape closes an open suggestion list or clears a half-typed entry first; only then does it reach the form', async ({ page }) => {
  const dialog = await openEdit(page);
  const deps = ctl(dialog, 'depends_on');
  await deps.fill('T-10');
  const list = field(dialog, 'depends_on').locator('.ef-chip-dropdown');
  await expect(list).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(list).toBeHidden();
  await expect(dialog).toBeVisible();
  await expect(confirmBox(page)).toHaveCount(0);
  await expect(deps).toBeFocused();

  const anchors = ctl(dialog, 'anchors');
  await anchors.focus();
  await page.keyboard.type('half/typed');
  await page.keyboard.press('Escape');
  await expect(anchors).toHaveValue('');
  await expect(dialog).toBeVisible();
  await expect(field(dialog, 'anchors').locator('.ef-chip')).toHaveCount(2);

  // Nothing of the field's own is open now: the key is the form's, and the form is clean.
  await page.keyboard.press('Escape');
  await expect(page.locator('.modal')).toHaveCount(0);
});

test('Escape with a select list open closes the list only; with it closed, Escape closes the form', async ({ page }) => {
  const dialog = await openCreate(page);
  await ctl(dialog, 'status').click();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeVisible();
  await expect(ctl(dialog, 'status')).toHaveValue('todo');
  await ctl(dialog, 'status').focus();
  await page.keyboard.press('Escape');
  await expect(page.locator('.modal')).toHaveCount(0);
});

// ── Labels and order ──
test('clicking a label focuses its control; a Content heading opens its section and puts the caret in it', async ({ page }) => {
  const dialog = await openEdit(page);
  for (const key of ['title', 'status', 'priority', 'epic', 'phase', 'estimate', 'stage', 'sub_repo', 'release', 'branch', 'worktree', 'depends_on', 'docs', 'anchors']) {
    await field(dialog, key).locator('label.eform-label').click();
    await expect(ctl(dialog, key), key).toBeFocused();
  }
  await field(dialog, 'review_instructions').getByRole('button', { name: /Review instructions/ }).click();   // closes
  await expect(ctl(dialog, 'review_instructions')).toBeHidden();
  await expect(field(dialog, 'review_instructions').locator('.eform-section-hint')).toHaveText(`${RICH_TASK.review_instructions.length} characters`);
  await field(dialog, 'review_instructions').getByRole('button', { name: /Review instructions/ }).click();   // opens
  await expect(ctl(dialog, 'review_instructions')).toBeFocused();
  for (const key of ['description', 'specification', 'plan', 'notes', 'review_instructions', 'patchnote']) {
    await expect(dialog.getByRole('textbox', { name: RegExp(`^${key === 'review_instructions' ? 'Review instructions' : key}$`, 'i') })).toHaveCount(1);
  }
});

test('with no docs yet there is one blank row: the Docs label reaches it, and "Add doc" keeps its own name', async ({ page }) => {
  const dialog = await openCreate(page);
  await expect(field(dialog, 'docs').locator('.ef-kv-row')).toHaveCount(1);
  await field(dialog, 'docs').locator('label.eform-label').click();
  await expect(dialog.getByRole('textbox', { name: 'Type, row 1' })).toBeFocused();
  await field(dialog, 'docs').getByRole('button', { name: 'Add doc' }).click();
  await expect(dialog.getByRole('textbox', { name: 'Type, row 2' })).toBeFocused();
  await expect(save(dialog)).toBeDisabled();   // blank rows are not a change
});

test('Tab follows the visual order, never leaves the dialog, and wraps', async ({ page }) => {
  const dialog = await openCreate(page);
  const where = () => page.evaluate(() => {
    const el = document.activeElement;
    const r = el.getBoundingClientRect();
    const inBody = !!el.closest('.modal-body');
    return {
      name: el.closest('[data-key]')?.dataset.key ?? el.textContent.trim() ?? el.className,
      inDialog: !!el.closest('.modal--form'), inBody, x: r.left, cy: r.top + r.height / 2 + (inBody ? el.closest('.modal-body').scrollTop : 0),
    };
  });
  const stops = [await where()];
  for (let i = 0; i < 30; i++) { await page.keyboard.press('Tab'); stops.push(await where()); }
  expect(stops.every((s) => s.inDialog)).toBe(true);
  expect(stops.map((s) => s.name)).toEqual([
    'title', 'status', 'priority', 'epic', 'phase', 'estimate', 'estimate', 'estimate', 'estimate', 'stage',
    'sub_repo', 'release', 'branch', 'worktree', 'depends_on',
    'docs', 'docs', 'docs', 'docs' /* type, path, remove, Add doc */, 'anchors',
    'description', 'description', 'specification', 'plan', 'notes', 'review_instructions', 'patchnote',
    'Cancel', '' /* the close button; Save is disabled on a clean form */, 'title', 'status',
  ]);
  const body = stops.slice(0, 27);
  for (let i = 1; i < body.length; i++) {
    const [a, b] = [body[i - 1], body[i]];
    const sameRow = Math.abs(a.cy - b.cy) < 14;
    expect(sameRow ? b.x > a.x : b.cy > a.cy, `${a.name} → ${b.name} reads forward`).toBe(true);
  }
  for (let i = 0; i < 3; i++) await page.keyboard.press('Shift+Tab');
  expect((await where()).name).toBe('Cancel');
});

// ── Validation and save ──
test('Save with an empty title: the title is focused, flagged and described, and the footer says "1 field needs attention"', async ({ page }) => {
  const posts = writes(page, 'POST', '/api/tasks');
  const dialog = await openCreate(page);
  await ctl(dialog, 'description').fill('Only a description so far');
  await expect(save(dialog)).toBeEnabled();
  await save(dialog).click();
  const title = ctl(dialog, 'title');
  await expect(title).toBeFocused();
  await expect(title).toHaveAttribute('aria-invalid', 'true');
  await expect(field(dialog, 'title').locator('.ef-error')).toHaveText('Title is required');
  expect(await title.evaluate((el) => document.getElementById(el.getAttribute('aria-describedby')).textContent)).toBe('Title is required');
  await expect(dialog.locator('[role="status"]')).toHaveText('1 field needs attention');
  await expect(dialog).toBeVisible();
  expect(posts).toEqual([]);
  await page.keyboard.type('Now named');
  await expect(dialog.locator('[role="status"]')).toHaveText('');
  await cancel(dialog).click();
  await confirmBox(page).getByRole('button', { name: 'Discard' }).click();
});

test('a successful Create sends one POST with the defaults and the typed values, and closes', async ({ page }) => {
  const posts = writes(page, 'POST', '/api/tasks');
  const dialog = await openCreate(page, { table: { 'POST /api/tasks': { id: 'T-108' } } });
  await page.keyboard.type('  Write the release notes ');
  await ctl(dialog, 'priority').selectOption('high');
  await field(dialog, 'estimate').getByRole('button', { name: 'M', exact: true }).click();
  await ctl(dialog, 'depends_on').fill('T-105');
  await page.keyboard.press('Enter');
  await dialog.getByRole('textbox', { name: 'Type, row 1' }).fill('spec');
  await page.keyboard.press('Tab');
  await page.keyboard.type('docs/release.md');
  await ctl(dialog, 'anchors').fill('CHANGELOG.md');   // left in the input, not turned into a chip by hand
  await ctl(dialog, 'description').fill('Collect the merged tasks.\n');
  await save(dialog).click();
  await expect(page.locator('.modal')).toHaveCount(0);
  expect(posts.map((p) => p.body)).toEqual([{
    epic: 'viewer', status: 'todo', priority: 'high', title: 'Write the release notes', estimate: 'M',
    depends_on: ['T-105'], docs: { spec: 'docs/release.md' }, anchors: ['CHANGELOG.md'], description: 'Collect the merged tasks.',
  }]);
});

test('Ctrl+Enter saves from inside a textarea', async ({ page }) => {
  const posts = writes(page, 'POST', '/api/tasks');
  const dialog = await openCreate(page, { table: { 'POST /api/tasks': { id: 'T-108' } } });
  await page.keyboard.type('From the keyboard');
  await ctl(dialog, 'description').focus();
  await page.keyboard.type('Body text');
  await page.keyboard.press('Control+Enter');
  await expect(page.locator('.modal')).toHaveCount(0);
  expect(posts.map((p) => p.body)).toEqual([{ epic: 'viewer', status: 'todo', priority: 'medium', title: 'From the keyboard', description: 'Body text' }]);
});

test('a message that appears when a field is left does not eat the click that left it', async ({ page }) => {
  const dialog = await openCreate(page);
  await expect(ctl(dialog, 'title')).toBeFocused();
  const size = field(dialog, 'estimate').getByRole('button', { name: 'S', exact: true });
  await size.click();
  await expect(size).toHaveAttribute('aria-pressed', 'true');
  await expect(field(dialog, 'title').locator('.ef-error')).toHaveText('Title is required');
  await cancel(dialog).click();
  await confirmBox(page).getByRole('button', { name: 'Discard' }).click();
});

test('Create refuses an epic that no longer exists in the form and sends nothing', async ({ page }) => {
  const posts = writes(page, 'POST', '/api/tasks');
  // The store derives the active epic from the tasks in flight (a mocked `context` is replaced), so the epic those
  // tasks belong to is one the board no longer lists.
  const gone = { ...BOARD, tasks: BOARD.tasks.map((t) => (t.status === 'in-progress' ? { ...t, epic: 'gone' } : t)) };
  const dialog = await openCreate(page, { table: { '/api/board': gone, '/api/backlog': gone } });
  await expect(ctl(dialog, 'epic')).toHaveValue('gone');
  await page.keyboard.type('Has a title');
  await save(dialog).click();
  await expect(field(dialog, 'epic').locator('.ef-error')).toHaveText('Unknown epic');
  await expect(ctl(dialog, 'epic')).toHaveAttribute('aria-invalid', 'true');
  await expect(ctl(dialog, 'epic')).toBeFocused();
  await expect(dialog.locator('[role="status"]')).toHaveText('1 field needs attention');
  expect(posts).toEqual([]);
  await cancel(dialog).click();
  await confirmBox(page).getByRole('button', { name: 'Discard' }).click();
  expect(posts).toEqual([]);
});

test('clicking blank space in the dialog after leaving a field shows its message', async ({ page }) => {
  const dialog = await openCreate(page);
  await ctl(dialog, 'title').fill('');
  await expect(field(dialog, 'title').locator('.ef-error')).toHaveText('');
  // The body's own padding: no control there, so focus lands on the dialog itself.
  await dialog.locator('.modal-body').click({ position: { x: 4, y: 4 } });
  await expect(ctl(dialog, 'title')).not.toBeFocused();
  await expect(field(dialog, 'title').locator('.ef-error')).toHaveText('Title is required');
  await expect(ctl(dialog, 'title')).toHaveAttribute('aria-invalid', 'true');
});

test('while saving the form says so, is disabled and cannot be closed; a server error leaves it open and editable', async ({ page }) => {
  let release;
  const gate = new Promise((ok) => { release = ok; });
  const dialog = await openCreate(page);
  await page.route('**/api/tasks', async (route) => {
    if (route.request().method() !== 'POST') return route.fallback();
    await gate;
    return route.fulfill({ status: 500, contentType: 'text/plain', body: 'store is locked' });
  });
  await page.keyboard.type('Slow one');
  await save(dialog).click();
  await expect(dialog.getByRole('button', { name: 'Saving…' })).toBeDisabled();
  await expect(ctl(dialog, 'title')).toBeDisabled();
  await expect(cancel(dialog)).toBeDisabled();
  await expect(dialog.getByRole('button', { name: 'Close' })).toBeDisabled();
  expect(await dialog.locator('button:enabled, input:enabled, select:enabled, textarea:enabled').count()).toBe(0);
  await page.keyboard.press('Escape');
  await page.mouse.click(5, 5);   // the overlay
  await expect(dialog).toBeVisible();
  await expect(confirmBox(page)).toHaveCount(0);
  release();
  // The failure is said in words; the server's raw text and status stay in the console.
  await expect(dialog.locator('[role="alert"]')).toHaveText('The server could not save this change. Try again in a moment.');
  await expect(save(dialog)).toBeEnabled();
  await expect(save(dialog)).toBeFocused();
  await expect(ctl(dialog, 'title')).toBeEnabled();
  await expect(ctl(dialog, 'title')).toHaveValue('Slow one');
  await cancel(dialog).click();
  await confirmBox(page).getByRole('button', { name: 'Discard' }).click();
});

// ── Edit: only what changed ──
test('Edit: changing only the title sends a PATCH with exactly { title }', async ({ page }) => {
  const patches = writes(page, 'PATCH', '/api/tasks/T-102');
  const dialog = await openEdit(page, { table: { 'PATCH /api/tasks/T-102': {} } });
  for (let i = 0; i < 45; i++) await page.keyboard.press('Tab');   // visit everything first
  await ctl(dialog, 'title').fill('Re-skin the board');
  await save(dialog).click();
  await expect(page.locator('.modal')).toHaveCount(0);
  expect(patches).toEqual([{ body: { title: 'Re-skin the board' }, ifMatch: 't1:fixture' }]);
});

test('Edit: docs round-trips as a map — one edited row and one added row, nothing else in the PATCH', async ({ page }) => {
  const patches = writes(page, 'PATCH', '/api/tasks/T-102');
  const dialog = await openEdit(page, { table: { 'PATCH /api/tasks/T-102': {} } });
  await expect(field(dialog, 'docs').locator('.ef-kv-row')).toHaveCount(2);
  await dialog.getByRole('textbox', { name: 'Path or URL, row 2' }).fill('docs/plans/new-plan.md');
  await field(dialog, 'docs').getByRole('button', { name: 'Add doc' }).click();
  await page.keyboard.type('review');
  await dialog.getByRole('textbox', { name: 'Path or URL, row 3' }).fill('https://example.com/review');
  await save(dialog).click();
  await expect(page.locator('.modal')).toHaveCount(0);
  expect(patches.map((p) => p.body)).toEqual([{
    docs: { spec: RICH_TASK.docs.spec, plan: 'docs/plans/new-plan.md', review: 'https://example.com/review' },
  }]);
});

test('Edit: a duplicate doc type is refused in the form and nothing is sent; removing the row clears it', async ({ page }) => {
  const patches = writes(page, 'PATCH', '/api/tasks/T-102');
  const dialog = await openEdit(page, { table: { 'PATCH /api/tasks/T-102': {} } });
  await dialog.getByRole('textbox', { name: 'Type, row 2' }).fill('spec');
  await save(dialog).click();
  await expect(field(dialog, 'docs').locator('.ef-error')).toHaveText('"spec" is used twice');
  await expect(dialog.locator('[role="status"]')).toHaveText('1 field needs attention');
  expect(patches).toEqual([]);
  await field(dialog, 'docs').getByRole('button', { name: 'Remove spec' }).last().click();
  await expect(dialog.locator('[role="status"]')).toHaveText('');
  await save(dialog).click();
  await expect(page.locator('.modal')).toHaveCount(0);
  expect(patches.map((p) => p.body)).toEqual([{ docs: { spec: RICH_TASK.docs.spec } }]);
});

test('Edit: stored values the form cannot represent are shown, and survive a save that changes something else', async ({ page }) => {
  const legacy = { ...RICH_TASK, estimate: '2 weeks', status: 'someday', phase: 'P0-retired', docs: ['spec: docs/spec.md'], depends_on: null };
  const patches = writes(page, 'PATCH', '/api/tasks/T-102');
  const dialog = await openEdit(page, { task: legacy, table: { 'PATCH /api/tasks/T-102': {} } });
  await expect(field(dialog, 'estimate').getByRole('button', { name: '2 weeks' })).toHaveAttribute('aria-pressed', 'true');
  await expect(field(dialog, 'estimate').locator('.ef-estimate-note')).toBeVisible();
  await expect(ctl(dialog, 'status')).toHaveValue('someday');
  await expect(ctl(dialog, 'phase')).toHaveValue('P0-retired');
  await expect(save(dialog)).toBeDisabled();
  await expect(field(dialog, 'estimate').locator('.ef-estimate-note')).toContainText('Current: 2 weeks');
  for (let i = 0; i < 45; i++) await page.keyboard.press('Tab');   // through every field, the estimate included
  await expect(save(dialog)).toBeDisabled();
  await ctl(dialog, 'title').fill('Re-skin the board');
  await expect(save(dialog)).toBeEnabled();
  await save(dialog).click();
  await expect(page.locator('.modal')).toHaveCount(0);
  expect(patches.map((p) => p.body)).toEqual([{ title: 'Re-skin the board' }]);
});

test('Edit: changing a legacy estimate to an invalid one is refused with a visible message', async ({ page }) => {
  const patches = writes(page, 'PATCH', '/api/tasks/T-102');
  const dialog = await openEdit(page, { task: { ...RICH_TASK, estimate: '2 weeks' } });
  await ctl(dialog, 'estimate').fill('0');
  await save(dialog).click();
  await expect(field(dialog, 'estimate').locator('.ef-error')).toHaveText('Use S, M, L or a whole number of days');
  await expect(dialog.locator('[role="status"]').last()).toHaveText('1 field needs attention');
  expect(patches).toEqual([]);
  await page.keyboard.press('Escape');
  await confirmBox(page).getByRole('button', { name: 'Discard' }).click();
});

test('a lone "-" or "e" in the days input does not clear the estimate: nothing changes and the field says what it expects', async ({ page }) => {
  const dialog = await openEdit(page);
  const days = ctl(dialog, 'estimate');
  const medium = field(dialog, 'estimate').getByRole('button', { name: 'M', exact: true });
  for (const key of ['-', 'e']) {
    await days.focus();
    await page.keyboard.type(key);
    await expect(field(dialog, 'estimate').locator('.ef-estimate-unreadable')).toHaveText('Use S, M, L or a whole number of days');
    await expect(medium).toHaveAttribute('aria-pressed', 'true');
    await expect(save(dialog)).toBeDisabled();
    await page.keyboard.press('Backspace');
    await expect(field(dialog, 'estimate').locator('.ef-estimate-unreadable')).toHaveText('');
  }
  await page.keyboard.press('Tab');
  await expect(save(dialog)).toBeDisabled();
  await page.keyboard.press('Escape');
  await expect(page.locator('.modal')).toHaveCount(0);
});

// ── A write that lost a race ──
const THEIRS = { ...RICH_TASK, title: 'Their title', priority: 'high', last_referenced: '2026-10-01T08:00:00Z' };
// The server's two kinds of 409: a lost race names the revision it lost to; a refusal only says why.
const STALE = { status: 409, json: { ok: false, error: 'stale', current: THEIRS, current_etag: 't1:fresh' } };
const REFUSED = { status: 409, json: { ok: false, error: 'T-102: blocking gate "review" is not cleared' } };
// Each PATCH takes the next answer (a fulfil object, or an async function of the route); past the list, success.
async function answerPatches(page, answers) {
  const patches = writes(page, 'PATCH', '/api/tasks/T-102');
  let n = 0;
  await page.route('**/api/tasks/T-102', async (route) => {
    if (route.request().method() !== 'PATCH') return route.fallback();
    const next = answers[n++];
    if (typeof next === 'function') return next(route);
    return route.fulfill(next ?? { json: { ...THEIRS, ...route.request().postDataJSON() } });
  });
  return patches;
}
async function conflict(page, later = []) {
  const dialog = await openEdit(page);
  const patches = await answerPatches(page, [STALE, ...later]);
  await ctl(dialog, 'title').fill('My title');
  await save(dialog).click();
  const banner = page.locator('#conflict-banner-host .cb-banner');
  await expect(banner).toBeVisible();
  return { dialog, banner, patches };
}

test('409 that refuses the write (no revision named): its reason shows in the form, no banner, and the next save still names the stored revision', async ({ page }) => {
  const dialog = await openEdit(page);
  const patches = await answerPatches(page, [REFUSED]);
  await ctl(dialog, 'title').fill('My title');
  await save(dialog).click();
  await expect(dialog.locator('[role="alert"]')).toHaveText('T-102: blocking gate "review" is not cleared');
  await expect(page.locator('#conflict-banner-host .cb-banner')).toHaveCount(0);
  await expect(ctl(dialog, 'title')).toBeEnabled();
  await expect(ctl(dialog, 'title')).toHaveValue('My title');
  await save(dialog).click();
  await expect(page.locator('.modal')).toHaveCount(0);
  expect(patches).toEqual([
    { body: { title: 'My title' }, ifMatch: 't1:fixture' },
    { body: { title: 'My title' }, ifMatch: 't1:fixture' },
  ]);
});

test('409: "Apply choices" is held while it writes; a merged save that fails returns the form with the reason, and the next save is compared again', async ({ page }) => {
  let land;
  const held = new Promise((ok) => { land = ok; });
  const { dialog, banner, patches } = await conflict(page, [
    async (route) => { await held; return route.fulfill({ status: 500, body: 'store is busy' }); },
    STALE,
  ]);
  const apply = banner.getByRole('button', { name: 'Apply choices' });
  await apply.click();
  await expect(apply).toBeDisabled();
  await expect(banner.getByRole('button', { name: 'Dismiss' })).toBeDisabled();
  land();
  await expect(banner).toHaveCount(0);
  await expect(dialog.locator('[role="alert"]')).toContainText('could not save this change');
  await expect(dialog.locator('[role="alert"]')).not.toContainText('500');
  await expect(ctl(dialog, 'title')).toBeEnabled();
  await save(dialog).click();
  await expect(page.locator('#conflict-banner-host .cb-banner')).toBeVisible();
  expect(patches.map((p) => p.ifMatch)).toEqual(['t1:fixture', 't1:fresh', 't1:fixture']);
  await page.locator('#conflict-banner-host').getByRole('button', { name: 'Dismiss' }).click();
  await page.keyboard.press('Escape');
  await confirmBox(page).getByRole('button', { name: 'Discard' }).click();
  await expect(page.locator('.modal')).toHaveCount(0);
});

test('409: a refusal while applying the banner\'s choices shows the server\'s reason, not "changed again"', async ({ page }) => {
  const { dialog, banner } = await conflict(page, [REFUSED]);
  await banner.getByRole('button', { name: 'Apply choices' }).click();
  await expect(banner).toHaveCount(0);
  await expect(dialog.locator('[role="alert"]')).toHaveText('T-102: blocking gate "review" is not cleared');
  await expect(ctl(dialog, 'title')).toBeEnabled();
  await page.keyboard.press('Escape');
  await confirmBox(page).getByRole('button', { name: 'Discard' }).click();
  await expect(page.locator('.modal')).toHaveCount(0);
});

test('409: the banner sits above the held form, lists only my change, and is worked from the keyboard', async ({ page }) => {
  const { dialog, banner, patches } = await conflict(page);
  await expect(dialog.locator('[role="alert"]')).toHaveText('Conflict — see banner');
  await expect(save(dialog)).toHaveText('Save');
  await expect(ctl(dialog, 'title')).toBeDisabled();
  await expect(banner.locator('.cb-key')).toHaveText(['Title']);
  // Painted above the modal: the point at each banner control is that control.
  for (const control of await banner.locator('input, button').all()) {
    expect(await control.evaluate((el) => { const r = el.getBoundingClientRect(); return el.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)) || el.parentElement.contains(document.elementFromPoint(r.left + r.width / 2, r.top + r.height / 2)); })).toBe(true);
  }
  expect(await page.evaluate(() => document.querySelector('#conflict-banner-host').contains(document.activeElement))).toBe(true);
  // Tab stays between the banner and the dialog; Escape in the banner does not close the form.
  for (let i = 0; i < 8; i++) {
    await page.keyboard.press('Tab');
    expect(await page.evaluate(() => !!document.activeElement.closest('#conflict-banner-host, .modal--form'))).toBe(true);
  }
  await banner.getByRole('button', { name: 'Apply choices' }).focus();
  await page.keyboard.press('Escape');
  await expect(dialog).toBeVisible();
  await expect(banner).toBeVisible();
  await page.keyboard.press('Enter');
  await expect(page.locator('.modal')).toHaveCount(0);
  await expect(banner).toHaveCount(0);
  expect(patches).toEqual([
    { body: { title: 'My title' }, ifMatch: 't1:fixture' },
    { body: { title: 'My title' }, ifMatch: 't1:fresh' },
  ]);
});

test('409: dismissing the banner returns to an editable form with the edit intact', async ({ page }) => {
  const { dialog, banner, patches } = await conflict(page);
  await banner.getByRole('button', { name: 'Dismiss' }).click();
  await expect(banner).toHaveCount(0);
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('[role="alert"]')).toHaveText('');
  await expect(ctl(dialog, 'title')).toBeEnabled();
  await expect(ctl(dialog, 'title')).toHaveValue('My title');
  await expect(save(dialog)).toBeEnabled();
  await expect(save(dialog)).toBeFocused();
  expect(patches.length).toBe(1);
  await page.keyboard.press('Escape');
  await confirmBox(page).getByRole('button', { name: 'Discard' }).click();
  await expect(page.locator('.modal')).toHaveCount(0);
});

// ── The relation list inside a scrolling dialog ──
for (const [width, height] of [[1440, 900], [390, 844]]) {
  test(`${width}×${height}: the relation list opened at the bottom edge of the form is fully visible and uncovered`, async ({ page }) => {
    await page.setViewportSize({ width, height });
    const dialog = await openEdit(page);
    const input = ctl(dialog, 'depends_on');
    await input.evaluate((el) => el.scrollIntoView({ block: 'end' }));
    await input.fill('T-10');
    const rows = field(dialog, 'depends_on').locator('.ef-chip-dd-row');
    // T-101..T-107, less T-101 (already chosen) and T-102 (the task itself).
    await expect(rows).toHaveCount(5);
    const report = await page.evaluate(() => {
      const box = (el) => { const r = el.getBoundingClientRect(); return { top: r.top, bottom: r.bottom, left: r.left, right: r.right }; };
      const dialogBox = box(document.querySelector('.modal--form'));
      const bodyBox = box(document.querySelector('.modal--form .modal-body'));
      const input = document.activeElement;
      return {
        dialogBox, bodyBox, input: box(input),
        rows: [...document.querySelectorAll('.ef-chip-dd-row')].map((row) => {
          const r = box(row);
          const hit = document.elementFromPoint((r.left + r.right) / 2, (r.top + r.bottom) / 2);
          return { ...r, uncovered: row.contains(hit) };
        }),
      };
    });
    for (const row of report.rows) {
      expect(row.uncovered).toBe(true);
      expect(row.top).toBeGreaterThanOrEqual(report.bodyBox.top - 0.5);
      expect(row.bottom).toBeLessThanOrEqual(report.bodyBox.bottom + 0.5);
      expect(row.left).toBeGreaterThanOrEqual(report.dialogBox.left - 0.5);
      expect(row.right).toBeLessThanOrEqual(report.dialogBox.right + 0.5);
    }
    // Still attached to its input: right under it, or right above it when there is no room below.
    const first = report.rows[0];
    const last = report.rows.at(-1);
    const below = first.top >= report.input.bottom && first.top - report.input.bottom < 12;
    const above = last.bottom <= report.input.top && report.input.top - last.bottom < 12;
    expect(below || above, JSON.stringify({ input: report.input, first, last })).toBe(true);
    await page.keyboard.press('Escape');
  });
}

test('Depends on never offers the task itself', async ({ page }) => {
  const dialog = await openEdit(page);
  const input = ctl(dialog, 'depends_on');
  await expect(input).toHaveAttribute('role', 'combobox');
  await input.fill('T-10');
  const list = page.getByRole('listbox', { name: 'Depends on suggestions' });
  await expect(list).toBeVisible();
  await expect(input).toHaveAttribute('aria-expanded', 'true');
  await expect(input).toHaveAttribute('aria-controls', await list.getAttribute('id'));
  const offered = await list.getByRole('option').allTextContents();
  expect(offered.some((t) => t.startsWith('T-103'))).toBe(true);
  expect(offered.some((t) => t.startsWith('T-102'))).toBe(false);
  // T-101 is already chosen; the add list offers it again only once it is removed.
  await page.keyboard.press('Escape');
  await field(dialog, 'depends_on').getByRole('button', { name: /^Remove T-101/ }).click();
  await input.fill('T-10');
  await expect(list.getByRole('option', { name: /^T-101/ })).toHaveCount(1);
  await expect(list.getByRole('option', { name: /^T-102/ })).toHaveCount(0);
  await page.keyboard.press('Escape');
  await expect(list).toHaveCount(0);
  await expect(dialog).toBeVisible();
});

// ── Phone ──
test('390×844: one column, nothing wider than the screen, Save and Cancel in view without scrolling', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const dialog = await openEdit(page);
  const boxes = await page.evaluate(() => Object.fromEntries(['status', 'priority', 'epic', 'phase'].map((k) => {
    const r = document.querySelector(`.modal--form [data-key="${k}"]`).getBoundingClientRect();
    return [k, { left: r.left, top: r.top, right: r.right }];
  })));
  expect(boxes.priority.left).toBe(boxes.status.left);
  expect(boxes.priority.top).toBeGreaterThan(boxes.status.top);
  expect(boxes.phase.top).toBeGreaterThan(boxes.epic.top);
  const overflow = await page.evaluate(() => {
    const body = document.querySelector('.modal--form .modal-body');
    const wide = [...document.querySelectorAll('.modal--form *')].filter((el) => el.getBoundingClientRect().right > window.innerWidth + 0.5 && !el.closest('.eform-sr'));
    return { page: document.documentElement.scrollWidth, body: body.scrollWidth - body.clientWidth, wide: wide.map((el) => el.className), scrollY: window.scrollY };
  });
  expect(overflow.page).toBeLessThanOrEqual(390);
  expect(overflow.body).toBeLessThanOrEqual(0);
  expect(overflow.wide).toEqual([]);
  expect(overflow.scrollY).toBe(0);
  await expect(save(dialog)).toBeInViewport({ ratio: 1 });
  await expect(cancel(dialog)).toBeInViewport({ ratio: 1 });
  await expect(dialog.getByRole('button', { name: 'Close' })).toBeInViewport({ ratio: 1 });
});

// ── Accessibility ──
const AXE_RULES = /^(color-contrast|label|select-name|button-name|aria-.+)$/;
async function axe(page) {
  await page.evaluate(axeSource);
  const result = await page.evaluate(() => window.axe.run(document.querySelector('.modal--form'), { resultTypes: ['violations'] }));
  return result.violations.filter((v) => AXE_RULES.test(v.id)).map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`);
}

// While saving, and for as long as the conflict banner holds it, the form's controls are disabled; its body must still
// scroll from the keyboard, or a keyboard user cannot read what they are being asked to settle (M-10).
test('a held form can still be scrolled from the keyboard: saving, and held by the conflict banner', async ({ page }) => {
  let land;
  const held = new Promise((ok) => { land = ok; });
  const dialog = await openEdit(page);
  await answerPatches(page, [async (route) => { await held; return route.fulfill(STALE); }]);
  const body = dialog.locator('.modal-body');
  expect(await body.evaluate((b) => b.scrollHeight > b.clientHeight), 'the full task overflows the form').toBe(true);
  const scrollable = async () => {
    await page.evaluate(axeSource);
    const result = await page.evaluate(() => window.axe.run(document.querySelector('.modal--form'), {
      runOnly: { type: 'rule', values: ['scrollable-region-focusable'] }, resultTypes: ['violations'],
    }));
    return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`);
  };
  expect(await scrollable()).toEqual([]);
  await ctl(dialog, 'title').fill('My title');
  await save(dialog).click();
  await expect(dialog.getByRole('button', { name: 'Saving…' })).toBeDisabled();
  expect(await scrollable(), 'saving').toEqual([]);
  // Reached with Tab, the body scrolls with the arrow keys.
  await page.keyboard.press('Tab');
  await expect(body).toBeFocused();
  await page.keyboard.press('PageDown');
  await expect.poll(() => body.evaluate((b) => b.scrollTop)).toBeGreaterThan(0);

  land();
  const banner = page.locator('#conflict-banner-host .cb-banner');
  await expect(banner).toBeVisible();
  await expect(ctl(dialog, 'title')).toBeDisabled();
  expect(await scrollable(), 'held by the banner').toEqual([]);
  await banner.getByRole('button', { name: 'Dismiss' }).click();
  await expect(ctl(dialog, 'title')).toBeEnabled();
  await expect(body).not.toHaveAttribute('tabindex');
  await page.keyboard.press('Escape');
  await confirmBox(page).getByRole('button', { name: 'Discard' }).click();
  await expect(page.locator('.modal')).toHaveCount(0);
});

async function unscrollable(page) {
  await page.evaluate(axeSource);
  const result = await page.evaluate(() => window.axe.run(document.querySelector('.modal--form'), {
    runOnly: ['scrollable-region-focusable'], resultTypes: ['violations'],
  }));
  return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`);
}

test('axe: no scroll region in a saving Create form is out of the keyboard\'s reach', async ({ page }) => {
  const dialog = await openCreate(page);
  // Held like capture-modals' holdCreate: the POST never answers while the form is looked at.
  await page.route('**/api/tasks', (route) => (route.request().method() === 'POST' ? undefined : route.fallback()));
  await page.keyboard.type('Write the release notes for 7.1');
  await ctl(dialog, 'description').fill(Array.from({ length: 40 }, (_, i) => `Line ${i + 1}`).join('\n'));
  await save(dialog).click();
  await expect(dialog.getByRole('button', { name: 'Saving…' })).toBeDisabled();
  expect(await unscrollable(page)).toEqual([]);
});

test('axe: no scroll region in a form held by the conflict banner is out of the keyboard\'s reach', async ({ page }) => {
  const { dialog, banner } = await conflict(page);
  await expect(ctl(dialog, 'title')).toBeDisabled();
  expect(await unscrollable(page)).toEqual([]);
  await banner.getByRole('button', { name: 'Dismiss' }).click();
  await page.keyboard.press('Escape');
  await confirmBox(page).getByRole('button', { name: 'Discard' }).click();
});

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): the Create form, clean and with its messages shown`, async ({ page }) => {
    const dialog = await openCreate(page, { theme });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    expect(await axe(page)).toEqual([]);
    await ctl(dialog, 'description').fill('x');
    await ctl(dialog, 'stage').fill('-1');
    await save(dialog).click();
    await expect(dialog.locator('[role="status"]')).toHaveText('2 fields need attention');
    expect(await axe(page)).toEqual([]);
    await cancel(dialog).click();
    await confirmBox(page).getByRole('button', { name: 'Discard' }).click();
  });

  test(`axe (${theme}): the Edit form of a full task with every section open`, async ({ page }) => {
    const dialog = await openEdit(page, { theme, task: { ...RICH_TASK, estimate: '2 weeks', status: 'someday' } });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(dialog.locator('.eform-section-toggle[aria-expanded="true"]')).toHaveCount(6);
    expect(await axe(page)).toEqual([]);
  });

  test(`axe (${theme}): the Depends on suggestion list, open with a highlighted option`, async ({ page }) => {
    const dialog = await openEdit(page, { theme });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await ctl(dialog, 'depends_on').fill('T-10');
    await expect(page.getByRole('listbox', { name: 'Depends on suggestions' })).toBeVisible();
    await page.keyboard.press('ArrowDown');
    expect(await axe(page)).toEqual([]);
  });
}
