// User intent: the one popover must behave in a real browser — a press outside closes it and still does what it says,
// Escape closes only the popover inside a dialog, a redraw of its button takes it away with nothing left listening,
// and it stays on screen, solid and accessible in both themes.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD, DETAIL_TASK, RICH_RELATED, taskDetail } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
});
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

async function mock(page, theme = 'dark') {
  await mockApi(page, {
    '/api/viewer/prefs': { theme, ui: {}, screens: {} },
    '/api/board': BOARD, '/api/backlog': BOARD, '/api/bugs': [],
    '/api/task/T-102/detail': taskDetail(DETAIL_TASK, 't1:fixture', RICH_RELATED),
    'POST /api/handover/2026-09-30-kanban-reskin/status': { ok: true },
  });
}

async function taskPage(page, theme) {
  await mock(page, theme);
  await page.goto('/#/task/T-102');
  await expect(page.locator('.td-doc--page')).toBeVisible();
}

// The detail dialog over the board, with the task's document (not the loading state) in it.
async function dialogOver(page) {
  await mock(page);
  await page.goto('/#/kanban');
  await page.locator('.card-task[data-task-id="T-102"]').click();
  const dialog = page.locator('.modal--detail');
  await expect(dialog.locator('.td-doc--embedded')).toBeVisible();
  return dialog;
}

const menu = (page) => page.locator('.ho-status-menu');
const openCount = (page) => page.evaluate(() => import('/js/components/popover.js').then((m) => m.openPopoverCount()));

test('a click outside the menu closes it and still lands on what was clicked', async ({ page }) => {
  await taskPage(page);
  await page.locator('.ho-status-pill').click();
  await expect(menu(page)).toBeVisible();
  const toggle = page.locator('.td-spec-toggle');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await toggle.click();
  await expect(menu(page)).toHaveCount(0);
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
});

test('Escape closes the menu only, then the dialog', async ({ page }) => {
  const dialog = await dialogOver(page);
  const pill = dialog.locator('.ho-status-pill');
  await pill.click();
  await expect(menu(page)).toBeVisible();
  await expect(menu(page).getByRole('menuitemradio', { name: 'open' })).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(menu(page)).toHaveCount(0);
  await expect(dialog).toBeVisible();
  await expect(pill).toBeFocused();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
});

test('a menu whose button is redrawn closes with it and leaves no listener behind', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  const dialog = await dialogOver(page);
  await dialog.locator('.ho-status-pill').click();
  await expect(menu(page)).toBeVisible();

  // Another writer renames the task; the board's next revision redraws the dialog's document.
  await page.route('**/api/task/T-102/detail', (route) => route.fulfill({
    json: taskDetail({ ...DETAIL_TASK, title: 'Renamed by another writer' }, 't1:other', RICH_RELATED),
  }));
  await page.evaluate(() => import('/js/store.js').then(({ store }) => {
    const next = structuredClone(store.getBacklog());
    next.revision = `r-${Date.now()}`;
    store.setBoard(next);
  }));
  await expect(dialog.locator('.modal-title')).toHaveText('Renamed by another writer');
  await expect(menu(page)).toHaveCount(0);
  expect(await openCount(page)).toBe(0);
  expect(errors).toEqual([]);

  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('placement: inside a phone viewport, and above the button when there is no room below', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await taskPage(page);
  const pill = page.locator('.ho-status-pill');
  await pill.click();
  await expect(menu(page)).toBeVisible();
  const box = await menu(page).boundingBox();
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.y).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
  expect(box.y + box.height).toBeLessThanOrEqual(844);
  await page.keyboard.press('Escape');
  await expect(menu(page)).toHaveCount(0);

  await pill.evaluate((p) => p.scrollIntoView({ block: 'end' }));
  const at = await pill.boundingBox();
  expect(at.y + at.height).toBeGreaterThan(844 - 60);
  await pill.click();
  await expect(menu(page)).toBeVisible();
  const above = await menu(page).boundingBox();
  expect(above.y + above.height).toBeLessThanOrEqual(at.y);
  expect(above.y).toBeGreaterThanOrEqual(0);
});

for (const theme of ['dark', 'light']) {
  test(`${theme}: the open menu has no shadow and no axe violation`, async ({ page }) => {
    await taskPage(page, theme);
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await page.locator('.ho-status-pill').click();
    await expect(menu(page)).toBeVisible();
    expect(await page.locator('.popover').evaluate((el) => getComputedStyle(el).boxShadow)).toBe('none');
    await page.evaluate(axeSource);
    const result = await page.evaluate(() => window.axe.run(document.querySelector('.popover'), { resultTypes: ['violations'] }));
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}
