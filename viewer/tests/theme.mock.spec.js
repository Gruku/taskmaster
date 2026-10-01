// User intent: the theme choice survives reloads, never flashes the wrong theme, and still works when storage is blocked.
import { test, expect } from '@playwright/test';
import { mockApi } from './mock-api.js';

test('light pref from the server is applied', async ({ page }) => {
  await mockApi(page, { '/api/viewer/prefs': { theme: 'light', ui: {}, screens: {} } });
  await page.goto('/#/settings');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.locator('body')).toHaveCSS('background-color', 'rgb(230, 228, 221)');   // ground-5 light = #e6e4dd
});

test('system pref follows the OS scheme', async ({ page }) => {
  await mockApi(page);
  await page.emulateMedia({ colorScheme: 'light' });
  await page.goto('/#/settings');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
});

test('stored choice is applied before first paint', async ({ page }) => {
  await mockApi(page);
  await page.addInitScript(() => localStorage.setItem('tm.theme', 'light'));
  await page.route('**/css/tokens.css', async (r) => { await new Promise((ok) => setTimeout(ok, 300)); r.continue(); });
  await page.goto('/#/settings', { waitUntil: 'commit' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('blocked localStorage does not break boot', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.addInitScript(() => {
    Object.defineProperty(window, 'localStorage', { get() { throw new Error('denied'); } });
  });
  await mockApi(page);
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/#/settings');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(page.locator('#sidebar .sidebar-link').first()).toBeVisible();
  expect(errors).toEqual([]);
});

test('fonts load locally with no network font request', async ({ page }) => {
  const external = [];
  page.on('request', (r) => { if (!r.url().startsWith('http://127.0.0.1')) external.push(r.url()); });
  await mockApi(page);
  await page.goto('/#/settings');
  await page.evaluate(() => document.fonts.ready);
  expect(await page.evaluate(() => document.fonts.check('600 16px "DM Sans"'))).toBe(true);
  expect(external).toEqual([]);
});
