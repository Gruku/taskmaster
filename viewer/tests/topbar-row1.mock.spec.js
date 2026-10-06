// User intent: on a phone, topbar row 1 keeps the title, the count, the screen's primary action and the theme toggle on
// one line — the count gives way first and keeps its words in its title, a primary shows only its icon — and a new route
// leaves none of the last screen's count or primary behind.
import { test, expect } from '@playwright/test';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD } from './mock-fixtures.js';

const COUNT = '230 tasks · 230 visible';
const PROBE = 'Row one probe';

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
  await mockApi(page, {
    '/api/viewer/prefs': { theme: 'dark', ui: {}, screens: {} },
    '/api/board': BOARD, '/api/backlog': BOARD, '/api/bugs': [],
  });
});
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

// Settings claims row 1 and leaves it empty; the probe fills it the way a screen's mount() would.
async function bootProbe(page, width, height) {
  await page.setViewportSize({ width, height });
  await page.goto('/#/settings');
  await expect(page.locator('.set-detail-view')).toBeVisible();
  await expect(page.locator('#theme-toggle')).toBeEnabled();
  await page.evaluate(([count, probe]) => import('/js/lib/topbar.js').then(({ setTopbarCount, claimTopbarPrimary, tmAction }) => {
    setTopbarCount(count);
    claimTopbarPrimary().append(tmAction({ icon: 'plus', label: 'Task', variant: 'primary', title: probe }));
  }), [COUNT, PROBE]);
  await page.evaluate(() => document.fonts.ready);
}

test('at 390 row 1 keeps title, count, primary and theme toggle on one 56px line', async ({ page }) => {
  await bootProbe(page, 390, 844);
  const row = page.locator('.topbar-row1');
  const box = await row.boundingBox();
  expect(box.height).toBeCloseTo(56, 0);
  const widths = await row.evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth }));
  expect(widths.scroll).toBeLessThanOrEqual(widths.client);

  for (const sel of ['.topbar-hamburger', '#page-title', '#topbar-count', '#topbar-primary .btn', '#theme-toggle']) {
    const b = await page.locator(`.topbar-row1 ${sel}`).boundingBox();
    expect(b, sel).not.toBeNull();
    expect(b.y, `${sel} top`).toBeGreaterThanOrEqual(box.y - 0.5);
    expect(b.y + b.height, `${sel} bottom`).toBeLessThanOrEqual(box.y + box.height + 0.5);
    expect(b.x + b.width, `${sel} right`).toBeLessThanOrEqual(box.x + box.width + 0.5);
  }

  const primary = page.getByRole('button', { name: PROBE });
  await expect(primary).toBeVisible();
  const pb = await primary.boundingBox();
  expect(pb.width).toBeGreaterThanOrEqual(44);
  expect(pb.height).toBeGreaterThanOrEqual(44);
  await expect(primary.locator('span')).toBeHidden();

  const count = page.locator('#topbar-count');
  await expect(count).toHaveAttribute('title', COUNT);
  const cut = await count.evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth }));
  expect(cut.scroll).toBeGreaterThan(cut.client);
});

test('at 1440 the primary shows its label and the count is not cut', async ({ page }) => {
  await bootProbe(page, 1440, 900);
  const primary = page.getByRole('button', { name: PROBE });
  await expect(primary.locator('span')).toHaveText('Task');
  await expect(primary.locator('span')).toBeVisible();
  const count = page.locator('#topbar-count');
  await expect(count).toHaveText(COUNT);
  const cut = await count.evaluate((el) => ({ scroll: el.scrollWidth, client: el.clientWidth }));
  expect(cut.scroll).toBeLessThanOrEqual(cut.client);
});

test('a new route clears the count, its title and the primary', async ({ page }) => {
  await bootProbe(page, 390, 844);
  await expect(page.locator('#topbar-count')).toHaveAttribute('title', COUNT);
  await page.evaluate(() => { location.hash = '#/table'; });
  await expect(page.locator('table.tbl')).toBeVisible();
  await expect(page.locator('#topbar-count')).not.toHaveAttribute('title', COUNT);
  await expect(page.locator('#topbar-primary').getByRole('button', { name: PROBE })).toHaveCount(0);
  await expect(page.locator('#topbar-primary').getByRole('link', { name: PROBE })).toHaveCount(0);
});
