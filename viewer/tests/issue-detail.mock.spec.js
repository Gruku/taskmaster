// User intent: the issue page reads as the shared detail template — markers, a stale tag, rendered sections, links in
// the rail — and says a missing or failed load in words, by keyboard, at 390px and in both themes (plan 3c Task 7).
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD, ISSUE, ISSUES, LONG_ISSUE } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');
let errors;

test.beforeEach(async ({ page }) => {
  errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.emulateMedia({ reducedMotion: 'reduce' });
});
test.afterEach(async ({ page }) => {
  expect(unmockedWrites(page)).toEqual([]);
  expect(errors).toEqual([]);
});

async function open(page, hash, { theme = 'dark', issues = ISSUES, before } = {}) {
  await mockApi(page, {
    '/api/viewer/prefs': { theme, ui: {}, screens: {}, issues: { aging: { High: 30 } } },
    '/api/board': BOARD, '/api/backlog': BOARD, '/api/bugs': [], '/api/issues': issues,
  });
  if (before) await before();
  await page.goto(`/${hash}`);
}

const mount = (page) => page.locator('#screen-mount');

test('the issue reads as the detail template', async ({ page }) => {
  await open(page, '#/issue/ISS-012');
  const m = mount(page);
  await expect(m.locator('h1')).toHaveCount(1);
  await expect(m.locator('h1')).toHaveText(ISSUE.title);
  const meta = m.locator('[data-test="meta"]');
  await expect(meta.locator('a[href="#/issues"]')).toHaveText('Issues');
  await expect(meta).toContainText('discovered');
  await expect(m.locator('[data-field="severity"] .marker__word')).toHaveText('High');
  await expect(m.locator('[data-field="status"] .marker__word')).toHaveText('Investigating');
  await expect(m.locator('[data-tag="stale"]')).toBeVisible();
  await expect(m.locator('[data-tag="stale"]')).toHaveText(/stale\s*\d+d/i);
  expect(await m.locator('.td-body [data-section]').evaluateAll((els) => els.map((e) => e.dataset.section)))
    .toEqual(['evidence', 'repro', 'impact', 'notes', 'location']);
  await expect(m.locator('[data-section="evidence"] strong')).toHaveCount(1);
  await expect(m.locator('[data-section="location"] code')).toHaveText(ISSUE.location);
  const rail = page.getByRole('complementary', { name: 'Related' });
  await expect(rail.locator('a.link-pill[href="#/task/T-102"]')).toBeVisible();
  await expect(rail.locator('a.link-pill[href="#/issue/ISS-009"]')).toBeVisible();
  await expect(m.locator('.id-crumb')).toHaveCount(0);
  await expect(m).not.toContainText('‹');
});

test('a fixed issue shows no stale tag and its resolved date', async ({ page }) => {
  await open(page, '#/issue/ISS-009');
  await expect(mount(page).locator('h1')).toHaveText('Light card edge');
  await expect(mount(page).locator('[data-tag="stale"]')).toHaveCount(0);
  await expect(mount(page).locator('[data-test="dates"]')).toContainText('Resolved');
});

test('ISS-999 is not found in words, and a failed load says so without the server\'s text', async ({ page, context }) => {
  const puts = [];
  page.on('request', (r) => { if (r.method() === 'PUT' && r.url().includes('/api/viewer/prefs')) puts.push(r.postData() || ''); });
  await open(page, '#/issue/ISS-999');
  const missing = mount(page).locator('.tm-empty[data-state="missing"]');
  await expect(missing).toBeVisible();
  await expect(missing.locator('.tm-empty__label')).toHaveText('ISS-999');
  await expect(missing.locator('.tm-empty__headline')).toHaveText('Issue not found');
  await expect(missing.getByRole('link', { name: 'Open Issues' })).toBeVisible();
  // Writes are debounced (400 ms) and merged per window: wait it out here, so a write for the missing id cannot be
  // overwritten by the next issue's patch.
  await page.waitForTimeout(700);
  expect(puts).toEqual([]);
  await page.evaluate(() => { location.hash = '#/issue/ISS-012'; });
  await expect(mount(page).locator('h1')).toHaveText(ISSUE.title);
  await expect.poll(() => puts.some((b) => b.includes('ISS-012')), { timeout: 5000 }).toBe(true);
  expect(puts.filter((b) => b.includes('ISS-999'))).toEqual([]);

  const fresh = await context.newPage();
  fresh.on('pageerror', (e) => errors.push(e.message));
  await open(fresh, '#/issue/ISS-012', { issues: ISSUES });
  // Registered after mockApi, so it wins until unrouted.
  let hold = null; // set before the last Try again, so its Loading state can be seen
  const fail = async (route) => {
    if (hold) { await hold; await route.fallback(); return; }
    await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ error: 'Traceback: KeyError severity' }) });
  };
  await fresh.route('**/api/issues*', fail);
  await fresh.goto('about:blank');
  await fresh.goto('/#/issue/ISS-012');
  const failed = mount(fresh).locator('.tm-empty[data-state="error"]');
  await expect(failed).toBeVisible();
  for (const word of ['Traceback', '500', '/api']) await expect(mount(fresh)).not.toContainText(word);
  // A second failure after Try again lands focus on the new Try again, never <body>.
  await failed.getByRole('button', { name: 'Try again' }).click();
  await expect(mount(fresh).locator('.tm-empty[data-state="error"] button')).toBeFocused();
  let release;
  hold = new Promise((r) => { release = r; });
  await failed.getByRole('button', { name: 'Try again' }).click();
  const busy = mount(fresh).locator('.tm-empty[aria-busy="true"]');
  await expect(busy).toBeVisible();
  await expect(busy).toBeFocused();
  release();
  await expect(mount(fresh).locator('h1')).toHaveText(ISSUE.title);
  await expect(mount(fresh).locator('h1')).toBeFocused();
  expect(unmockedWrites(fresh)).toEqual([]);
});

test('the first fetch shows a Loading state, not a blank page', async ({ page }) => {
  let release;
  const gate = new Promise((r) => { release = r; });
  // Registered after mockApi (via before), so it wins.
  await open(page, '#/issue/ISS-012', { before: () => page.route('**/api/issues*', async (route) => { await gate; await route.fallback(); }) });
  await expect(mount(page).locator('.tm-empty[aria-busy="true"]')).toBeVisible();
  release();
  await expect(mount(page).locator('h1')).toHaveText(ISSUE.title);
});

test('an issue made after the list was cached is still found', async ({ page }) => {
  await open(page, '#/issue/ISS-009', { issues: { issues: [ISSUES.issues[1]] } });
  await expect(mount(page).locator('h1')).toHaveText('Light card edge');
  let gets = 0;
  page.on('request', (r) => { if (r.method() === 'GET' && new URL(r.url()).pathname === '/api/issues') gets++; });
  await page.route('**/api/issues*', (route) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(ISSUES) }));
  await page.evaluate(() => { location.hash = '#/issue/ISS-012'; });
  await expect(mount(page).locator('h1')).toHaveText(ISSUE.title);
  expect(gets).toBe(1);
});

test('walks by keyboard', async ({ page }) => {
  await open(page, '#/issue/ISS-012');
  await expect(mount(page).locator('h1')).toBeVisible();
  const stops = await page.evaluate(() => {
    const root = document.querySelector('#screen-mount');
    return [...root.querySelectorAll('a[href], button, input, select, textarea, [tabindex]')]
      .filter((el) => el.tabIndex >= 0 && el.getClientRects().length && !el.disabled)
      .map((el) => el.getAttribute('data-test') || el.getAttribute('href'));
  });
  expect(stops).toEqual(['issue-id', '#/issues', '#/task/T-102', '#/issue/ISS-009']);
  // And Tab really visits them in that order.
  await page.locator('[data-test="issue-id"]').focus();
  for (const sel of ['[data-test="meta"] a[href="#/issues"]', 'a.link-pill[href="#/task/T-102"]', 'a.link-pill[href="#/issue/ISS-009"]']) {
    await page.keyboard.press('Tab');
    await expect(mount(page).locator(sel)).toBeFocused();
  }
});

test('a long title and a long path stay inside 390px', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await open(page, '#/issue/ISS-1234', { issues: { issues: [LONG_ISSUE] } });
  await expect(mount(page).locator('h1')).toBeVisible();
  const box = await page.evaluate(() => {
    const h1 = document.querySelector('#screen-mount h1').getBoundingClientRect();
    const id = document.querySelector('#screen-mount .td-id-text');
    return { sw: document.documentElement.scrollWidth, iw: innerWidth, right: h1.right, rects: id.getClientRects().length, text: id.textContent };
  });
  expect(box.sw).toBeLessThanOrEqual(box.iw);
  expect(box.right).toBeLessThanOrEqual(box.iw);
  expect(box.text).toBe('ISS-1234');
  expect(box.rects).toBe(1);
});

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): the issue page has no violations`, async ({ page }) => {
    await open(page, '#/issue/ISS-012', { theme });
    await expect(mount(page).locator('h1')).toBeVisible();
    await page.addScriptTag({ content: axeSource });
    const result = await page.evaluate(() => window.axe.run('#screen-mount', { runOnly: { type: 'tag', values: ['wcag2a', 'wcag2aa'] } }));
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target).join(' | ')}`)).toEqual([]);
  });
}
