// User intent: the theme choice survives reloads, never flashes the wrong theme, and still works when storage is blocked.
import { test, expect } from '@playwright/test';
import { mockApi, unmockedWrites } from './mock-api.js';

// A write the mock did not expect means the page talked to an endpoint this spec never set up.
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

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
  // Dark OS + prefs held open: only the inline script in index.html can have produced "light".
  let release;
  const gate = new Promise((ok) => { release = ok; });
  let prefsRequested = false;
  await mockApi(page);
  await page.route('**/api/viewer/prefs', async (route) => {
    prefsRequested = true;
    await gate;
    await route.fulfill({ json: { theme: 'light', ui: {}, screens: {} } });
  });
  await page.addInitScript(() => localStorage.setItem('tm.theme', 'light'));
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/#/settings', { waitUntil: 'commit' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect.poll(() => prefsRequested).toBe(true);
  await expect(page.locator('#sidebar .sidebar-link')).toHaveCount(0);   // boot is still waiting on prefs
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  release();
  await expect(page.locator('#sidebar .sidebar-link').first()).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
});

test('a failed prefs fetch keeps the cached choice', async ({ page }) => {
  await mockApi(page, { '/api/viewer/prefs': { status: 500, json: {} } });
  await page.addInitScript(() => {
    if (localStorage.getItem('tm.theme') === null) localStorage.setItem('tm.theme', 'light');
  });
  await page.emulateMedia({ colorScheme: 'dark' });
  await page.goto('/#/settings');
  await expect(page.locator('#sidebar .sidebar-link').first()).toBeVisible();   // boot finished
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  expect(await page.evaluate(() => localStorage.getItem('tm.theme'))).toBe('light');
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

test('a throwing matchMedia does not break boot', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.addInitScript(() => { window.matchMedia = () => { throw new Error('matchMedia denied'); }; });
  await mockApi(page);
  await page.goto('/#/settings');
  await expect(page.locator('#sidebar .sidebar-link').first()).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');   // same fallback as the inline script
  expect(errors).toEqual([]);
});

test('fonts load locally with no network font request', async ({ page }) => {
  const external = [];
  page.on('request', (r) => { if (!r.url().startsWith('http://127.0.0.1')) external.push(r.url()); });
  await mockApi(page);
  await page.goto('/#/settings');
  await expect(page.locator('#sidebar .sidebar-link').first()).toBeVisible();   // body-font text is on the page
  await page.evaluate(() => document.fonts.ready);
  expect(await page.evaluate(() => document.fonts.check('600 16px "DM Sans"'))).toBe(true);
  expect(external).toEqual([]);
});
