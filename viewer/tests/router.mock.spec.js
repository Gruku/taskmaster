// User intent: whatever a navigation ends in — a screen, a failed load, a screen that crashes — the top bar never keeps the previous screen's controls.
import { test, expect } from '@playwright/test';
import { mockApi, unmockedWrites } from './mock-api.js';

const SLOTS = ['#topbar-actions', '#topbar-count', '#topbar-primary'];

// Kanban with its row-2 controls, plus stand-ins in the two row-1 slots no screen fills yet.
async function openScreenWithControls(page) {
  await page.goto('/#/kanban');
  await expect(page.locator('#topbar-actions [data-global-search]')).toBeVisible();
  await page.evaluate(() => {
    document.getElementById('topbar-count').textContent = '12 tasks';
    document.getElementById('topbar-primary').appendChild(document.createElement('button')).textContent = 'New';
  });
}
// Row 2 keeps only its Filters button, hidden, and with it the row.
async function expectClearedTopbar(page) {
  for (const sel of SLOTS) await expect(page.locator(`${sel} > :not(.overflow-more)`), sel).toHaveCount(0);
  for (const sel of ['#topbar-count', '#topbar-primary']) await expect(page.locator(sel), sel).toHaveText('');
  await expect(page.locator('#topbar-actions > .overflow-more')).toBeHidden();
  await expect(page.locator('#topbar-actions')).toBeHidden();
}
const screenModule = (body) => (route) => route.fulfill({ contentType: 'text/javascript', body });

test.beforeEach(async ({ page }) => { await mockApi(page); });
// A write the mock did not expect means the page talked to an endpoint this spec never set up.
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

test('a screen whose mount throws leaves a cleared top bar and a visible error', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.route('**/js/screens/settings.js', screenModule(`
    export const meta = { title: 'Settings', sidebarKey: 'settings' };
    export function mount() {
      // Controls added before the crash must not outlive it either.
      document.getElementById('topbar-actions').appendChild(document.createElement('button')).textContent = 'half-built';
      throw new Error('boom <b id="injected">markup</b>');
    }
  `));
  await openScreenWithControls(page);
  await page.evaluate(() => { location.hash = '#/settings'; });
  const stub = page.locator('#screen-mount .stub');
  await expect(stub).toContainText('Failed to open screen: /settings');
  await expect(stub.locator('.stub-meta')).toHaveText('boom <b id="injected">markup</b>');
  await expect(page.locator('#injected')).toHaveCount(0);
  await expectClearedTopbar(page);
  expect(errors).toEqual([]);
});

test('a screen whose mount rejects leaves a cleared top bar and a visible error', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.route('**/js/screens/settings.js', screenModule(`
    export const meta = { title: 'Settings', sidebarKey: 'settings' };
    export async function mount() {
      await Promise.resolve();
      document.getElementById('topbar-actions').appendChild(document.createElement('button')).textContent = 'half-built';
      throw new Error('late boom');
    }
  `));
  await openScreenWithControls(page);
  await page.evaluate(() => { location.hash = '#/settings'; });
  await expect(page.locator('#screen-mount .stub .stub-meta')).toHaveText('late boom');
  await expectClearedTopbar(page);
  expect(errors).toEqual([]);
});

test('a screen that fails to load leaves a cleared top bar and a visible error', async ({ page }) => {
  await page.route('**/js/screens/settings.js', (route) => route.abort());
  await openScreenWithControls(page);
  await page.evaluate(() => { location.hash = '#/settings'; });
  await expect(page.locator('#screen-mount .stub')).toContainText('Failed to load screen: /settings');
  await expectClearedTopbar(page);
});

test('the next screen mounts normally after a failed one', async ({ page }) => {
  await page.route('**/js/screens/settings.js', screenModule(`
    export const meta = { title: 'Settings' };
    export function mount() { throw new Error('boom'); }
  `));
  await openScreenWithControls(page);
  await page.evaluate(() => { location.hash = '#/settings'; });
  await expect(page.locator('#screen-mount .stub')).toBeVisible();
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('#topbar-actions [data-global-search]')).toBeVisible();
  await expect(page.locator('#screen-mount .stub')).toHaveCount(0);
  await expect(page.locator('#topbar-count')).toHaveText('');
  await expect(page.locator('#topbar-primary > *')).toHaveCount(0);
});

test('every working screen starts from an empty top bar', async ({ page }) => {
  // Stand-ins in row 1 survive only if the router (not each screen) forgets to clear.
  await openScreenWithControls(page);
  await page.evaluate(() => { location.hash = '#/settings'; });
  await expect(page.locator('#page-title')).toHaveText('Settings');
  await expectClearedTopbar(page);
});
