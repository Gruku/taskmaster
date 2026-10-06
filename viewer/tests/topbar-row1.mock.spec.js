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
  await expect(page.locator('.set-control[role="group"] .tm-segmented > button[data-key="system"]')).toBeVisible();
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

// The page scrolls at phone width, so the topbar must stick to the viewport, not to .main (which never scrolls).
for (const [hash, loaded] of [['#/kanban', '.card-task'], ['#/table', 'table.tbl .tbl-row']]) {
  test(`at 390 the topbar stays at the top of a scrolled ${hash} and nothing scrolls sideways`, async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/' + hash);
    await expect(page.locator(loaded).first()).toBeVisible();
    await page.evaluate(() => window.scrollTo(0, 3000));
    expect(await page.evaluate(() => window.scrollY), 'the page is long enough to scroll').toBeGreaterThan(0);
    const top = await page.locator('.topbar').evaluate((el) => el.getBoundingClientRect().top);
    expect(top).toBe(0);
    const widths = await page.evaluate(() => ({ scroll: document.documentElement.scrollWidth, inner: window.innerWidth }));
    expect(widths.scroll).toBeLessThanOrEqual(widths.inner);
  });
}
