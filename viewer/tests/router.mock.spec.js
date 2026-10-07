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
const ERROR_BLOCK = '#screen-mount .tm-empty[data-state="error"]';
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
  await expect(page.locator(ERROR_BLOCK)).toContainText('This screen could not be opened.');
  await expect(page.locator('#screen-mount')).not.toContainText('boom');
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
  await expect(page.locator(ERROR_BLOCK)).toContainText('This screen could not be opened.');
  await expect(page.locator('#screen-mount')).not.toContainText('late boom');
  await expectClearedTopbar(page);
  expect(errors).toEqual([]);
});

test('a screen that fails to load leaves a cleared top bar and a visible error', async ({ page }) => {
  await page.route('**/js/screens/settings.js', (route) => route.abort());
  await openScreenWithControls(page);
  await page.evaluate(() => { location.hash = '#/settings'; });
  await expect(page.locator(ERROR_BLOCK)).toContainText('This screen could not be opened.');
  await expectClearedTopbar(page);
});

test('the next screen mounts normally after a failed one', async ({ page }) => {
  await page.route('**/js/screens/settings.js', screenModule(`
    export const meta = { title: 'Settings' };
    export function mount() { throw new Error('boom'); }
  `));
  await openScreenWithControls(page);
  await page.evaluate(() => { location.hash = '#/settings'; });
  await expect(page.locator(ERROR_BLOCK)).toBeVisible();
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('#topbar-actions [data-global-search]')).toBeVisible();
  await expect(page.locator(ERROR_BLOCK)).toHaveCount(0);
  await expect(page.locator('#topbar-count')).toHaveText('0 tasks');
  await expect(page.locator('#topbar-primary > *')).toHaveCount(1);
  await expect(page.locator('#topbar-primary').getByRole('button', { name: /Add task/ })).toHaveCount(1);
});

test('every working screen starts from an empty top bar', async ({ page }) => {
  // Stand-ins in row 1 survive only if the router (not each screen) forgets to clear.
  await openScreenWithControls(page);
  await page.evaluate(() => { location.hash = '#/settings'; });
  await expect(page.locator('#page-title')).toHaveText('Settings');
  await expectClearedTopbar(page);
});

test('a screen that fails to load says so in words', async ({ page }) => {
  await page.route('**/js/screens/epics.js', (route) => route.fulfill({ status: 500, body: '' }));
  await page.goto('/#/kanban');
  await expect(page.locator('#topbar-actions [data-global-search]')).toBeVisible();
  await page.evaluate(() => { location.hash = '#/epics'; });
  const block = page.locator(ERROR_BLOCK);
  await expect(block).toBeVisible();
  await expect(block.locator('.tm-empty__headline')).toHaveText('This screen could not be opened.');
  await expect(block.locator('.tm-empty__label')).toHaveText('Could not open');
  await expect(block.locator('.tm-empty__hint')).toHaveText('Reload the page. If it keeps happening, restart the viewer.');
  await expect(block.getByRole('link', { name: 'Go to the dashboard' })).toHaveAttribute('href', '#/dashboard');
  const text = await page.locator('#screen-mount').innerText();
  for (const word of ['Failed', 'fetch', 'import', 'http', '.js']) expect(text, word).not.toContain(word);
  await expect(page.locator('#page-title')).toHaveText('Could not open');
  await expect(page.locator('.sidebar-link[aria-current]')).toHaveCount(0);
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('#page-title')).toHaveText('Kanban');
  await expect(page.locator('#topbar-actions [data-global-search]')).toBeVisible();
  await expect(page.locator('.sidebar-link[data-key="kanban"]')).toHaveAttribute('aria-current', 'page');
  await expect(block).toHaveCount(0);
});

test('a screen that throws while mounting keeps its title and sidebar item', async ({ page }) => {
  await page.route('**/js/screens/settings.js', screenModule(
    "export const meta = { title: 'Settings', sidebarKey: 'settings' }; export async function mount() { throw new Error('boom'); }"));
  await page.goto('/#/kanban');
  await expect(page.locator('#topbar-actions [data-global-search]')).toBeVisible();
  await page.evaluate(() => { location.hash = '#/settings'; });
  await expect(page.locator(ERROR_BLOCK)).toContainText('This screen could not be opened.');
  await expect(page.locator('#page-title')).toHaveText('Settings');
  await expect(page.locator('.sidebar-link[data-key="settings"]')).toHaveAttribute('aria-current', 'page');
  await expect(page.locator('.sidebar-link[aria-current]')).toHaveCount(1);
  expect(await page.locator('body').innerText()).not.toContain('boom');
});
