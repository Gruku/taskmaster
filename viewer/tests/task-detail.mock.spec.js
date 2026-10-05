// User intent: clicking a task card must open one proper dialog in a real browser — the task named once, its document
// readable in both themes, Edit stacked on top, every way out costing exactly one Back and handing focus back — and
// a task with absurdly long content must scroll inside the dialog without ever pushing it past the screen.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import {
  BOARD, DETAIL_TASK, RICH_RELATED, DONE_TASK, REVIEW_TASK, LONG_TASK, LONG_RELATED, taskDetail,
} from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

const DETAILS = {
  '/api/task/T-102/detail': taskDetail(DETAIL_TASK, 't1:fixture', RICH_RELATED),
  '/api/task/T-101/detail': taskDetail(DONE_TASK),
  '/api/task/T-107/detail': taskDetail(REVIEW_TASK),
  '/api/task/T-105/detail': taskDetail(LONG_TASK, 't1:fixture', LONG_RELATED),
};

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
});
// A write the mock did not expect means the page talked to an endpoint this spec never set up.
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

async function board(page, { theme = 'dark', table = {} } = {}) {
  await mockApi(page, {
    '/api/viewer/prefs': { theme, ui: {}, screens: {} },
    '/api/board': BOARD, '/api/backlog': BOARD, '/api/bugs': [],
    ...DETAILS, ...table,
  });
  await page.goto('/#/kanban');
  await expect(card(page, 'T-102')).toBeVisible();
}

const card = (page, id) => page.locator(`.card-task[data-task-id="${id}"]`);
const detail = (page) => page.locator('.modal--detail');
const titleOf = (dialog) => dialog.locator('.modal-title');

// Clicks the card and waits for the task's document (not the loading state) to be in the dialog.
async function openCard(page, id) {
  await card(page, id).click();
  const dialog = detail(page);
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('.td-doc--embedded')).toBeVisible();
  return dialog;
}

const modalEntry = (page) => page.evaluate(() => !!history.state?.detailModal);

test('a card opens the task as a dialog named by its title, shown once, with markdown rendered and no back link', async ({ page }) => {
  await board(page);
  const dialog = await openCard(page, 'T-102');
  await expect(page).toHaveURL(/#\/kanban$/);

  const labelledBy = await dialog.getAttribute('aria-labelledby');
  expect(labelledBy).toBeTruthy();
  await expect(page.locator(`[id="${labelledBy}"]`)).toHaveText(DETAIL_TASK.title);
  await expect(page.getByRole('dialog', { name: DETAIL_TASK.title })).toBeVisible();
  await expect(dialog.locator('.modal-eyebrow')).toHaveText('T-102');
  const times = await dialog.evaluate((d, t) => d.textContent.split(t).length - 1, DETAIL_TASK.title);
  expect(times).toBe(1);

  await expect(page.getByText('‹ back')).toHaveCount(0);
  await expect(dialog.locator('[data-test="sec-notes"] .md-body table')).toHaveCount(1);
  await expect(dialog.locator('[data-test="sec-notes"] .md-body h2')).toHaveText('Findings');

  // Header actions: Edit and Open full beside the shell's close button.
  await expect(dialog.getByRole('button', { name: 'Edit', exact: true })).toBeVisible();
  await expect(dialog.getByRole('link', { name: 'Open full' })).toHaveAttribute('href', '#/task/T-102');
  await expect(dialog.getByRole('button', { name: 'Close' })).toBeVisible();
});

test('status is a shape plus a word: Done and In progress differ in both', async ({ page }) => {
  await board(page);
  const status = (dialog) => dialog.locator('[data-field="status"] .marker');

  let dialog = await openCard(page, 'T-101');
  await expect(status(dialog).locator('.marker__word')).toHaveText('Done');
  await expect(status(dialog)).toHaveClass(/marker--success/);
  const doneShape = await status(dialog).locator('.marker__shape').getAttribute('data-shape');
  await page.keyboard.press('Escape');
  await expect(detail(page)).toHaveCount(0);

  dialog = await openCard(page, 'T-102');
  await expect(status(dialog).locator('.marker__word')).toHaveText('In progress');
  await expect(status(dialog)).not.toHaveClass(/marker--success/);
  const progressShape = await status(dialog).locator('.marker__shape').getAttribute('data-shape');
  expect(doneShape).toBe('dot');
  expect(progressShape).toBeTruthy();
  expect(progressShape).not.toBe(doneShape);
});

test('Edit opens the form on top; Escape closes only the form, then the dialog, and focus walks back to the card', async ({ page }) => {
  await board(page);
  const dialog = await openCard(page, 'T-102');
  const edit = dialog.getByRole('button', { name: 'Edit', exact: true });
  await edit.click();
  const form = page.getByRole('dialog', { name: 'Edit task' });
  await expect(form).toBeVisible();

  await page.keyboard.press('Escape');
  await expect(form).toHaveCount(0);
  await expect(dialog).toBeVisible();
  await expect(edit).toBeFocused();
  expect(await modalEntry(page)).toBe(true);

  await page.keyboard.press('Escape');
  await expect(detail(page)).toHaveCount(0);
  await expect(card(page, 'T-102')).toBeFocused();
  await expect(page).toHaveURL(/#\/kanban$/);
  expect(await modalEntry(page)).toBe(false);
});

test('Back with the Edit form on top closes the whole stack in one step', async ({ page }) => {
  await board(page);
  const dialog = await openCard(page, 'T-102');
  await dialog.getByRole('button', { name: 'Edit', exact: true }).click();
  await expect(page.getByRole('dialog', { name: 'Edit task' })).toBeVisible();

  await page.goBack();
  await expect(page.locator('.modal')).toHaveCount(0);
  await expect(page).toHaveURL(/#\/kanban$/);
  expect(await modalEntry(page)).toBe(false);
  await expect(card(page, 'T-102')).toBeFocused();
});

test('Back with unsaved edits asks first: "Keep editing" keeps everything and the entry, "Discard" closes the stack', async ({ page }) => {
  await board(page);
  const before = await page.evaluate(() => history.length);
  const dialog = await openCard(page, 'T-102');
  await dialog.getByRole('button', { name: 'Edit', exact: true }).click();
  const form = page.getByRole('dialog', { name: 'Edit task' });
  await expect(form).toBeVisible();
  await page.keyboard.type(' (draft)');
  const confirm = page.getByRole('dialog', { name: 'Discard changes?' });

  await page.goBack();
  await expect(confirm).toBeVisible();
  await confirm.getByRole('button', { name: 'Keep editing' }).click();
  await expect(confirm).toHaveCount(0);
  await expect(form).toBeVisible();
  await expect(dialog).toBeVisible();
  expect(await modalEntry(page), 'the refused Back leaves the dialog its entry').toBe(true);
  await expect(form.locator('[data-key="title"] input')).toHaveValue(/\(draft\)/);

  await page.goBack();
  await expect(confirm).toBeVisible();
  await confirm.getByRole('button', { name: 'Discard' }).click();
  await expect(page.locator('.modal')).toHaveCount(0);
  await expect(page).toHaveURL(/#\/kanban$/);
  expect(await modalEntry(page)).toBe(false);
  await expect(card(page, 'T-102')).toBeFocused();
  // Every entry the dialog put back was consumed again: Back from here leaves the board's own history alone.
  expect(await page.evaluate(() => history.state?.detailModal ?? null)).toBe(null);
  expect(await page.evaluate(() => history.length)).toBeGreaterThanOrEqual(before);
});

test('a card the board redrew while the dialog was open still gets focus back', async ({ page }) => {
  await board(page);
  await card(page, 'T-102').evaluate((el) => { el.dataset.before = 'redraw'; });
  await openCard(page, 'T-102');
  // A poll brings a new revision and the board repaints every card; the one that opened the dialog is gone.
  await page.evaluate(() => import('/js/store.js').then(({ store }) => {
    const next = structuredClone(store.getBacklog());
    next.revision = 'r2';
    next.tasks.find((t) => t.id === 'T-104').title = 'Sessions timeline: renamed by another writer';
    store.setBoard(next);
  }));
  await expect(page.locator('.card-task[data-before]')).toHaveCount(0);
  await expect(card(page, 'T-102')).toHaveCount(1);

  await page.keyboard.press('Escape');
  await expect(detail(page)).toHaveCount(0);
  await expect(card(page, 'T-102')).toBeFocused();
});

test('the close button and the browser Back both close the dialog, each through the one history entry', async ({ page }) => {
  await board(page);
  const before = await page.evaluate(() => history.length);
  let dialog = await openCard(page, 'T-102');
  expect(await page.evaluate(() => history.length)).toBe(before + 1);

  await page.goBack();
  await expect(detail(page)).toHaveCount(0);
  await expect(page).toHaveURL(/#\/kanban$/);
  await expect(card(page, 'T-102')).toBeFocused();

  dialog = await openCard(page, 'T-102');
  await dialog.getByRole('button', { name: 'Close' }).click();
  await expect(detail(page)).toHaveCount(0);
  expect(await modalEntry(page)).toBe(false);
  await expect(page).toHaveURL(/#\/kanban$/);
});

test('Open full replaces the dialog\'s entry with the task page, which shows the same template under an h1', async ({ page }) => {
  await board(page);
  const dialog = await openCard(page, 'T-102');
  await dialog.getByRole('link', { name: 'Open full' }).click();
  await expect(page).toHaveURL(/#\/task\/T-102$/);
  await expect(detail(page)).toHaveCount(0);

  const doc = page.locator('.td-doc--page');
  await expect(doc.locator('h1')).toHaveText(DETAIL_TASK.title);
  await expect(doc.locator('[data-test="meta"]')).toContainText('T-102');
  await expect(doc.locator('[data-field="status"] .marker__word')).toHaveText('In progress');
  await expect(doc.locator('[data-test="sec-notes"] .md-body table')).toHaveCount(1);
  await expect(doc.locator('[data-test="rail"] [data-panel="relations"]')).toBeVisible();
  await expect(page.getByText('‹ back')).toHaveCount(0);
  await expect(page.locator('main')).toHaveCount(1);

  // The dialog's entry was replaced, not stacked: one Back returns to the board, with no dialog reopening.
  await page.goBack();
  await expect(page).toHaveURL(/#\/kanban$/);
  await expect(detail(page)).toHaveCount(0);
});

test('peeking a dependency swaps the content in place, focuses its title, and keeps a single history entry', async ({ page }) => {
  await board(page);
  const before = await page.evaluate(() => history.length);
  const dialog = await openCard(page, 'T-102');
  await dialog.locator('[data-sub="depends"] a.td-dep', { hasText: 'T-101' }).click();

  await expect(titleOf(dialog)).toHaveText(DONE_TASK.title);
  await expect(dialog.locator('.modal-eyebrow')).toHaveText('T-101');
  await expect(dialog.getByRole('link', { name: 'Open full' })).toHaveAttribute('href', '#/task/T-101');
  await expect(titleOf(dialog)).toBeFocused();
  await expect(page.locator('.modal-overlay')).toHaveCount(1);
  expect(await page.evaluate(() => history.length)).toBe(before + 1);

  await page.goBack();
  await expect(detail(page)).toHaveCount(0);
  await expect(page).toHaveURL(/#\/kanban$/);
});

test('an inline save the server refuses (409 with no revision) shows its reason, raises no conflict banner, and the next save still sends If-Match', async ({ page }) => {
  const reason = 'Completion blocked: review-gate is still open';
  const sent = [];
  page.on('request', (r) => {
    if (r.method() === 'PATCH') sent.push({ path: new URL(r.url()).pathname, ifMatch: r.headers()['if-match'] ?? null });
  });
  await board(page, { table: { 'PATCH /api/tasks/T-102': { status: 409, json: { ok: false, error: reason } } } });
  const dialog = await openCard(page, 'T-102');
  const status = dialog.locator('[data-field="status"]');
  for (const value of ['done', 'in-review']) {
    await status.locator('.ef-editable').click();
    // The native select sits inside the marker picker; the change event is what the field listens to.
    await status.locator('select').evaluate((el, v) => { el.value = v; el.dispatchEvent(new Event('change', { bubbles: true })); }, value);
    await expect(status.locator('.if-status-error')).toHaveAttribute('title', reason);
    // The reason is read on the page, not hovered for: visible text, announced, describing the select.
    const message = status.locator('.if-error');
    await expect(message).toBeVisible();
    await expect(message).toHaveText(reason);
    await expect(message).toHaveAttribute('role', 'alert');
    const messageId = await message.getAttribute('id');
    expect((await status.locator('select').getAttribute('aria-describedby')).split(' ')).toContain(messageId);
    if (value === 'done') expect(await axe(page)).toEqual([]);
    await status.locator('select').press('Escape');
    await expect(message).toBeHidden();
    await expect(status.locator('select')).toHaveCount(0);
  }
  await expect(page.locator('#conflict-banner-host .cb-banner')).toHaveCount(0);
  expect(sent.length).toBe(2);
  expect(sent.every((s) => s.path === '/api/tasks/T-102' && s.ifMatch), JSON.stringify(sent)).toBe(true);
});

test('a task that fails to load says so in a sentence, offers Open full, and prints no raw API error', async ({ page }) => {
  await board(page, { table: { '/api/task/T-102/detail': { status: 500, json: { error: 'Traceback: KeyError depends_on' } } } });
  await card(page, 'T-102').click();
  const dialog = detail(page);
  await expect(dialog.locator('.tm-empty[data-state="error"]')).toBeVisible();
  await expect(dialog).toContainText('Could not load this task');
  await expect(dialog).not.toContainText('Traceback');
  await expect(dialog).not.toContainText('500');
  await expect(dialog.locator('.tm-empty').getByRole('link', { name: 'Open full' })).toHaveAttribute('href', '#/task/T-102');
});

// An inline field's save status sits beside it and is empty while idle. Beside a field that fills its line it used to
// open a blank line of its own: a gap under every markdown section and under a title that wraps.
for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  test(`at ${viewport.width}×${viewport.height} an idle inline field leaves no blank line under a section or a wrapped title`, async ({ page }) => {
    test.setTimeout(30_000);   // the long task renders a 5,000-line plan
    await page.setViewportSize(viewport);
    await board(page);
    // Bottom of the host minus bottom of the field's own content: only the host's padding may remain.
    const slack = (locator) => locator.evaluate((host) => {
      const wrap = host.querySelector('.if-wrap');
      return host.getBoundingClientRect().bottom - parseFloat(getComputedStyle(host).paddingBottom) - wrap.getBoundingClientRect().bottom;
    });
    let dialog = await openCard(page, 'T-102');
    for (const key of ['description', 'spec', 'plan', 'notes']) {
      expect(await slack(dialog.locator(`[data-test="sec-${key}"]`)), key).toBeLessThanOrEqual(1);
    }
    await page.keyboard.press('Escape');
    await expect(detail(page)).toHaveCount(0);
    dialog = await openCard(page, 'T-105');
    expect(await slack(titleOf(dialog)), 'wrapped title').toBeLessThanOrEqual(1);
  });
}

// Review focus 5: a 140-character unbroken title, a 5,000-line plan and 40 dependencies.
for (const viewport of [{ width: 1440, height: 900 }, { width: 390, height: 844 }]) {
  test(`long content at ${viewport.width}×${viewport.height}: the body scrolls, the header stays, nothing is wider than the screen`, async ({ page }) => {
    test.setTimeout(30_000);
    await page.setViewportSize(viewport);
    await board(page);
    const dialog = await openCard(page, 'T-105');
    await expect(dialog.locator('[data-test="sec-plan"] li').last()).toHaveText('step 5000 of the plan');
    await expect(dialog.locator('[data-sub="depends"] a.td-dep')).toHaveCount(40);

    const box = await dialog.boundingBox();
    expect(box.x).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width).toBeLessThanOrEqual(viewport.width);
    expect(box.y + box.height).toBeLessThanOrEqual(viewport.height);
    const widths = await page.evaluate(() => {
      const body = document.querySelector('.modal--detail .modal-body');
      const header = document.querySelector('.modal--detail .modal-header');
      return {
        page: document.documentElement.scrollWidth, view: innerWidth,
        body: body.scrollWidth, bodyBox: body.clientWidth,
        header: header.scrollWidth, headerBox: header.clientWidth,
        scrolls: body.scrollHeight > body.clientHeight,
      };
    });
    expect(widths.page).toBeLessThanOrEqual(widths.view);
    expect(widths.body).toBeLessThanOrEqual(widths.bodyBox);
    expect(widths.header).toBeLessThanOrEqual(widths.headerBox);
    expect(widths.scrolls).toBe(true);

    // The rail sits beside the body only when the dialog has its full width.
    const bodyBox = await dialog.locator('.td-body').boundingBox();
    const railBox = await dialog.locator('[data-test="rail"]').boundingBox();
    if (viewport.width >= 1440) expect(railBox.x).toBeGreaterThan(bodyBox.x + bodyBox.width - 1);
    else expect(railBox.y).toBeGreaterThan(bodyBox.y + bodyBox.height - 1);

    // Scrolled to the end of the plan, then to the last dependency: the header never moves.
    const footer = dialog.locator('.modal-footer');
    for (const end of [dialog.locator('[data-test="sec-plan"] li').last(), dialog.locator('[data-sub="depends"] a.td-dep').last()]) {
      await end.scrollIntoViewIfNeeded();
      await expect(end).toBeInViewport();
      await expect(dialog.locator('.modal-header')).toBeInViewport({ ratio: 1 });
      await expect(titleOf(dialog)).toBeInViewport();
      await expect(dialog.getByRole('button', { name: 'Close' })).toBeInViewport({ ratio: 1 });
      // The footer is empty here, so it is not shown; were it shown, it would have to stay on screen too.
      if (await footer.isVisible()) await expect(footer).toBeInViewport({ ratio: 1 });
      expect(await page.evaluate(() => scrollY), 'only the dialog body scrolls').toBe(0);
    }
    expect(await dialog.locator('.modal-body').evaluate((b) => b.scrollTop)).toBeGreaterThan(0);
  });
}

async function axe(page, scope = '.modal--detail') {
  await page.evaluate(axeSource);
  const result = await page.evaluate((sel) => window.axe.run(document.querySelector(sel), {
    runOnly: { type: 'rule', values: ['color-contrast', 'nested-interactive'] },
    resultTypes: ['violations'],
  }), scope);
  return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`);
}

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): the dialog has no contrast or nested-interactive violation, and the page one main`, async ({ page }) => {
    await board(page, { theme, table: { '/api/bugs': [{ id: 'B-031', title: 'Card edge vanishes', status: 'open' }] } });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);

    let dialog = await openCard(page, 'T-102');
    await expect(dialog.locator('[data-test="linked-bugs"]')).toBeVisible();
    await dialog.locator('.td-spec-toggle').click();
    await expect(dialog.locator('.td-codex-note')).toBeVisible();
    expect(await axe(page)).toEqual([]);
    await expect(page.locator('main')).toHaveCount(1);
    await page.keyboard.press('Escape');
    await expect(detail(page)).toHaveCount(0);

    // A task in review: failed and skipped gates, and the line of empty sections.
    dialog = await openCard(page, 'T-107');
    await expect(dialog.locator('[data-test="empty-sections"]')).toBeVisible();
    await expect(dialog.locator('[data-test="sec-review-instructions"]')).toBeVisible();
    expect(await axe(page)).toEqual([]);
    await expect(page.locator('main')).toHaveCount(1);
  });

  const RAIL_ROWS = ['[data-sub="depends"] a.td-dep', '[data-sub="unblocks"] a.td-dep', '[data-panel="issues"] a.td-issue', '[data-panel="docs"] a.td-doc-link'];
  async function hoverEach(page, scope, selector) {
    for (const row of RAIL_ROWS) {
      const el = scope.locator(row).first();
      await el.scrollIntoViewIfNeeded();
      await el.hover();
      await expect.poll(() => el.evaluate((a) => getComputedStyle(a).backgroundColor), { message: `${row} shows its hover fill` })
        .not.toBe('rgba(0, 0, 0, 0)');
      expect(await axe(page, selector), row).toEqual([]);
    }
  }

  test(`axe (${theme}): every hovered row in the dialog's rail keeps its text readable`, async ({ page }) => {
    await board(page, { theme });
    await hoverEach(page, await openCard(page, 'T-102'), '.modal--detail');
  });

  test(`axe (${theme}): every hovered row in the full page's rail keeps its text readable`, async ({ page }) => {
    await board(page, { theme });
    await page.goto('/#/task/T-102');
    const rail = page.locator('.td-doc--page [data-test="rail"]');
    await expect(rail).toBeVisible();
    await hoverEach(page, rail, '.td-doc--page [data-test="rail"]');
  });
}
