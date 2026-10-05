// User intent: the Table is the one screen that uses every filter-chip feature at once (four groups, a long epic list,
// a filter at zero) and sortable headers, so in a real browser its chips must toggle and multi-select, never wrap,
// park the long epic list behind More, its headers must sort by keyboard and say so, and leaving it leaves nothing behind.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

// BOARD plus twelve epics "Epic A"…"Epic L"; every one but Epic L has a task, and the extra tasks carry areas so all
// four chip groups are drawn.
const LETTERS = 'ABCDEFGHIJKL'.split('');
const STATUSES = ['todo', 'done', 'blocked', 'in-progress', 'in-review'];
const PRIORITIES = ['low', 'medium', 'high', 'critical'];
const AREAS = ['viewer-ui', 'store', 'docs'];
const TABLE_BOARD = {
  ...BOARD,
  epics: [...BOARD.epics, ...LETTERS.map((l) => ({ id: `epic-${l.toLowerCase()}`, name: `Epic ${l}`, status: 'active', phase: 'P1' }))],
  tasks: [...BOARD.tasks, ...LETTERS.slice(0, 11).map((l, i) => ({
    id: `T-${201 + i}`, title: `Extra task ${l}`, status: STATUSES[i % 5], priority: PRIORITIES[i % 4],
    epic: `epic-${l.toLowerCase()}`, area: AREAS[i % 3], phase: 'P1', depends_on: [],
  }))],
};

test.beforeEach(async ({ page }) => { await page.emulateMedia({ reducedMotion: 'reduce' }); });
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

async function boot(page, { theme = 'dark', width = 1440, height = 900, route = '#/table', table } = {}) {
  await page.setViewportSize({ width, height });
  await mockApi(page, {
    '/api/viewer/prefs': { theme, ui: {}, screens: {}, ...(table ? { table } : {}) },
    '/api/board': TABLE_BOARD, '/api/backlog': TABLE_BOARD, '/api/bugs': [],
  });
  const puts = [];
  page.on('request', (r) => {
    if (r.method() === 'PUT' && new URL(r.url()).pathname === '/api/viewer/prefs') puts.push(JSON.parse(r.postData() || '{}'));
  });
  await page.goto('/' + route);
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  if (route === '#/table') await expect(page.locator('table.tbl')).toBeVisible();
  return puts;
}

const group = (page, name) => page.getByRole('group', { name });
const chip = (page, name, value) => group(page, name).locator(`.chip[data-value="${value}"]`);
const statusCells = (page) => page.locator('.tbl-row .tbl-cell--status').allTextContents();

test('every chip is a toggle button and every group with options is a row', async ({ page }) => {
  await boot(page);
  for (const name of ['Status', 'Priority', 'Epic', 'Area']) await expect(group(page, name)).toHaveCount(1);
  const chips = page.locator('.tbl-chips .chip');
  expect(await chips.count()).toBeGreaterThan(10);
  for (const el of await chips.all()) {
    expect(await el.evaluate((b) => b.tagName)).toBe('BUTTON');
    await expect(el).toHaveAttribute('aria-pressed', /^(true|false)$/);
  }
  await expect(chip(page, 'Status', 'in-progress').locator('.chip__label')).toHaveText('In progress');
  await expect(chip(page, 'Priority', 'critical').locator('.chip__label')).toHaveText('Critical');
  await expect(chip(page, 'Epic', 'viewer').locator('.chip__label')).toHaveText('Viewer re-skin');
  await expect(chip(page, 'Epic', 'viewer').locator('.chip__swatch')).toHaveCount(1);
  // count = tasks in the whole backlog with that value
  await expect(chip(page, 'Status', 'done').locator('.chip__count')).toHaveText(String(TABLE_BOARD.tasks.filter((t) => t.status === 'done').length));
  await expect(page.locator('.tbl-chips .chip-row__label').first()).toHaveAttribute('title', /shift-click/);
  // Nothing set: no Clear button.
  await expect(page.getByRole('button', { name: 'Clear filters' })).toHaveCount(0);
});

test('click Done filters to Done; shift+click Blocked adds Blocked; Clear filters empties both', async ({ page }) => {
  await boot(page);
  await chip(page, 'Status', 'done').click();
  await expect(chip(page, 'Status', 'done')).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(() => statusCells(page)).toEqual(Array(TABLE_BOARD.tasks.filter((t) => t.status === 'done').length).fill('Done'));

  await chip(page, 'Status', 'blocked').click({ modifiers: ['Shift'] });
  await expect(chip(page, 'Status', 'blocked')).toHaveAttribute('aria-pressed', 'true');
  await expect(chip(page, 'Status', 'done')).toHaveAttribute('aria-pressed', 'true');
  const cells = await statusCells(page);
  expect(new Set(cells)).toEqual(new Set(['Done', 'Blocked']));
  expect(cells.length).toBe(TABLE_BOARD.tasks.filter((t) => ['done', 'blocked'].includes(t.status)).length);

  const clear = page.getByRole('button', { name: 'Clear filters' });
  await expect(clear).toBeVisible();
  await expect(clear).toHaveClass(/(^|\s)btn(\s|$)/);
  await expect(clear).toHaveClass(/btn--ghost/);
  await expect(clear).toHaveClass(/btn--sm/);
  await expect(clear.locator('svg.icon')).toHaveCount(1);
  await clear.click();
  await expect(page.locator('.tbl-chips .chip[aria-pressed="true"]')).toHaveCount(0);
  await expect(page.locator('.tbl-row')).toHaveCount(TABLE_BOARD.tasks.length);
  await expect(page.getByRole('button', { name: 'Clear filters' })).toHaveCount(0);
});

test('a search alone shows Clear filters', async ({ page }) => {
  await boot(page);
  await page.getByPlaceholder('Filter… (prefix ! to exclude)').fill('token');
  await expect(page.getByRole('button', { name: 'Clear filters' })).toBeVisible();
});

test('at 1440×900 the Epic row stays on one line with the rest behind More, and the empty epic is disabled', async ({ page }) => {
  await boot(page);
  const epic = group(page, 'Epic');
  const more = epic.locator('.overflow-more');
  await expect(more).toBeVisible();
  const tops = await epic.locator('.chip-row__chips > .chip').evaluateAll((els) => els.map((el) => el.offsetTop));
  expect(tops.length).toBeGreaterThan(0);
  expect(new Set(tops).size).toBe(1);
  await more.click();
  const pop = page.getByRole('dialog', { name: 'More Epic' });
  await expect(pop).toBeVisible();
  await expect(pop.locator('.chip[data-value="epic-l"]')).toBeDisabled();
  await expect(pop.locator('.chip[data-value="epic-k"]')).toBeEnabled();
});

test('a pressed epic at zero, parked behind More, stays enabled and is announced on More', async ({ page }) => {
  await boot(page, { table: { filters: { epic: ['epic-l'] } } });
  const more = group(page, 'Epic').locator('.overflow-more');
  await expect(more).toBeVisible();
  await expect(more.locator('.overflow-more__on')).toHaveText('· 1 on');
  await expect(more).toHaveAttribute('aria-label', /1 selected/);
  await more.click();
  const parked = page.getByRole('dialog', { name: 'More Epic' }).locator('.chip[data-value="epic-l"]');
  await expect(parked).toHaveAttribute('aria-pressed', 'true');
  await expect(parked).toBeEnabled();
  await parked.click();
  await expect(page.locator('.tbl-row')).toHaveCount(TABLE_BOARD.tasks.length);
});

test('at 390×844 no chip row wraps', async ({ page }) => {
  await boot(page, { width: 390, height: 844 });
  const rows = page.locator('.tbl-chips .chip-row');
  expect(await rows.count()).toBe(4);
  for (const row of await rows.all()) {
    const tops = await row.locator('.chip-row__chips > .chip').evaluateAll((els) => els.map((el) => el.offsetTop));
    expect(new Set(tops).size).toBeLessThanOrEqual(1);
    // Nothing the row shows is clipped by its own edge.
    const fits = await row.locator('.chip-row__chips').evaluate((r) => {
      const edge = r.getBoundingClientRect().right;
      return [...r.children].filter((c) => !c.hidden && !c.classList.contains('popover')).every((c) => c.getBoundingClientRect().right <= edge + 0.5);
    });
    expect(fits).toBe(true);
  }
  const scroll = await page.locator('.tbl-chips').evaluate((el) => el.scrollWidth <= el.clientWidth + 0.5);
  expect(scroll).toBe(true);
});

// Status and Priority rows take their chips' width, so a parked row is narrower and would never see the room come
// back by itself; 800px stays on one side of the phone breakpoint, where nothing else resizes the chips.
for (const width of [390, 800]) {
  test(`Status chips parked at ${width}px come back when the window widens`, async ({ page }) => {
    await boot(page, { width, height: 844 });
    const status = group(page, 'Status');
    await expect(status.locator('.overflow-more')).toBeVisible();
    await page.setViewportSize({ width: 1440, height: 900 });
    await expect(status.locator('.overflow-more')).toBeHidden();
    await expect(status.locator('.chip-row__chips > .chip')).toHaveCount(5);
  });
}

test('the Title header sorts by keyboard, says so with aria-sort, and the sort is saved', async ({ page }) => {
  const puts = await boot(page);
  const titles = TABLE_BOARD.tasks.map((t) => t.title.toLowerCase()).sort();
  await page.locator('th[data-key="id"] button.sort-header').focus();
  await page.keyboard.press('Tab');
  const title = page.locator('th[data-key="title"] button.sort-header');
  await expect(title).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('th[aria-sort]')).toHaveCount(1);
  await expect(page.locator('th[aria-sort="ascending"]')).toHaveAttribute('data-key', 'title');
  await expect(page.locator('.tbl-row .tbl-cell--title').first()).toHaveText(new RegExp(`^${titles[0]}$`, 'i'));
  await expect(title).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.locator('th[aria-sort="descending"]')).toHaveAttribute('data-key', 'title');
  await expect(page.locator('.tbl-row .tbl-cell--title').first()).toHaveText(new RegExp(`^${titles.at(-1)}$`, 'i'));
  await expect.poll(() => puts.filter((b) => b.table?.sort).at(-1)?.table.sort, { timeout: 5000 }).toEqual({ by: 'title', dir: 'desc' });
  // Every header is a sortHeader; Branch is the one that does not sort.
  await expect(page.locator('table.tbl th.sort-th')).toHaveCount(10);
  await expect(page.locator('th[data-key="branch"] button')).toHaveCount(0);
});

test('leaving the Table, even with More open, leaves no observer or font listener behind', async ({ page }) => {
  await page.addInitScript(() => {
    const live = new Set();
    window.__live = live;
    for (const name of ['ResizeObserver', 'MutationObserver']) {
      const Base = window[name];
      window[name] = class extends Base {
        observe(target, ...rest) { this.__targets = [...(this.__targets ?? []), target]; live.add(this); return super.observe(target, ...rest); }
        disconnect() { live.delete(this); return super.disconnect(); }
      };
    }
    window.__fonts = 0;
    const add = document.fonts.addEventListener.bind(document.fonts);
    const remove = document.fonts.removeEventListener.bind(document.fonts);
    const seen = new Set();
    document.fonts.addEventListener = (type, fn, opts) => { if (type === 'loadingdone' && !seen.has(fn)) { seen.add(fn); window.__fonts++; } return add(type, fn, opts); };
    document.fonts.removeEventListener = (type, fn, opts) => { if (type === 'loadingdone' && seen.delete(fn)) window.__fonts--; return remove(type, fn, opts); };
  });
  await boot(page, { route: '#/kanban' });
  await expect(page.locator('.card-task').first()).toBeVisible();
  const fontsBefore = await page.evaluate(() => window.__fonts);
  await page.evaluate(() => { location.hash = '#/table'; });
  await expect(page.locator('.tbl-row').first()).toBeVisible();
  await expect.poll(() => page.evaluate(() => window.__fonts)).toBe(fontsBefore + 4);
  await group(page, 'Epic').locator('.overflow-more').click();
  await expect(page.getByRole('dialog', { name: 'More Epic' })).toBeVisible();
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('.card-task').first()).toBeVisible();
  expect(await page.evaluate(() => window.__fonts)).toBe(fontsBefore);
  const leftover = await page.evaluate(() => [...window.__live].filter((o) => o.__targets.some((t) => t.classList?.contains('chip-row__chips'))).length);
  expect(leftover).toBe(0);
  await expect(page.locator('.popover')).toHaveCount(0);
  await expect(page.locator('.chip-row')).toHaveCount(0);
});

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): the Table has no nested-interactive, contrast or aria violation`, async ({ page }) => {
    await boot(page, { theme });
    await chip(page, 'Status', 'done').click({ modifiers: ['Shift'] });
    await chip(page, 'Status', 'todo').click({ modifiers: ['Shift'] });
    await expect(page.getByRole('button', { name: 'Clear filters' })).toBeVisible();
    await page.evaluate(axeSource);
    // The body cells' status, priority and epic markers are plan 3b's (still the legacy hex pastels, unreadable in the
    // light theme); contrast is checked on everything this task draws: the chip rail and the header row.
    const result = await page.evaluate(async () => {
      const aria = window.axe.getRules().map((r) => r.ruleId).filter((id) => id.startsWith('aria-'));
      const opts = (values) => ({ runOnly: { type: 'rule', values }, resultTypes: ['violations'] });
      const all = await window.axe.run(document.getElementById('screen-mount'), opts(['nested-interactive', ...aria]));
      const ink = await window.axe.run({ include: [['#screen-mount']], exclude: [['#screen-mount .tbl-row']] }, opts(['color-contrast']));
      return [...all.violations, ...ink.violations];
    });
    expect(result.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}
