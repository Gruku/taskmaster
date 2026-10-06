// User intent: the Archived screen in a real browser — groups sit under epic-name headings in order, rows open their
// task by keyboard and give focus back, the count says how many are visible, a redraw keeps focus, leaving mid-search
// touches nothing, forty long titles fit a phone, and neither theme has an axe violation.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { archivedMocks } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

let errors = [];
test.beforeEach(async ({ page }) => {
  errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await page.emulateMedia({ reducedMotion: 'reduce' });
});
test.afterEach(async ({ page }) => {
  expect(unmockedWrites(page)).toEqual([]);
  expect(errors).toEqual([]);
});

async function boot(page, { theme = 'dark', width = 1440, height = 900 } = {}) {
  await page.setViewportSize({ width, height });
  await mockApi(page, archivedMocks({ theme }));
  await page.goto('/#/archived');
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await expect(page.locator('.arch-row[data-task-id="T-1001"]')).toBeVisible();
}

async function axe(page, scope, rules) {
  await page.evaluate(axeSource);
  const result = await page.evaluate(([sel, only]) => window.axe.run(document.querySelector(sel), {
    resultTypes: ['violations'], ...(only ? { runOnly: { type: 'rule', values: only } } : {}),
  }), [scope, rules]);
  return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`);
}

const search = (page) => page.getByRole('textbox', { name: 'Filter archived tasks' });
const rowLink = (page, id) => page.locator(`.arch-row[data-task-id="${id}"] > .link-row__link`);

test('headings name the epics in order, each with its count', async ({ page }) => {
  await boot(page);
  const texts = await page.locator('#screen-mount').getByRole('heading', { level: 2 }).allTextContents();
  expect(texts).toEqual(['Viewer re-skin10', 'Native store10', 'legacy10', 'No epic10']);
  await expect(page.locator('section.arch-group[aria-labelledby] > h2.arch-group-h > .arch-group-count')).toHaveCount(4);
  expect(await axe(page, '#screen-mount', ['heading-order'])).toEqual([]);
});

test('keyboard: an archived row opens its task and Escape returns to the row', async ({ page }) => {
  await boot(page);
  await search(page).focus();
  await page.keyboard.press('Tab');
  await expect(rowLink(page, 'T-1001')).toBeFocused();
  await page.keyboard.press('Enter');
  const dialog = page.getByRole('dialog', { name: 'Archived task 1001' });
  await expect(dialog).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(dialog).toHaveCount(0);
  await expect(rowLink(page, 'T-1001')).toBeFocused();
});

test('the count says how many are visible', async ({ page }) => {
  await boot(page);
  await expect(page.locator('#topbar-count')).toHaveText('40 archived tasks');
  await search(page).fill('T-101');
  await expect(page.locator('.arch-row')).toHaveCount(10);
  const n = await page.locator('.arch-row').count();
  expect(n).toBeLessThan(40);
  await expect(page.locator('#topbar-count')).toHaveText(`40 archived tasks · ${n} visible`);
});

test('no match offers to clear the search', async ({ page }) => {
  await boot(page);
  await search(page).fill('zzz');
  await expect(page.getByText('No archived tasks match "zzz"')).toBeVisible();
  await page.locator('#screen-mount').getByRole('button', { name: 'Clear search' }).click();
  await expect(page.locator('.arch-row')).toHaveCount(40);
  await expect(search(page)).toBeFocused();
  await expect(search(page)).toHaveValue('');
  await expect(page.locator('.tm-search')).not.toHaveClass(/tm-search--has-value/);
  await expect(page.locator('#topbar-count')).toHaveText('40 archived tasks');
});

test('the reason is a tag, not italics', async ({ page }) => {
  await boot(page);
  const reason = page.locator('.arch-row[data-task-id="T-1002"] .arch-reason');
  await expect(reason).toHaveText('Superseded by T-140');
  expect(await reason.evaluate((el) => getComputedStyle(el).fontStyle)).toBe('normal');
});

test('a board redraw keeps focus on the focused row', async ({ page }) => {
  await boot(page);
  const third = page.locator('.arch-row').nth(2);
  const id = await third.getAttribute('data-task-id');
  await third.locator('.link-row__link').focus();
  await page.evaluate(async () => {
    const { store } = await import('/js/store.js');
    store.setBoard({ ...store.getBacklog(), revision: 'r2' });
  });
  const focused = await page.evaluate(() => {
    const a = document.activeElement;
    return { tag: a?.tagName, id: a?.closest('.arch-row')?.dataset.taskId };
  });
  expect(focused).toEqual({ tag: 'A', id });
});

test('leaving within the search debounce leaves the next screen\'s count alone', async ({ page }) => {
  await boot(page);
  await search(page).pressSequentially('T-10');
  await page.evaluate(() => { location.hash = '#/settings'; });
  await page.waitForTimeout(400);
  await expect(page.locator('#topbar-count')).toHaveText('');
});

test('forty archived tasks with long titles stay inside a phone screen', async ({ page }) => {
  await boot(page, { width: 390, height: 844 });
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  const report = await page.evaluate(() => ({
    idsCut: [...document.querySelectorAll('.arch-id')].filter((el) => el.scrollWidth > el.clientWidth).length,
    titlesWithoutWords: [...document.querySelectorAll('.arch-title')].filter((el) => el.title !== el.textContent).length,
    shortLinks: [...document.querySelectorAll('.arch-row > .link-row__link')].filter((el) => el.getBoundingClientRect().height < 44).length,
    rows: document.querySelectorAll('.arch-row').length,
  }));
  expect(report).toEqual({ idsCut: 0, titlesWithoutWords: 0, shortLinks: 0, rows: 40 });
});

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): the screen`, async ({ page }) => {
    await boot(page, { theme });
    expect(await axe(page, '#screen-mount')).toEqual([]);
  });
}

// Plan 4's accessibility gate reuses archivedMocks() once per theme; this pins that it loads real content, not a state block.
for (const theme of ['dark', 'light']) {
  test(`archived loads its content from archivedMocks() in ${theme}`, async ({ page }) => {
    await mockApi(page, archivedMocks({ theme }));
    await page.goto('/#/archived');
    await expect(page.locator('.arch-row[data-task-id="T-1001"] .link-row__link')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.locator('.tm-empty[data-state="error"]')).toHaveCount(0);
  });
}
