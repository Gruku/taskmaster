// User intent: Settings works in a real browser — a theme chosen there applies at once, survives a reload with no flash,
// and stays in step with the topbar toggle; card density and detail view are saved and shown again; every control is
// reachable by keyboard, tall enough to tap at phone width, and clean under axe in both themes.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { settingsMocks } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');
const LOADED = '.set-control[role="group"] .tm-segmented > button[data-key="system"]';

test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

// Every PUT /api/viewer/prefs body the page sent.
function watchPrefs(page) {
  const puts = [];
  page.on('request', (req) => {
    if (req.method() === 'PUT' && new URL(req.url()).pathname === '/api/viewer/prefs') puts.push(req.postDataJSON());
  });
  return puts;
}

const isPrefsPut = (req) => req.method() === 'PUT' && new URL(req.url()).pathname === '/api/viewer/prefs';
const group = (page, name) => page.getByRole('group', { name });
const segment = (page, groupName, label) => group(page, groupName).getByRole('button', { name: label, exact: true });

async function openSettings(page, mocks = settingsMocks()) {
  await mockApi(page, mocks);
  await page.goto('/#/settings');
  // 15s: the first test of a cold run waits on the dev server compiling the screen.
  await expect(page.locator(LOADED)).toBeVisible({ timeout: 15_000 });
}

test('the three blocks are in order, each a group named by its heading and described by its sentence', async ({ page }) => {
  await openSettings(page);
  const blocks = page.locator('section.set-block[aria-labelledby]');
  await expect(blocks).toHaveCount(3);
  await expect(blocks.locator('h2.set-h')).toHaveText(['Theme', 'Card density', 'Detail view']);
  const expected = [
    ['Theme', "Choose the theme. System follows your computer's setting.", ['Dark', 'Light', 'System']],
    ['Card density', 'How much each Kanban card shows.', ['Full', 'Minimal']],
    ['Detail view', 'How a task or epic opens when you click it.', ['Modal', 'Full page']],
  ];
  for (const [name, description, labels] of expected) {
    const g = group(page, name);
    await expect(g).toHaveAccessibleDescription(description);
    await expect(g.locator('.tm-segmented > button')).toHaveText(labels);
  }
  await expect(segment(page, 'Theme', 'Dark')).toHaveAttribute('aria-pressed', 'true');
  await expect(segment(page, 'Card density', 'Full')).toHaveAttribute('aria-pressed', 'true');
  await expect(segment(page, 'Detail view', 'Modal')).toHaveAttribute('aria-pressed', 'true');
  // The page gutter is #screen-mount's, and Settings puts nothing in the topbar.
  const pad = await page.locator('.settings').evaluate((el) => getComputedStyle(el).padding);
  expect(pad).toBe('0px');
  await expect(page.locator('#topbar-actions > *:visible')).toHaveCount(0);
  await expect(page.locator('#topbar-primary > *')).toHaveCount(0);
});

test('a theme chosen in Settings applies at once, persists, and never flashes', async ({ page }) => {
  await openSettings(page, settingsMocks({ theme: 'dark' }));
  const saved = page.waitForRequest((req) => isPrefsPut(req) && req.postDataJSON()?.theme === 'light');
  await segment(page, 'Theme', 'Light').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  expect(await page.evaluate(() => localStorage.getItem('tm.theme'))).toBe('light');
  await saved;

  // Reload with the prefs read held open: only the pre-paint script can be painting "light".
  let release;
  const gate = new Promise((ok) => { release = ok; });
  await page.route('**/api/viewer/prefs', async (route) => {
    if (route.request().method() !== 'GET') return route.fallback();
    await gate;
    return route.fulfill({ json: { theme: 'light', ui: {}, screens: {} } });
  });
  await page.reload({ waitUntil: 'commit' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(page.locator('#sidebar .sidebar-link')).toHaveCount(0);   // boot is still waiting on prefs
  release();
  // 15s: the first test of a cold run waits on the dev server compiling the screen.
  await expect(page.locator(LOADED)).toBeVisible({ timeout: 15_000 });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(segment(page, 'Theme', 'Light')).toHaveAttribute('aria-pressed', 'true');
});

test('the topbar toggle moves the Settings control with it', async ({ page }) => {
  await openSettings(page);
  await expect(segment(page, 'Theme', 'Dark')).toHaveAttribute('aria-pressed', 'true');
  await page.locator('#theme-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(segment(page, 'Theme', 'Light')).toHaveAttribute('aria-pressed', 'true');
  await expect(segment(page, 'Theme', 'Dark')).toHaveAttribute('aria-pressed', 'false');
});

test('leaving Settings removes its theme listener', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await openSettings(page);
  // Keep a handle on the Theme control, leave, then switch the theme from the topbar.
  await page.evaluate((sel) => { window.__themeSeg = document.querySelector(sel).parentElement; }, LOADED);
  await page.evaluate(() => { location.hash = '#/archived'; });
  await expect(page.locator('.set-block')).toHaveCount(0);
  await page.locator('#theme-toggle').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  expect(await page.evaluate(() => window.__themeSeg.querySelector('[aria-pressed="true"]').dataset.key)).toBe('dark');
  expect(errors).toEqual([]);
});

test('System follows the computer', async ({ page }) => {
  await openSettings(page);
  await page.emulateMedia({ colorScheme: 'light' });
  await segment(page, 'Theme', 'System').click();
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await page.emulateMedia({ colorScheme: 'dark' });
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'dark');
  await expect(segment(page, 'Theme', 'System')).toHaveAttribute('aria-pressed', 'true');
});

test('density and detail view are saved and shown again', async ({ page }) => {
  const puts = watchPrefs(page);
  await openSettings(page);
  const saved = page.waitForRequest((req) => isPrefsPut(req) && req.postDataJSON()?.ui?.detail_view_mode === 'full');
  await segment(page, 'Card density', 'Minimal').click();
  await segment(page, 'Detail view', 'Full page').click();
  await expect(segment(page, 'Card density', 'Minimal')).toHaveAttribute('aria-pressed', 'true');
  await expect(segment(page, 'Detail view', 'Full page')).toHaveAttribute('aria-pressed', 'true');
  await saved;
  expect(puts.some((b) => b.card_density === 'minimal')).toBe(true);
  expect(puts.some((b) => b.ui?.detail_view_mode === 'full')).toBe(true);

  await page.unrouteAll({ behavior: 'wait' });
  await mockApi(page, settingsMocks({ card_density: 'minimal', ui: { detail_view_mode: 'full' } }));
  await page.reload();
  // 15s: the first test of a cold run waits on the dev server compiling the screen.
  await expect(page.locator(LOADED)).toBeVisible({ timeout: 15_000 });
  await expect(segment(page, 'Card density', 'Minimal')).toHaveAttribute('aria-pressed', 'true');
  await expect(segment(page, 'Card density', 'Full')).toHaveAttribute('aria-pressed', 'false');
  await expect(segment(page, 'Detail view', 'Full page')).toHaveAttribute('aria-pressed', 'true');
  await expect(segment(page, 'Detail view', 'Modal')).toHaveAttribute('aria-pressed', 'false');
});

test('keyboard: Tab reaches the theme control and Enter applies it', async ({ page }) => {
  await openSettings(page);
  const light = segment(page, 'Theme', 'Light');
  let reached = false;
  for (let i = 0; i < 30 && !reached; i++) {
    await page.keyboard.press('Tab');
    reached = await light.evaluate((el) => el === document.activeElement);
  }
  expect(reached).toBe(true);
  await page.keyboard.press('Enter');
  await expect(page.locator('html')).toHaveAttribute('data-theme', 'light');
  await expect(light).toHaveAttribute('aria-pressed', 'true');
});

test('at 390px every segment is at least 44px tall and nothing scrolls sideways', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openSettings(page);
  const buttons = page.locator('.set-control .tm-segmented > button');
  await expect(buttons).toHaveCount(7);
  const heights = await buttons.evaluateAll((els) => els.map((el) => el.getBoundingClientRect().height));
  for (const h of heights) expect(h).toBeGreaterThanOrEqual(44);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): Settings shows no violations`, async ({ page }) => {
    await openSettings(page, settingsMocks({ theme }));
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await page.evaluate(axeSource);
    const result = await page.evaluate(() => window.axe.run(document.querySelector('#screen-mount'), { resultTypes: ['violations'] }));
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}

// Plan 4's accessibility gate reuses settingsMocks() once per theme; this pins that it loads real content, not a state block.
for (const theme of ['dark', 'light']) {
  test(`settings loads its content from settingsMocks() in ${theme}`, async ({ page }) => {
    await mockApi(page, settingsMocks({ theme }));
    await page.goto('/#/settings');
    await expect(page.locator('.set-control[role="group"] .tm-segmented > button[data-key="system"]')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.locator('.tm-empty[data-state="error"]')).toHaveCount(0);
  });
}
