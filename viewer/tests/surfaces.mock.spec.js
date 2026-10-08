// User intent: with shadows banned, a light-theme card must be the lightest surface on the board and stay
// readable when hovered — never a grey slab, never a dark hover left over from the old palette.
import { test, expect } from '@playwright/test';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD } from './mock-fixtures.js';

test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

async function openBoard(page, theme) {
  await mockApi(page, { '/api/viewer/prefs': { theme, ui: {}, screens: {} }, '/api/board': BOARD, '/api/backlog': BOARD });
  await page.goto('/#/kanban');
  await expect(page.locator('.card-task').first()).toBeVisible();
}

// Hover is halfway from ground-0 to ground-5: ground-5 itself is the page, so a hovered card merged into it.
test('light theme: card is ground-0 on a ground-10 column, and hover steps it halfway to the page ground', async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });   // the hover fill is read at once, not mid-transition
  await openBoard(page, 'light');
  const card = page.locator('.card-task').first();
  await expect(card).toHaveCSS('background-color', 'rgb(245, 243, 237)');
  await expect(page.locator('.kanban-col').first()).toHaveCSS('background-color', 'rgb(210, 207, 200)');
  await card.hover();
  const hover = await card.evaluate((el) => getComputedStyle(el).backgroundColor);
  expect(hover).not.toBe('rgb(245, 243, 237)');
  expect(hover).not.toBe(await page.locator('body').evaluate((el) => getComputedStyle(el).backgroundColor));
  expect(hover).not.toBe('rgb(210, 207, 200)');
  await expect(card.locator('.card-title')).toHaveCSS('color', 'rgb(13, 13, 12)');
});

test('dark theme: card and hover keep the RR raised and overlay grounds', async ({ page }) => {
  await openBoard(page, 'dark');
  const card = page.locator('.card-task').first();
  await expect(card).toHaveCSS('background-color', 'rgb(29, 29, 27)');
  await card.hover();
  await expect(card).toHaveCSS('background-color', 'rgb(39, 39, 37)');
});
