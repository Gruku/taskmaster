// User intent: when someone else changed a task while the user was editing it, the conflict banner must be readable in
// both themes, name each field the way the form does, be worked from the keyboard, and never hide the dialog it holds.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD, RICH_TASK, taskDetail } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');
const BOARD_ACTIVE = { ...BOARD, context: { active_epic: 'viewer' } };
const DESKTOP = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
});
test.afterEach(async ({ page }) => {
  expect(unmockedWrites(page)).toEqual([]);
});

const THEIRS = { ...RICH_TASK, title: 'Their title', priority: 'high', last_referenced: '2026-10-01T08:00:00Z' };
// A lost race: the 409 names the revision it lost to.
const STALE = { status: 409, json: { ok: false, error: 'stale', current: THEIRS, current_etag: 't1:fresh' } };

async function openEdit(page, { theme = 'dark', viewport = DESKTOP } = {}) {
  await page.setViewportSize(viewport);
  await mockApi(page, {
    '/api/viewer/prefs': { theme, ui: {}, screens: {} },
    '/api/board': BOARD_ACTIVE, '/api/backlog': BOARD_ACTIVE,
    [`/api/task/${RICH_TASK.id}/detail`]: taskDetail(RICH_TASK),
  });
  await page.goto(`/#/task/${RICH_TASK.id}`);
  await page.getByTitle('Edit task').click();
  const dialog = page.getByRole('dialog', { name: 'Edit task' });
  await expect(dialog).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  return dialog;
}

const ctl = (dialog, key) => dialog.locator(`[data-key="${key}"]`).locator('input, select, textarea').first();

// Edit the given fields, save, and answer the PATCH with a lost race.
async function conflict(page, edits = { title: 'My title' }, opts) {
  const dialog = await openEdit(page, opts);
  await page.route('**/api/tasks/T-102', (route) => (route.request().method() === 'PATCH' ? route.fulfill(STALE) : route.fallback()));
  for (const [key, value] of Object.entries(edits)) await ctl(dialog, key).fill(value);
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  const banner = page.locator('#conflict-banner-host .cb-banner');
  await expect(banner).toBeVisible();
  return { dialog, banner };
}

// The banner's bottom edge is at or above the dialog header's top edge.
async function expectHeaderClear(dialog, banner) {
  await expect.poll(async () => {
    const b = await banner.boundingBox();
    const h = await dialog.locator('.modal-header').boundingBox();
    return b.y + b.height - h.y;
  }).toBeLessThanOrEqual(1);
}

async function leave(page) {
  await page.locator('#conflict-banner-host').getByRole('button', { name: 'Dismiss' }).click();
  await expect(page.locator('#conflict-banner-host .cb-banner')).toHaveCount(0);
}

for (const [name, viewport] of [['1440×900', DESKTOP], ['390×844', PHONE]]) {
  test(`${name}: the banner pushes the dialog down instead of covering its header`, async ({ page }) => {
    const { dialog, banner } = await conflict(page, undefined, { viewport });
    await expectHeaderClear(dialog, banner);
    await expect(dialog.locator('.modal-header')).toBeInViewport();
    await leave(page);
    // With the banner gone the dialog takes its place again.
    expect(await page.evaluate(() => document.documentElement.style.getPropertyValue('--conflict-banner-height'))).toBe('');
  });
}

test('each choice is a radiogroup named by its field label, and Tab goes from the dialog to the radios and back', async ({ page }) => {
  const { dialog, banner } = await conflict(page);
  const group = page.getByRole('radiogroup', { name: 'Title' });
  await expect(group).toBeVisible();
  await expect(group.getByRole('radio', { name: 'Keep mine' })).toBeChecked();
  await expect(group.getByRole('radio', { name: 'Use server' })).not.toBeChecked();
  await expect(banner.locator('.cb-headline')).toContainText('Task T-102 was changed by someone else');

  await dialog.locator('.modal-close').focus();
  const where = async () => page.evaluate(() => {
    const a = document.activeElement;
    if (a.closest('#conflict-banner-host')) return a.type === 'radio' ? 'radio' : 'banner';
    return a.closest('.modal--form') ? 'dialog' : 'elsewhere';
  });
  const seen = [];
  for (let i = 0; i < 20; i++) {
    await page.keyboard.press('Tab');
    seen.push(await where());
  }
  expect(seen).not.toContain('elsewhere');
  const radio = seen.indexOf('radio');
  expect(radio, `Tab reaches a radio: ${seen.join(' ')}`).toBeGreaterThanOrEqual(0);
  expect(seen.slice(radio).includes('dialog'), `and comes back to the dialog: ${seen.join(' ')}`).toBe(true);
  await leave(page);
});

test('390×844 with 8 changed fields: the banner is at most half the screen, scrolls, and the dialog header stays visible', async ({ page }) => {
  const long = (s) => `${s} — ${'a long value that wraps over several lines on a phone '.repeat(3)}`;
  const { dialog, banner } = await conflict(page, {
    title: 'My title', sub_repo: 'mine', release: '9.9.9', branch: long('feat/mine'), worktree: long('.worktrees/mine'),
    description: long('My description'), notes: long('My notes'), plan: long('My plan'),
  }, { viewport: PHONE });
  await expect(banner.locator('.cb-multi-row')).toHaveCount(8);
  const box = await banner.boundingBox();
  expect(box.height).toBeLessThanOrEqual(PHONE.height / 2 + 1);
  expect(await banner.evaluate((b) => b.scrollHeight > b.clientHeight), 'the rows overflow and scroll').toBe(true);
  await expectHeaderClear(dialog, banner);
  await expect(dialog.locator('.modal-header')).toBeInViewport();
  // Its buttons are reachable by scrolling the banner itself, and are touch-sized.
  const apply = banner.getByRole('button', { name: 'Apply choices' });
  await apply.scrollIntoViewIfNeeded();
  expect((await apply.boundingBox()).height).toBeGreaterThanOrEqual(44);
  await leave(page);
});

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): the banner has no violation`, async ({ page }) => {
    await conflict(page, { title: 'My title', branch: 'feat/mine' }, { theme });
    await page.evaluate(axeSource);
    const result = await page.evaluate(() => window.axe.run(document.querySelector('#conflict-banner-host'), { resultTypes: ['violations'] }));
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
    await leave(page);
  });
}
