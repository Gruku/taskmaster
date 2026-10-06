// User intent: the full task page must hold up as a frame — the Document/Graph switch shows what is on screen, Edit is
// the page's one primary in row 1, a load that fails says so in words with one way on, and another writer, a reload
// mid-edit or leaving the page (while it loads, or with Edit open) never paints over the user or loses their typing.
// Its Graph view is walked by keyboard — nodes are links, tabs move by arrow keys — and holds at phone width.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD, DETAIL_TASK, DONE_TASK, LONG_TASK, LONG_RELATED, RICH_RELATED, taskDetail, taskPageMocks } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');
const DETAIL = '/api/task/T-102/detail';
const TABLE = taskPageMocks();

let errors;
let patches;
test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  errors = [];
  patches = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('request', (r) => { if (r.method() === 'PATCH') patches.push(new URL(r.url()).pathname); });
});
// A write the mock did not expect means the page talked to an endpoint this spec never set up.
test.afterEach(async ({ page }) => {
  expect(unmockedWrites(page)).toEqual([]);
  expect(errors).toEqual([]);
});

// The document classes the mount itself, so its own node is the one to look for.
const doc = (page) => page.locator('#screen-mount.td-doc');
const h1 = (page) => page.locator('#screen-mount h1.td-title');

// Waits on the view's loaded heading (the brief's loaded-selectors); pass `loaded: null` for a page that never loads.
async function open(page, hash = '#/task/T-102', table = {}, { loaded = /[?&]view=B\b/.test(hash) ? '.td-page-B h1.td-title' : '.td-page-A h1.td-title' } = {}) {
  await mockApi(page, { ...TABLE, ...table });
  await page.goto('/' + hash);
  if (loaded) await expect(page.locator(`#screen-mount${loaded}, #screen-mount ${loaded}`).first()).toBeVisible();
}

// Another writer changes the task: the next detail read has `task`, and a poll brings a new board revision.
async function changedElsewhere(page, task) {
  await page.route('**/api/task/T-102/detail', (route) => route.fulfill({ json: taskDetail(task, 't1:other', RICH_RELATED) }));
  await bumpRevision(page);
}
// Another writer renames the task: the next detail read has the new title, and a poll brings a new board revision.
async function renamedElsewhere(page, title) {
  await page.route('**/api/task/T-102/detail', (route) => route.fulfill({ json: taskDetail({ ...DETAIL_TASK, title }, 't1:other', RICH_RELATED) }));
  await bumpRevision(page);
}
function bumpRevision(page) {
  return page.evaluate(() => import('/js/store.js').then(({ store }) => {
    const next = structuredClone(store.getBacklog());
    next.revision = `r-${Date.now()}`;
    store.setBoard(next);
  }));
}

// Holds the next detail read until the test releases it with the detail to answer; later reads answer that detail too.
async function holdDetail(page) {
  let release;
  const gate = new Promise((resolve) => { release = resolve; });
  await page.route('**/api/task/T-102/detail', async (route) => {
    const json = await gate;
    await route.fulfill({ json });
  });
  return release;
}

test('the view switch shows the view on screen and saves the choice', async ({ page }) => {
  const saves = [];
  page.on('request', (r) => { if (r.method() === 'PUT' && r.url().endsWith('/api/viewer/prefs')) saves.push(r.postDataJSON()); });
  await open(page, '#/task/T-102?view=B');
  const pressed = page.locator('#topbar-actions .tm-segmented button[aria-pressed="true"]');
  await expect(pressed).toHaveText('Graph');
  await expect(page.locator('.td-page-B')).toBeVisible();

  await page.locator('#topbar-actions .tm-segmented').getByRole('button', { name: 'Document' }).click();
  await expect(pressed).toHaveText('Document');
  await expect(page.locator('.td-page-A')).toBeVisible();
  expect(await page.evaluate(() => location.hash)).toBe('#/task/T-102');
  await expect.poll(() => saves.at(-1)?.screens?.task_detail?.view).toBe('A');

  await page.locator('#topbar-actions .tm-segmented').getByRole('button', { name: 'Graph' }).click();
  await expect(pressed).toHaveText('Graph');
});

test('Edit sits in row 1 as the page\'s primary and the switch alone in row 2', async ({ page }) => {
  await open(page);
  await expect(doc(page)).toBeVisible();
  const edit = page.locator('#topbar-primary [title="Edit task"]');
  await expect(edit).toHaveClass(/(^|\s)btn(\s|$)/);
  await expect(edit).toHaveClass(/(^|\s)btn--primary(\s|$)/);
  await expect(page.locator('#topbar-actions [title="Edit task"]')).toHaveCount(0);

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(edit).toBeVisible();
  expect((await edit.boundingBox()).height).toBeGreaterThanOrEqual(44);
  // At 390 the primary shows its icon only, and still says "Edit" to a screen reader.
  await expect(edit.locator('> .icon')).toBeVisible();
  await expect(edit.locator('> span')).toBeHidden();
  await expect(page.locator('#topbar-primary').getByRole('button', { name: /Edit/ })).toHaveCount(1);
});

test('another writer\'s change waits while a section is edited on the page, then shows', async ({ page }) => {
  await open(page);
  await expect(doc(page)).toBeVisible();
  await page.locator('[data-focus="edit:notes"]').click();
  const textarea = page.locator('[data-section="notes"] textarea');
  await expect(textarea).toBeFocused();
  await textarea.press('Control+End');
  await page.keyboard.type(' more');

  await renamedElsewhere(page, 'Renamed elsewhere');
  await page.waitForTimeout(300);
  await expect(h1(page)).toHaveText(DETAIL_TASK.title);
  await expect(textarea).toBeFocused();
  expect(await textarea.inputValue()).toMatch(/ more$/);

  await page.keyboard.press('Escape');
  await expect(h1(page)).toHaveText('Renamed elsewhere');
  expect(await page.evaluate(() => document.activeElement !== document.body && document.activeElement.isConnected)).toBe(true);
  expect(patches).toEqual([]);
});

test('a reload already under way when the title editor opens does not paint over it', async ({ page }) => {
  await open(page);
  await expect(doc(page)).toBeVisible();
  const release = await holdDetail(page);
  const started = page.waitForRequest('**/api/task/T-102/detail');
  await bumpRevision(page);
  await started;

  await h1(page).locator('.ef-editable').click();
  const input = h1(page).locator('input');
  await input.fill('Draft');
  release(taskDetail({ ...DETAIL_TASK, title: 'Renamed elsewhere' }, 't1:other', RICH_RELATED));
  await page.waitForTimeout(300);
  await expect(input).toHaveCount(1);
  await expect(input).toBeFocused();
  await expect(input).toHaveValue('Draft');

  await page.keyboard.press('Escape');
  await expect(h1(page)).toHaveText('Renamed elsewhere');
  expect(patches).toEqual([]);
});

test('leaving the page while it loads leaves nothing behind', async ({ page }) => {
  await mockApi(page, TABLE);
  const release = await holdDetail(page);
  const started = page.waitForRequest('**/api/task/T-102/detail');
  await page.goto('/#/task/T-102');
  await started;
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('.card-task').first()).toBeVisible();
  release(taskDetail(DETAIL_TASK, 't1:fixture', RICH_RELATED));
  await page.waitForTimeout(300);

  await expect(page.locator('#screen-mount .td-doc, #screen-mount.td-doc')).toHaveCount(0);
  await expect(h1(page)).toHaveCount(0);
  await expect(page.locator('#topbar-primary [title="Edit task"]')).toHaveCount(0);
  await expect(page.locator('.tm-segmented button', { hasText: 'Graph' })).toHaveCount(0);
});

test('leaving with Edit open: clean closes, typed asks', async ({ page }) => {
  await open(page);
  await expect(doc(page)).toBeVisible();
  const form = page.getByRole('dialog', { name: 'Edit task' });
  await page.locator('#topbar-primary [title="Edit task"]').click();
  await expect(form).toBeVisible();
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('.modal')).toHaveCount(0);
  await expect(page.getByRole('alertdialog')).toHaveCount(0);

  await page.evaluate(() => { location.hash = '#/task/T-102'; });
  await expect(doc(page)).toBeVisible();
  await page.locator('#topbar-primary [title="Edit task"]').click();
  await expect(form).toBeVisible();
  const title = form.locator('[data-key="title"]').locator('input, select, textarea').first();
  await expect(title).toBeFocused();
  await page.keyboard.press('End');
  await page.keyboard.type(' (draft)');
  await page.evaluate(() => { location.hash = '#/kanban'; });
  const confirm = page.getByRole('alertdialog', { name: 'Discard changes?' });
  await expect(confirm).toBeVisible();

  await confirm.getByRole('button', { name: 'Keep editing' }).click();
  await expect(confirm).toHaveCount(0);
  await expect(form).toBeVisible();
  await expect(title).toHaveValue(`${DETAIL_TASK.title} (draft)`);

  await page.keyboard.press('Escape');
  await expect(confirm).toBeVisible();
  await confirm.getByRole('button', { name: 'Discard' }).click();
  await expect(page.locator('.modal')).toHaveCount(0);
  expect(patches).toEqual([]);
});

test('a load that fails says so in words and offers Try again', async ({ page }) => {
  let answer = { status: 500, json: { error: 'Traceback: KeyError depends_on' } };
  await mockApi(page, TABLE);
  await page.route('**/api/task/T-102/detail', (route) => route.fulfill(answer));
  await page.goto('/#/task/T-102');
  const block = page.locator('#screen-mount .tm-empty[data-state="error"]');
  await expect(block).toBeVisible();
  await expect(block.locator('.tm-empty__label')).toHaveText('T-102');
  await expect(block.locator('.tm-empty__headline')).toHaveText('Could not load this task');
  const words = await page.locator('#screen-mount').innerText();
  for (const raw of ['Traceback', '500', '/api']) expect(words).not.toContain(raw);
  await expect(page.locator('#topbar-primary > *')).toHaveCount(0);
  await expect(page.locator('#topbar-actions .tm-segmented')).toHaveCount(0);

  answer = { json: taskDetail(DETAIL_TASK, 't1:fixture', RICH_RELATED) };
  await block.getByRole('button', { name: 'Try again' }).click();
  await expect(h1(page)).toHaveText(DETAIL_TASK.title);
});

test('Try again says it is loading, then puts focus on the title, or on the new Try again when it fails again', async ({ page }) => {
  await mockApi(page, TABLE);
  await page.route('**/api/task/T-102/detail', (route) => route.fulfill({ status: 500, json: { error: 'boom' } }));
  await page.goto('/#/task/T-102');
  const failed = page.locator('#screen-mount .tm-empty[data-state="error"]');
  await failed.getByRole('button', { name: 'Try again' }).focus();

  // Holds the next detail read until the test releases it with the whole response to answer.
  const hold = async () => {
    let release;
    const answer = new Promise((resolve) => { release = resolve; });
    await page.route('**/api/task/T-102/detail', async (route) => route.fulfill(await answer));
    return release;
  };

  // Fails again: the busy block shows while the read is held, then focus lands on the new Try again.
  let release = await hold();
  await page.keyboard.press('Enter');
  const busy = page.locator('#screen-mount .tm-empty[aria-busy="true"][role="status"]');
  await expect(busy).toHaveText('Loading…');
  await expect(busy).toBeFocused();
  release({ status: 500, json: { error: 'boom' } });
  await expect(failed.getByRole('button', { name: 'Try again' })).toBeFocused();

  // Succeeds: focus lands on the page's h1, not <body>.
  release = await hold();
  await page.keyboard.press('Enter');
  await expect(busy).toBeVisible();
  release({ json: taskDetail(DETAIL_TASK, 't1:fixture', RICH_RELATED) });
  await expect(h1(page)).toHaveText(DETAIL_TASK.title);
  await expect(h1(page)).toBeFocused();
});

async function axe(page, selector, rules = ['color-contrast']) {
  await page.evaluate(axeSource);
  const result = await page.evaluate(([sel, values]) => window.axe.run(document.querySelector(sel), {
    runOnly: { type: 'rule', values }, resultTypes: ['violations'],
  }), [selector, rules]);
  return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`);
}

for (const theme of ['dark', 'light']) {
  test(`gates and the epic read as words in both themes (${theme})`, async ({ page }) => {
    await open(page, '#/task/T-102', { '/api/viewer/prefs': { theme, ui: {}, screens: {} } });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    const strip = page.locator('#screen-mount [data-test="gate-pipeline"]');
    await expect(strip).toBeVisible();
    const words = (await strip.innerText()).replace(/\s+/g, ' ');
    expect(words).toContain('Spec review passed');
    expect(words).toContain('Plan review passed with warnings');
    expect(words).toContain('Review gate pending');
    expect(words).not.toContain('review-gate:pending');
    const epic = page.locator('#screen-mount [data-tag="epic"]');
    await expect(epic.locator('.td-tag__v')).toHaveText('Viewer re-skin');
    expect(await epic.getAttribute('style')).toBeNull();
    await expect(epic.locator('.td-swatch')).toHaveClass(/td-swatch--cat-\d/);
    expect(await axe(page, '#screen-mount [data-test="gate-pipeline"]')).toEqual([]);
    expect(await axe(page, '#screen-mount [data-tag="epic"]')).toEqual([]);
  });
}

test('a refused reason goes when another writer changes that field, and stays when they change another', async ({ page }) => {
  const reason = 'Completion blocked: review-gate is still open';
  await open(page, '#/task/T-102', { 'PATCH /api/tasks/T-102': { status: 409, json: { ok: false, error: reason } } });
  await expect(doc(page)).toBeVisible();
  const status = page.locator('#screen-mount [data-field="status"]');
  await status.locator('.ef-editable').click();
  const select = status.locator('select');
  await expect(select).toBeFocused();
  await select.selectOption('done');
  await expect(status.locator('.if-error')).toHaveText(reason);
  await page.keyboard.press('Tab');
  await expect(select).toHaveCount(0);
  await expect(status.locator('.if-error')).toHaveText(reason);

  await changedElsewhere(page, { ...DETAIL_TASK, title: 'Renamed elsewhere' });
  await expect(h1(page)).toHaveText('Renamed elsewhere');
  await expect(status.locator('.if-error'), 'another field changed: the reason still stands').toHaveText(reason);

  await changedElsewhere(page, { ...DETAIL_TASK, title: 'Renamed elsewhere', status: 'in-review' });
  await expect(status.locator('.marker__word')).toHaveText('In review');
  await expect(status.locator('.if-error'), 'the field itself changed: the reason is gone').toHaveText('');
});

test('a lost race on Status names both statuses in words', async ({ page }) => {
  let patches = 0;
  page.on('request', (r) => { if (r.method() === 'PATCH' && r.url().includes('/api/tasks/T-102')) patches += 1; });
  await open(page, '#/task/T-102', { 'PATCH /api/tasks/T-102': { status: 409, json: {
    ok: false, error: 'stale', current: { ...DETAIL_TASK, status: 'in-review' }, current_etag: 't1:fresh',
  } } });
  await expect(doc(page)).toBeVisible();
  const status = page.locator('#screen-mount [data-field="status"]');
  await status.locator('.ef-editable').click();
  await status.locator('select').selectOption('done');
  const banner = page.locator('#conflict-banner-host .cb-banner');
  await expect(banner.locator('.cb-val-mine')).toHaveText('Done');
  await expect(banner.locator('.cb-val-server')).toHaveText('In review');
  expect(await banner.textContent()).not.toContain('in-review');
  expect(patches).toBe(1);
  await banner.locator('.cb-use-server').click();
  await expect(banner).toHaveCount(0);
  expect(patches, 'Use server writes nothing').toBe(1);
});

// ── Graph view ──
const GRAPH = '#/task/T-102?view=B';
const graph = (page) => page.locator('#screen-mount.td-page-B');

test('the graph walks by keyboard: nodes are links and the tabs move by arrow keys', async ({ page }) => {
  await open(page, GRAPH, { '/api/task/T-101/detail': taskDetail(DONE_TASK) });
  await expect(graph(page)).toBeVisible();
  await expect(page.locator('#screen-mount h1')).toHaveCount(1);
  await page.locator('#screen-mount [data-test="task-id"]').focus();
  let href = null;
  for (let i = 0; i < 12 && href !== '#/task/T-101'; i++) {
    await page.keyboard.press('Tab');
    href = await page.evaluate(() => document.activeElement?.getAttribute('href') ?? null);
  }
  expect(href, 'Tab reaches the first dependency node').toBe('#/task/T-101');
  expect(await page.evaluate(() => document.activeElement.closest('svg')?.getAttribute('role'))).toBe('group');

  await page.keyboard.press('Enter');
  await expect.poll(() => page.evaluate(() => location.hash)).toBe('#/task/T-101');
  await expect(page.locator('#screen-mount h1')).toHaveText(DONE_TASK.title);

  await page.goBack();
  await expect.poll(() => page.evaluate(() => location.hash)).toBe(GRAPH);
  await expect(graph(page)).toBeVisible();
  await expect(page.locator('#screen-mount h1')).toHaveText(DETAIL_TASK.title);

  const tab = (name) => page.locator('#screen-mount [role="tablist"]').getByRole('tab', { name });
  await tab('Spec').focus();
  await page.keyboard.press('ArrowRight');
  await expect(tab('Plan')).toHaveAttribute('aria-selected', 'true');
  await expect(tab('Plan')).toBeFocused();
  await expect(tab('Spec')).toHaveAttribute('aria-selected', 'false');
  const panelId = await tab('Plan').getAttribute('aria-controls');
  await expect(page.locator(`#${panelId}`)).toBeVisible();
});

test('the graph at 390 scrolls nothing sideways and every cut label keeps its full text', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page, '#/task/T-105?view=B', { '/api/task/T-105/detail': taskDetail(LONG_TASK, 't1:fixture', LONG_RELATED) });
  await expect(page.locator('#screen-mount svg.td-graph-svg')).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
  const labels = await page.evaluate(() => [...document.querySelectorAll('#screen-mount text.node-title')].map((t) => ({
    shown: t.textContent,
    full: [...t.parentNode.children].find((c) => c.localName === 'title')?.textContent ?? '',
  })));
  const cutOnes = labels.filter((l) => l.shown.endsWith('…'));
  expect(cutOnes.length).toBeGreaterThan(0);
  for (const l of cutOnes) expect(l.full.length, l.shown).toBeGreaterThan(l.shown.length);
});

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): the graph view has no contrast, nested-interactive or name violation`, async ({ page }) => {
    await open(page, GRAPH, { '/api/viewer/prefs': { theme, ui: {}, screens: {} } });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.locator('#screen-mount svg.td-graph-svg')).toBeVisible();
    expect(await axe(page, '#screen-mount', ['color-contrast', 'nested-interactive', 'link-name', 'svg-img-alt', 'aria-allowed-role', 'aria-required-children'])).toEqual([]);
  });
}

test('a repaint of the graph keeps the open tab, focus on it, and the hidden context band', async ({ page }) => {
  await open(page, GRAPH);
  await expect(graph(page)).toBeVisible();
  const hide = page.locator('#screen-mount [data-test="graph-controls"]').getByRole('button', { name: 'Hide context' });
  await hide.click();
  await expect(page.locator('#screen-mount [data-test="context-band"]')).toBeHidden();
  const raw = page.locator('#screen-mount [role="tablist"]').getByRole('tab', { name: 'Raw JSON' });
  await raw.click();
  await expect(raw).toBeFocused();

  await renamedElsewhere(page, 'Renamed elsewhere');
  await expect(page.locator('#screen-mount h1')).toHaveText('Renamed elsewhere');
  await expect(raw).toHaveAttribute('aria-selected', 'true');
  await expect(raw).toBeFocused();
  await expect(page.locator('#screen-mount .td-tab-panel[data-tab-panel="raw"]')).toBeVisible();
  await expect(page.locator('#screen-mount [data-test="context-band"]')).toBeHidden();
  await expect(hide).toHaveAttribute('aria-pressed', 'true');
});

test('at 390 the graph\'s issue links and controls are touch-sized', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page, GRAPH);
  await expect(graph(page)).toBeVisible();
  const targets = page.locator('#screen-mount :is(.td-graph-context-band a.ctx-pill, .td-graph-controls .btn, .td-tab)');
  expect(await targets.count()).toBeGreaterThan(2);
  for (const box of await targets.evaluateAll((els) => els.map((el) => [el.textContent, el.getBoundingClientRect().height]))) {
    expect(box[1], box[0]).toBeGreaterThanOrEqual(44);
  }
});

test('fullscreen keeps a tall graph scrollable and its way out in reach, and its toggle reads pressed', async ({ page }) => {
  await open(page, '#/task/T-105?view=B', { '/api/task/T-105/detail': taskDetail(LONG_TASK, 't1:fixture', LONG_RELATED) });
  const full = page.locator('#screen-mount [data-test="graph-controls"] [data-focus="graph:fullscreen"]');
  await expect(full).toHaveText('Fullscreen');
  await full.click();
  await expect.poll(() => page.evaluate(() => document.fullscreenElement?.matches('.td-graph-frame') ?? false)).toBe(true);
  await expect(full).toHaveText('Fullscreen');
  await expect(full).toHaveAttribute('aria-pressed', 'true');
  const m = await page.evaluate(() => {
    const canvas = document.querySelector('.td-graph-frame .td-graph-canvas');
    const controls = document.querySelector('.td-graph-frame .td-graph-controls').getBoundingClientRect();
    return { scrolls: canvas.scrollHeight > canvas.clientHeight, controlsBottom: controls.bottom, vh: innerHeight };
  });
  expect(m.scrolls, 'the tall graph scrolls inside the canvas').toBe(true);
  expect(m.controlsBottom, 'the controls row stays on screen').toBeLessThanOrEqual(m.vh);
  await full.click();
  await expect.poll(() => page.evaluate(() => document.fullscreenElement)).toBeNull();
  await expect(full).toHaveText('Fullscreen');
  await expect(full).toHaveAttribute('aria-pressed', 'false');
});

test('a repaint of the same task keeps the graph in fullscreen, on the same frame, showing the new data', async ({ page }) => {
  await open(page, GRAPH);
  const full = page.locator('#screen-mount [data-test="graph-controls"] [data-focus="graph:fullscreen"]');
  await full.click();
  await expect.poll(() => page.evaluate(() => document.fullscreenElement?.matches('.td-graph-frame') ?? false)).toBe(true);
  await page.evaluate(() => { document.fullscreenElement.dataset.marked = 'before'; });

  await renamedElsewhere(page, 'Renamed elsewhere');
  await expect(page.locator('#screen-mount h1')).toHaveText('Renamed elsewhere');
  await expect(page.locator('#screen-mount .node--center')).toHaveAttribute('aria-label', /Renamed elsewhere/);
  const after = await page.evaluate(() => ({
    same: document.fullscreenElement?.dataset.marked === 'before',
    frames: document.querySelectorAll('#screen-mount .td-graph-frame').length,
  }));
  expect(after.same, 'the frame that fills the screen is the one the click put there').toBe(true);
  expect(after.frames).toBe(1);
  await expect(full).toHaveAttribute('aria-pressed', 'true');
  await full.click();
  await expect.poll(() => page.evaluate(() => document.fullscreenElement)).toBeNull();
});

test('leaving the task page while the graph fills the screen leaves fullscreen', async ({ page }) => {
  await open(page, GRAPH);
  await page.locator('#screen-mount [data-test="graph-controls"] [data-focus="graph:fullscreen"]').click();
  await expect.poll(() => page.evaluate(() => document.fullscreenElement?.matches('.td-graph-frame') ?? false)).toBe(true);
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('#screen-mount.td-page-B')).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => document.fullscreenElement)).toBeNull();
});
