// User intent: the bug page reads as the shared detail template — summary and location shown, status and severity as
// markers, relations in the rail — and says a missing or failed load in words, by keyboard, at 390px and in both themes
// (plan 3c Task 8).
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD, BUG, BUG_FIXED, LONG_BUG } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');
let errors;
let dialogs;

test.beforeEach(async ({ page }) => {
  errors = [];
  dialogs = [];
  page.on('pageerror', (e) => errors.push(e.message));
  page.on('dialog', (d) => { dialogs.push(d.message()); void d.dismiss(); });
  await page.emulateMedia({ reducedMotion: 'reduce' });
});
test.afterEach(async ({ page }) => {
  expect(unmockedWrites(page)).toEqual([]);
  expect(errors).toEqual([]);
  expect(dialogs).toEqual([]);
});

async function open(page, hash, { theme = 'dark', before } = {}) {
  await mockApi(page, {
    '/api/viewer/prefs': { theme, ui: {}, screens: {} },
    '/api/board': BOARD, '/api/backlog': BOARD, '/api/bugs': [], '/api/issues': { issues: [] },
    '/api/bugs/B-031': BUG, '/api/bugs/B-030': BUG_FIXED, '/api/bugs/B-1234': LONG_BUG,
    '/api/bugs/B-999': { status: 404, json: { ok: false, error: 'unknown bug B-999' } },
  });
  if (before) await before();
  await page.goto(`/${hash}`);
}

const mount = (page) => page.locator('#screen-mount');

test('the bug reads as the detail template, with its summary and location', async ({ page }) => {
  await open(page, '#/bug/B-031');
  const m = mount(page);
  await expect(m.locator('h1')).toHaveText(BUG.title);
  await expect(m).toHaveClass(/dp-page--bug/);
  await expect(m.locator('[data-field="status"] .marker__word')).toHaveText('Open');
  await expect(m.locator('[data-field="severity"] .marker__word')).toHaveText('Medium');
  await expect(m.locator('[data-section="summary"] code')).toHaveCount(1);
  await expect(m.locator('[data-section="summary"] ol')).toHaveCount(1);
  await expect(m.locator('[data-section="location"] code')).toHaveText('viewer/css/screens/kanban.css:87');
  const meta = m.locator('[data-test="meta"]');
  await expect(meta.locator('a[href="#/bugs"]')).toHaveText('Bugs');
  await expect(meta.locator('a[href="#/task/T-102"]')).toHaveText('T-102');
  await expect(meta).toContainText('reported by user');
  const found = m.locator('[data-sub="found-in"] a.td-dep');
  await expect(found.locator('.td-dep__id')).toHaveText('T-102');
  await expect(found.locator('.td-dep__title')).toHaveText('Re-skin the Kanban cards and columns');
  await expect(m.locator('.id-crumb, [class*="bug-detail"]')).toHaveCount(0);
  await expect(m).not.toContainText('‹');
});

test('a fixed bug shows its commit, where it went, and no severity it never had', async ({ page }) => {
  await open(page, '#/bug/B-030');
  const m = mount(page);
  await expect(m.locator('[data-field="status"] .marker__word')).toHaveText('Fixed');
  await expect(m.locator('[data-field="severity"]')).toHaveCount(0);
  await expect(m.locator('[data-tag="fix_commit"]')).toContainText('abfb1b9c0ffee');
  await expect(m.locator('[data-tag="fix_commit"]')).toHaveAttribute('aria-label', 'Copy fix commit abfb1b9c0ffee');
  for (const g of ['found-in', 'adopted-into', 'promoted-to']) await expect(m.locator(`[data-sub="${g}"]`)).toHaveCount(1);
  await expect(m.locator('[data-sub="adopted-into"] a.td-dep[href="#/task/T-101"]')).toBeVisible();
  const pill = m.locator('[data-sub="promoted-to"] a.link-pill[href="#/issue/ISS-012"]');
  await expect(pill.locator('.link-pill__label')).toHaveText('Issue');
  await expect(pill.locator('.link-pill__id')).toHaveText('ISS-012');
});

test('B-999 is not found in words; a 500 says so without the server\'s text; no action is offered without a record', async ({ page }) => {
  await open(page, '#/bug/B-999');
  const m = mount(page);
  const missing = m.locator('.tm-empty[data-state="missing"]');
  await expect(missing).toBeVisible();
  await expect(missing.locator('.tm-empty__label')).toHaveText('B-999');
  await expect(missing.locator('.tm-empty__headline')).toHaveText('Bug not found');
  await expect(missing.getByRole('link', { name: 'Open Bugs' })).toBeVisible();
  await expect(page.locator('#topbar-primary')).toBeEmpty();
  await expect(m.locator('button')).toHaveCount(0);

  let fails = 2;
  let hold = null; // set before the last Try again, so its Loading state can be seen
  await page.route('**/api/bugs/B-031', async (route) => {
    if (fails-- > 0) {
      await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'Traceback: KeyError found_in' }) });
      return;
    }
    if (hold) await hold;
    await route.fallback();
  });
  await page.evaluate(() => { location.hash = '#/bug/B-031'; });
  const failed = m.locator('.tm-empty[data-state="error"]');
  await expect(failed).toBeVisible();
  for (const word of ['Traceback', '500', '/api']) await expect(m).not.toContainText(word);
  // A second failure after Try again lands focus on the new Try again, never <body>.
  await failed.getByRole('button', { name: 'Try again' }).click();
  await expect(m.locator('.tm-empty[data-state="error"] button')).toBeFocused();
  let release;
  hold = new Promise((r) => { release = r; });
  await m.getByRole('button', { name: 'Try again' }).click();
  const busy = m.locator('.tm-empty[aria-busy="true"]');
  await expect(busy).toBeVisible();
  await expect(busy).toBeFocused();
  release();
  await expect(m.locator('h1')).toHaveText(BUG.title);
  await expect(m.locator('h1')).toBeFocused();
});

test('a bug opened after a missing one starts clean', async ({ page }) => {
  await open(page, '#/bug/B-999');
  await expect(mount(page).locator('.tm-empty')).toBeVisible();
  await page.evaluate(() => { location.hash = '#/bug/B-031'; });
  await expect(mount(page).locator('h1')).toHaveText(BUG.title);
  await expect(mount(page).locator('.td-doc')).toHaveCount(0); // the mount itself is the one .td-doc
  await expect(mount(page)).toHaveClass(/td-doc/);
  await expect(mount(page).locator('.tm-empty')).toHaveCount(0);
});

test('the first fetch shows a Loading state, not a blank page', async ({ page }) => {
  let release;
  const gate = new Promise((r) => { release = r; });
  // Registered after mockApi (via before), so it wins.
  await open(page, '#/bug/B-031', { before: () => page.route('**/api/bugs/B-031', async (route) => { await gate; await route.fallback(); }) });
  await expect(mount(page).locator('.tm-empty[aria-busy="true"]')).toBeVisible();
  release();
  await expect(mount(page).locator('h1')).toHaveText(BUG.title);
});

async function tabStops(page) {
  return page.evaluate(() => [...document.querySelector('#screen-mount').querySelectorAll('a[href], button, [tabindex]')]
    .filter((el) => el.tabIndex >= 0 && el.getClientRects().length && !el.disabled)
    .map((el) => el.getAttribute('data-test') || el.getAttribute('data-tag') || el.getAttribute('href') || el.getAttribute('data-action')));
}

test('walks by keyboard', async ({ page }) => {
  await open(page, '#/bug/B-031');
  await expect(mount(page).locator('h1')).toBeVisible();
  expect(await tabStops(page)).toEqual(['bug-id', '#/bugs', '#/task/T-102', 'shelve', 'adopt', 'promote', '#/task/T-102']);
  await page.evaluate(() => { location.hash = '#/bug/B-030'; });
  await expect(mount(page).locator('[data-tag="fix_commit"]')).toBeVisible();
  expect(await tabStops(page)).toEqual(['bug-id', '#/bugs', '#/task/T-102', 'fix_commit', '#/task/T-102', '#/task/T-101', '#/issue/ISS-012']);
  await page.locator('[data-test="bug-id"]').focus();
  for (const sel of ['[data-test="meta"] a[href="#/bugs"]', '[data-test="meta"] a[href="#/task/T-102"]', '[data-tag="fix_commit"]',
    '[data-sub="found-in"] a', '[data-sub="adopted-into"] a', '[data-sub="promoted-to"] a']) {
    await page.keyboard.press('Tab');
    await expect(mount(page).locator(sel)).toBeFocused();
  }
});

test('a long title and a long path stay inside 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page, '#/bug/B-1234');
  await expect(mount(page).locator('h1')).toBeVisible();
  const box = await page.evaluate(() => {
    const id = document.querySelector('#screen-mount .td-id-text');
    return { sw: document.documentElement.scrollWidth, iw: innerWidth, rects: id.getClientRects().length, text: id.textContent };
  });
  expect(box.sw).toBeLessThanOrEqual(box.iw);
  expect(box.text).toBe('B-1234');
  expect(box.rects).toBe(1);
});

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): the bug page has no violations`, async ({ page }) => {
    for (const id of ['B-031', 'B-030']) {
      await open(page, `#/bug/${id}`, { theme });
      await expect(mount(page).locator('h1')).toBeVisible();
      await page.addScriptTag({ content: axeSource });
      const result = await page.evaluate(() => window.axe.run('#screen-mount', { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] } }));
      expect(result.violations.map((v) => `${id} ${v.id}: ${v.nodes.map((n) => n.target).join(' | ')}`)).toEqual([]);
    }
  });
}
