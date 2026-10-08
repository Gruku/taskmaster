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

test('placement: a list opened while its dialog is still rising ends up at its field', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const detail = await dialogOver(page);
  await detail.locator('[data-action="edit"]').click();
  const form = page.locator('.modal--form');
  await expect(form).toBeVisible();
  // Typed at once, while the Edit dialog's rise is still running and its transform makes it the list's frame.
  const input = form.locator('[data-key="depends_on"] input').first();
  await input.fill('T-1');
  const list = page.getByRole('listbox', { name: 'Depends on suggestions' });
  await expect(list).toBeVisible();
  await page.evaluate(() => Promise.all(document.getAnimations().filter((a) => a.effect?.getTiming().iterations !== Infinity).map((a) => a.finished)));
  await page.evaluate(() => new Promise((ok) => requestAnimationFrame(() => ok())));
  const field = await input.boundingBox();
  const box = await list.boundingBox();
  expect(Math.abs(box.x - field.x)).toBeLessThanOrEqual(1);
  const gap = box.y >= field.y ? box.y - (field.y + field.height) : field.y - (box.y + box.height);
  expect(gap).toBeGreaterThanOrEqual(0);
  expect(gap).toBeLessThanOrEqual(8);
  await page.keyboard.press('Escape');
});

for (const theme of ['dark', 'light']) {
  test(`${theme}: the open menu has no shadow and no axe violation`, async ({ page }) => {
    await taskPage(page, theme);
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await page.locator('.ho-status-pill').click();
    await expect(menu(page)).toBeVisible();
    expect(await page.locator('.popover').evaluate((el) => getComputedStyle(el).boxShadow)).toBe('none');
    // Status words are capitalised by the status menu; a generic popover item renders its text as written.
    expect(await page.locator('.ho-status-menu-item').first().evaluate((el) => getComputedStyle(el).textTransform)).toBe('capitalize');
    expect(await page.evaluate(() => {
      const probe = document.createElement('button');
      probe.className = 'popover-item';
      document.querySelector('.popover').appendChild(probe);
      const t = getComputedStyle(probe).textTransform;
      probe.remove();
      return t;
    })).toBe('none');
    await page.evaluate(axeSource);
    const result = await page.evaluate(() => window.axe.run(document.querySelector('.popover'), { resultTypes: ['violations'] }));
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}

// The detail dialog over the board in a theme and a viewport; at phone width the card's column is chosen first.
async function detailAt(page, { width = 1440, height = 900, theme = 'dark' } = {}) {
  await page.setViewportSize({ width, height });
  await mock(page, theme);
  await page.goto('/#/kanban');
  const card = page.locator('.card-task[data-task-id="T-102"]');
  await card.waitFor({ state: 'attached' });
  if (width <= 768) {
    const panel = await card.evaluate((c) => c.closest('.kanban-col').id);
    await page.locator(`[id="${panel}-tab"]`).click();
  }
  await card.click();
  const dialog = page.locator('.modal--detail');
  await expect(dialog.locator('.td-doc--embedded')).toBeVisible();
  return dialog;
}
const settle = (page) => page.evaluate(async () => {
  await Promise.all(document.getAnimations().filter((a) => a.effect?.getTiming().iterations !== Infinity).map((a) => a.finished));
  await new Promise((ok) => requestAnimationFrame(() => requestAnimationFrame(() => ok())));
});

for (const [width, height] of [[1440, 900], [390, 844]]) {
  test(`placement: in the detail dialog at ${width}×${height} the handover menu sits 4px off its pill once the dialog has risen`, async ({ page }) => {
    const dialog = await detailAt(page, { width, height });
    const pill = dialog.locator('.ho-status-pill');
    await pill.click();
    await expect(menu(page)).toBeVisible();
    await settle(page);
    const at = await pill.boundingBox();
    const box = await menu(page).boundingBox();
    const gap = box.y >= at.y ? box.y - (at.y + at.height) : at.y - (box.y + box.height);
    expect(Math.abs(gap - 4), `gap ${gap}`).toBeLessThanOrEqual(1);
  });
}

for (const theme of ['dark', 'light']) {
  test(`${theme}: the relation suggestions and the handover menu are one surface`, async ({ page }) => {
    const dialog = await detailAt(page, { theme });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    const surface = (loc) => loc.evaluate((el) => {
      const cs = getComputedStyle(el);
      return { background: cs.backgroundColor, border: cs.borderTopColor };
    });
    await dialog.locator('.ho-status-pill').click();
    await expect(menu(page)).toBeVisible();
    const handover = await surface(menu(page));
    await page.keyboard.press('Escape');
    await expect(menu(page)).toHaveCount(0);
    await dialog.locator('[data-action="edit"]').click();
    const form = page.locator('.modal--form');
    await expect(form).toBeVisible();
    await form.locator('[data-key="depends_on"] input').first().fill('T-1');
    const list = page.getByRole('listbox', { name: 'Depends on suggestions' });
    await expect(list).toBeVisible();
    expect(await surface(list)).toEqual(handover);
    // Re-audit: in dark the list was the dialog's own grey. Light has nothing lighter than the dialog: the edge carries it.
    const dialogGround = await surface(form);
    if (theme === 'dark') expect(handover.background).not.toBe(dialogGround.background);
    expect(handover.border).not.toBe(handover.background);
    // X-01: each row says its task's status as the shared shape plus word.
    const first = list.getByRole('option').first();
    await expect(first.locator('.marker .marker__shape')).toBeVisible();
    await expect(first.locator('.marker .marker__word')).toHaveText('In progress');
    // The highlighted row is a step off the list.
    const active = await list.locator('.ef-chip-dd-active').evaluate((el) => getComputedStyle(el).backgroundColor);
    expect(active).not.toBe(handover.background);
    await page.keyboard.press('Escape');
  });
}
