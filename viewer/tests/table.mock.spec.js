// User intent: the Table is the one screen that uses every filter-chip feature at once (four groups, a long epic list,
// a filter at zero) and sortable headers, so in a real browser its chips must toggle and multi-select, never wrap,
// park the long epic list behind More, its headers must sort by keyboard and say so, and leaving it leaves nothing behind.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD, LONG_IDS_BOARD, DETAIL_TASK, TABLE_BOARD, tableMocks } from './mock-fixtures.js';
import { epicSwatch } from '../js/lib/epics.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

// TABLE_BOARD (mock-fixtures.js): BOARD plus twelve epics "Epic A"…"Epic L"; every one but Epic L has a task.

test.beforeEach(async ({ page }) => { await page.emulateMedia({ reducedMotion: 'reduce' }); });
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

async function boot(page, { theme = 'dark', width = 1440, height = 900, route = '#/table', table, board = TABLE_BOARD } = {}) {
  await page.setViewportSize({ width, height });
  await mockApi(page, tableMocks({ theme, board, table }));
  const puts = [];
  page.on('request', (r) => {
    if (r.method() === 'PUT' && new URL(r.url()).pathname === '/api/viewer/prefs') puts.push(JSON.parse(r.postData() || '{}'));
  });
  await page.goto('/' + route);
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  if (route.startsWith('#/table')) await expect(page.locator('table.tbl')).toBeVisible();
  return puts;
}

// The colour `var(--cat-N)` paints in the page's theme.
const catColour = (page, n) => page.evaluate((n) => {
  const probe = document.createElement('span');
  probe.style.background = `var(--cat-${n})`;
  document.body.append(probe);
  const colour = getComputedStyle(probe).backgroundColor;
  probe.remove();
  return colour;
}, n);
const group = (page, name) => page.getByRole('group', { name });
const chip = (page, name, value) => group(page, name).locator(`.chip[data-value="${value}"]`);
const statusCells = (page) => page.locator('.tbl-row .tbl-cell--status .marker__word').allTextContents();

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

test('at 390×844 no chip row wraps and every rail control is a 44px touch target', async ({ page }) => {
  await boot(page, { width: 390, height: 844, table: { filters: { status: ['done'] } } });
  await expect(page.getByRole('button', { name: 'Clear filters' })).toBeVisible();
  const heights = await page.locator('.tbl-chips').evaluate((rail) => [...rail.querySelectorAll('.chip, .overflow-more, .tbl-clear')]
    .filter((el) => !el.hidden).map((el) => ({ el: el.className, h: el.getBoundingClientRect().height })));
  expect(heights.some((x) => x.el.includes('tbl-clear'))).toBe(true);
  expect(heights.some((x) => x.el.includes('overflow-more'))).toBe(true);
  expect(heights.filter((x) => x.h < 44)).toEqual([]);
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
  // Kanban → Table swaps font listeners: the Kanban's phase strip (one overflowRow) unmounts
  // and drops its listener; the Table's four chip-group overflowRows each add one.
  await expect.poll(() => page.evaluate(() => window.__fonts)).toBe(fontsBefore - 1 + 4);
  await group(page, 'Epic').locator('.overflow-more').click();
  await expect(page.getByRole('dialog', { name: 'More Epic' })).toBeVisible();
  // Live observers on the rail itself (the Table's own) and on each row's chips (overflowRow's).
  const watching = () => page.evaluate(() => {
    const on = (cls) => [...window.__live].filter((o) => o.__targets.some((t) => t.classList?.contains(cls))).length;
    return { rail: on('tbl-chips'), rows: on('chip-row__chips'), host: on('tbl-host') };
  });
  expect(await watching()).toEqual({ rail: 1, rows: 8, host: 1 });   // each row: a ResizeObserver and a MutationObserver
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('.card-task').first()).toBeVisible();
  expect(await page.evaluate(() => window.__fonts)).toBe(fontsBefore);
  expect(await watching()).toEqual({ rail: 0, rows: 0, host: 0 });
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
    const result = await page.evaluate(async () => {
      const aria = window.axe.getRules().map((r) => r.ruleId).filter((id) => id.startsWith('aria-'));
      const opts = (values) => ({ runOnly: { type: 'rule', values }, resultTypes: ['violations'] });
      const all = await window.axe.run(document.getElementById('screen-mount'), opts(['nested-interactive', ...aria]));
      const ink = await window.axe.run({ include: [['#screen-mount']] }, opts(['color-contrast']));
      return [...all.violations, ...ink.violations];
    });
    expect(result.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}

const host = (page) => page.locator('.tbl-host');
// Another writer's change, as the poll would deliver it: the store gets a new board revision and every screen redraws.
const otherWriter = (page, id, patch) => page.evaluate(([id, patch]) => import('/js/store.js').then(({ store }) => {
  const next = structuredClone(store.getBacklog());
  Object.assign(next.tasks.find((t) => t.id === id), patch);
  next.revision = `r-${Date.now()}`;
  store.setBoard(next);
}), [id, patch]);

test('the epic swatch is its categorical colour', async ({ page }) => {
  // One more task, in an epic the board does not list.
  const stray = { id: 'T-9999', title: 'Stray task', status: 'todo', priority: 'low', epic: 'ghost-epic', phase: 'P1', depends_on: [] };
  const board = { ...LONG_IDS_BOARD, tasks: [...LONG_IDS_BOARD.tasks, stray] };
  await boot(page, { board });
  await expect(page.locator('.tbl-row')).toHaveCount(board.tasks.length);
  const cats = [];
  for (const n of [1, 2, 3, 4, 5, 6]) cats.push(await catColour(page, n));
  const cells = await page.locator('.tbl-row').evaluateAll((els) => els.map((el) => {
    const cell = el.querySelector('.tbl-cell--epic');
    const inner = cell.querySelector(':scope > .t-epic-cell');
    return {
      id: el.dataset.taskId,
      swatches: [...cell.querySelectorAll('.epic-swatch')].map((s) => getComputedStyle(s).backgroundColor),
      first: inner?.firstElementChild?.classList.contains('epic-swatch') ?? false,
      text: cell.textContent,
      title: cell.querySelector('.truncate')?.title ?? null,
    };
  }));
  const names = new Map(LONG_IDS_BOARD.epics.map((e) => [e.id, e.name]));
  const want = (t) => (names.has(t.epic)
    ? { id: t.id, swatches: [cats[epicSwatch(t.epic, LONG_IDS_BOARD.epics) - 1]], first: true, text: names.get(t.epic), title: names.get(t.epic) }
    : { id: t.id, swatches: [], first: false, text: t.epic, title: t.epic });
  const byId = new Map(cells.map((c) => [c.id, c]));
  expect(board.tasks.map((t) => byId.get(t.id))).toEqual(board.tasks.map(want));
  // Six swatches, then round again: a task in the 7th epic shows the 1st one's colour.
  const inEpic = (id) => byId.get(board.tasks.find((t) => t.epic === id).id).swatches;
  expect(inEpic('epic-07')).toEqual(inEpic('epic-01'));
  expect(inEpic('epic-01')).toEqual([cats[0]]);
});

test('cells are markers and plain words — no pills, no "···"', async ({ page }) => {
  await boot(page);
  const row = page.locator('.tbl-row[data-task-id="T-102"]');
  await expect(row.locator('.tbl-cell--status .marker__word')).toHaveText('In progress');
  await expect(row.locator('.tbl-cell--priority .marker__word')).toHaveText('Critical');
  await expect(row.locator('.tbl-cell--epic')).toHaveText('Viewer re-skin');
  await expect(page.locator('.t-status, .t-pri, .t-epic, .t-area')).toHaveCount(0);
  const cut = await page.locator('.tbl-cell--status, .tbl-cell--priority').evaluateAll((els) => els.filter((el) => el.scrollWidth > el.clientWidth).length);
  expect(cut).toBe(0);
  await expect(page.locator('.tbl-row[tabindex]')).toHaveCount(0);
});

test('at 1440 with 230 long rows the ID column fits its longest ID and ID and title stay put while the rest scrolls', async ({ page }) => {
  await boot(page, { board: LONG_IDS_BOARD });
  await expect(page.locator('.tbl-row')).toHaveCount(230);
  const ids = await page.locator('.tbl-cell--id').evaluateAll((els) => els.filter((el) => el.scrollWidth > el.clientWidth).map((el) => el.textContent));
  expect(ids).toEqual([]);
  expect(await page.evaluate(() => document.scrollingElement.scrollWidth <= innerWidth)).toBe(true);
  await expect(page.locator('.tbl-frame')).toHaveAttribute('data-more-end', '');
  await expect(page.locator('.tbl-fade')).toHaveCSS('opacity', '1');
  const titles = await page.locator('.tbl-cell--title .truncate').evaluateAll((els) => els.filter((el) => el.title !== el.textContent).length);
  expect(titles).toBe(0);
  await host(page).evaluate((el) => { el.scrollLeft = el.scrollWidth; });
  await expect(page.locator('.tbl-frame')).toHaveAttribute('data-scrolled', '');
  await expect(page.locator('.tbl-frame')).not.toHaveAttribute('data-more-end', '');
  const at = await page.evaluate(() => {
    const h = document.querySelector('.tbl-host').getBoundingClientRect().left;
    const id = document.querySelector('.tbl-row .tbl-cell--id').getBoundingClientRect();
    const title = document.querySelector('.tbl-row .tbl-cell--title').getBoundingClientRect().left;
    return { id: Math.round(id.left - h), title: Math.round(title - id.right) };
  });
  expect(Math.abs(at.id)).toBeLessThanOrEqual(1);
  expect(Math.abs(at.title)).toBeLessThanOrEqual(1);
});

test('a click on a row opens its task; Ctrl+click on its link is the browser\'s', async ({ page }) => {
  await boot(page);
  await page.locator('.tbl-row[data-task-id="T-102"] .tbl-cell--status').click();
  await expect(page.getByRole('dialog', { name: DETAIL_TASK.title })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(page.locator('.modal')).toHaveCount(0);
  const [popup] = await Promise.all([
    page.context().waitForEvent('page'),
    page.locator('.tbl-row[data-task-id="T-102"] .tbl-link').click({ modifiers: ['Control'] }),
  ]);
  await popup.close();
  await expect(page.locator('.modal')).toHaveCount(0);
});

test('a keyboard user opens a row and comes back to it', async ({ page }) => {
  await boot(page);
  // After the last header button, Tab goes to the first row's link: rows are not tab stops of their own.
  await page.locator('th[data-key="started"] button.sort-header').focus();
  await page.keyboard.press('Tab');
  await expect(page.locator('.tbl-row').first().locator('.tbl-link')).toBeFocused();
  await page.keyboard.press('Tab');
  await expect(page.locator('.tbl-row').nth(1).locator('.tbl-link')).toBeFocused();
  const link = page.locator('.tbl-row[data-task-id="T-102"] .tbl-link');
  await link.focus();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog', { name: DETAIL_TASK.title })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(link).toBeFocused();
});

test('a redraw keeps the keyboard on the same task and the frame where it was', async ({ page }) => {
  await boot(page, { board: LONG_IDS_BOARD });
  await host(page).evaluate((el) => { el.scrollTop = 900; el.scrollLeft = 200; });
  const link = page.locator('.tbl-row[data-task-id="T-1040"] .tbl-link');
  await link.evaluate((a) => a.focus({ preventScroll: true }));
  const where = await host(page).evaluate((el) => [el.scrollTop, el.scrollLeft]);
  expect(where[0]).toBeGreaterThan(0);
  await otherWriter(page, 'T-1040', { title: 'Renamed by another writer' });
  await expect(link).toHaveText('Renamed by another writer');
  await expect(link).toBeFocused();
  expect(await host(page).evaluate((el) => [el.scrollTop, el.scrollLeft])).toEqual(where);
  // The focused task leaves the filtered set: the row now at its place takes the keyboard, never <body>.
  await chip(page, 'Status', 'todo').click();
  const ids = await page.locator('.tbl-row').evaluateAll((rows) => rows.map((r) => r.dataset.taskId));
  const at = 3;
  await page.locator(`.tbl-row[data-task-id="${ids[at]}"] .tbl-link`).focus();
  await otherWriter(page, ids[at], { status: 'done' });
  await expect(page.locator('.tbl-row')).toHaveCount(ids.length - 1);
  const now = page.locator('.tbl-row .tbl-link:focus');
  await expect(now).toHaveCount(1);
  expect(await now.evaluate((a) => a.closest('tr').dataset.taskId)).toBe(ids[at + 1]);
});

test('a link with ?status= opens the Table with those chips pressed, without saving them', async ({ page }) => {
  const puts = await boot(page, { route: '#/table?status=in-progress', table: { filters: { epic: [] } } });
  await expect(chip(page, 'Status', 'in-progress')).toHaveAttribute('aria-pressed', 'true');
  const ip = TABLE_BOARD.tasks.filter((t) => t.status === 'in-progress').length;
  await expect.poll(() => statusCells(page)).toEqual(Array(ip).fill('In progress'));
  await expect(page.locator('.sidebar-link[data-key="table"]')).toHaveClass(/active/);
  expect(puts.filter((b) => b.table)).toEqual([]);
});

test('?status= takes a comma list, ignores unknown values, and an all-unknown value changes nothing', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await boot(page, { route: '#/table?status=in-review,blocked,bogus,in-review' });
  await expect(page.locator('.tbl-chips .chip[aria-pressed="true"]')).toHaveCount(2);
  await expect.poll(async () => new Set(await statusCells(page))).toEqual(new Set(['In review', 'Blocked']));
  await page.evaluate(() => { location.hash = '#/table?status=bogus'; });
  await expect(page.locator('.tbl-row')).toHaveCount(TABLE_BOARD.tasks.length);
  await expect(page.locator('.tbl-chips .chip[aria-pressed="true"]')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('no tasks is a state block', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  const empty = { ...BOARD, tasks: [] };
  await mockApi(page, { '/api/board': empty, '/api/backlog': empty, '/api/bugs': [] });
  await page.goto('/#/table');
  await expect(page.locator('.tbl-empty .tm-empty__headline')).toHaveText('No tasks yet.');
  await expect(page.locator('.tbl-empty .tm-empty__label')).toHaveText('Table');
});

test('no match is a state block whose one action clears the filters', async ({ page }) => {
  await boot(page);
  await page.getByPlaceholder('Filter… (prefix ! to exclude)').fill('nothing-matches-this');
  const block = page.locator('.tbl-empty .tm-empty');
  await expect(block.locator('.tm-empty__label')).toHaveText('No match');
  await expect(block.locator('.tm-empty__headline')).toHaveText(`0 of ${TABLE_BOARD.tasks.length} tasks match.`);
  await block.getByRole('button', { name: 'Clear filters' }).click();
  await expect(page.locator('.tbl-row')).toHaveCount(TABLE_BOARD.tasks.length);
});

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}, 1440): every table cell passes contrast`, async ({ page }) => {
    await boot(page, { theme, board: LONG_IDS_BOARD });
    await page.evaluate(axeSource);
    const v = await page.evaluate(async () => (await window.axe.run(document.getElementById('screen-mount'),
      { runOnly: { type: 'rule', values: ['color-contrast', 'nested-interactive'] }, resultTypes: ['violations'] })).violations);
    expect(v.map((x) => `${x.id}: ${x.nodes.length} — ${x.nodes.slice(0, 3).map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}

// ── Review round 1 ────────────────────────────────────────────────────────────────────────────────────────────────

const subcount = (page) => page.locator('#topbar-count');
const searchBox = (page) => page.getByPlaceholder('Filter… (prefix ! to exclude)');

test('the count says how many show only while a chip, the search or a ?status= link narrows the list', async ({ page }) => {
  await boot(page, { board: LONG_IDS_BOARD });
  await expect(subcount(page)).toHaveText('230 tasks');
  await chip(page, 'Status', 'todo').click();
  const todo = LONG_IDS_BOARD.tasks.filter((t) => t.status === 'todo').length;
  await expect(subcount(page)).toHaveText(`230 tasks · ${todo} visible`);
  await page.getByRole('button', { name: 'Clear filters' }).click();
  await expect(subcount(page)).toHaveText('230 tasks');
  await searchBox(page).fill('T-100');
  const hits = LONG_IDS_BOARD.tasks.filter((t) => `${t.id} ${t.title || ''} ${t.branch || ''}`.toLowerCase().includes('t-100')).length;
  await expect(subcount(page)).toHaveText(`230 tasks · ${hits} visible`);
  await searchBox(page).fill('');
  await expect(subcount(page)).toHaveText('230 tasks');
});

test('a ?status= link counts as narrowing; ?status=archived is no chip and changes nothing', async ({ page }) => {
  await boot(page, { route: '#/table?status=in-progress' });
  const ip = TABLE_BOARD.tasks.filter((t) => t.status === 'in-progress').length;
  await expect(subcount(page)).toHaveText(`${TABLE_BOARD.tasks.length} tasks · ${ip} visible`);
  await page.evaluate(() => { location.hash = '#/table?status=archived'; });
  await expect(page.locator('.tbl-row')).toHaveCount(TABLE_BOARD.tasks.length);
  await expect(page.locator('.tbl-chips .chip[aria-pressed="true"]')).toHaveCount(0);
  await expect(subcount(page)).toHaveText(`${TABLE_BOARD.tasks.length} tasks`);
});

test('at 900 wide the no-match block and its action stay inside the frame, wherever it is scrolled', async ({ page }) => {
  await boot(page, { width: 900, height: 800 });
  await searchBox(page).fill('nothing-matches-this');
  await expect(page.locator('.tbl-empty .tm-empty')).toBeVisible();
  const inView = () => page.evaluate(() => {
    const host = document.querySelector('.tbl-host');
    const r = host.getBoundingClientRect();
    const right = r.left + host.clientWidth;
    const inside = (el) => { const b = el.getBoundingClientRect(); return b.left >= r.left - 0.5 && b.right <= right + 0.5; };
    return { scrolls: host.scrollWidth > host.clientWidth, block: inside(host.querySelector('.tbl-empty .tm-empty')), action: inside(host.querySelector('.tbl-empty .tm-empty button')) };
  });
  expect(await inView()).toEqual({ scrolls: true, block: true, action: true });
  await host(page).evaluate((el) => { el.scrollLeft = el.scrollWidth; });
  await expect(page.locator('.tbl-frame')).toHaveAttribute('data-scrolled', '');
  expect(await inView()).toEqual({ scrolls: true, block: true, action: true });
});

test('the fade stops at classic scrollbars, and the header height is the host\'s scroll padding', async ({ page }) => {
  await boot(page, { board: LONG_IDS_BOARD });
  // Headless Chromium hides scrollbars, even styled ones; a 15px right and bottom border takes the same room a classic
  // Windows scrollbar does (offsetWidth - clientWidth counts both alike), so the arithmetic is what is checked.
  await page.addStyleTag({ content: '.tbl-host { border-right: 15px solid transparent; border-bottom: 15px solid transparent; }' });
  await expect.poll(() => host(page).evaluate((el) => [el.offsetWidth - el.clientWidth, el.offsetHeight - el.clientHeight])).toEqual([15, 15]);
  await expect(page.locator('.tbl-fade')).toHaveCSS('right', '15px');
  await expect(page.locator('.tbl-fade')).toHaveCSS('bottom', '15px');
  const head = await page.locator('table.tbl thead').evaluate((el) => `${el.offsetHeight}px`);
  await expect(host(page)).toHaveCSS('scroll-padding-top', head);
});

test('at 800 wide a long ID lets the title scroll away, so the other columns come into view beside a fixed ID', async ({ page }) => {
  await boot(page, { width: 800, height: 800, board: LONG_IDS_BOARD });
  await expect(page.locator('.tbl-frame')).toHaveAttribute('data-title-loose', '');
  const seen = await host(page).evaluate((el) => {
    const id = el.querySelector('.tbl-row .tbl-cell--id');
    const status = el.querySelector('.tbl-row .tbl-cell--status');
    el.scrollLeft = status.offsetLeft - id.offsetWidth;
    const b = status.getBoundingClientRect();
    const hit = document.elementFromPoint(b.left + b.width / 2, b.top + b.height / 2);
    return { status: status.contains(hit), id: Math.round(id.getBoundingClientRect().left - el.getBoundingClientRect().left) };
  });
  expect(seen.status).toBe(true);
  expect(Math.abs(seen.id)).toBeLessThanOrEqual(1);
});

test('at 1440 the same long IDs keep the title fixed beside the ID', async ({ page }) => {
  await boot(page, { board: LONG_IDS_BOARD });
  await expect(page.locator('.tbl-frame')).not.toHaveAttribute('data-title-loose', '');
});

test('with very short IDs the ID header and its sort arrow are not cut', async ({ page }) => {
  const short = { ...TABLE_BOARD, tasks: TABLE_BOARD.tasks.slice(0, 3).map((t, i) => ({ ...t, id: `T-${i + 1}` })) };
  await boot(page, { board: short });
  const th = await page.locator('th[data-key="id"]').evaluate((el) => [el.scrollWidth, el.clientWidth]);
  expect(th[0]).toBeLessThanOrEqual(th[1]);
});

test('a Shift-click on a row, or the end of a text selection in it, opens nothing', async ({ page }) => {
  await boot(page);
  const row = page.locator('.tbl-row[data-task-id="T-102"]');
  await row.locator('.tbl-cell--status').click({ modifiers: ['Shift'] });
  await page.evaluate(() => getSelection().removeAllRanges());
  const b = await row.locator('.tbl-cell--epic > span').boundingBox();
  await page.mouse.move(b.x + 2, b.y + b.height / 2);
  await page.mouse.down();
  await page.mouse.move(b.x + b.width - 2, b.y + b.height / 2, { steps: 5 });
  await page.mouse.up();
  expect(await page.evaluate(() => String(getSelection()))).not.toBe('');
  await page.waitForTimeout(300);
  await expect(page.locator('.modal')).toHaveCount(0);
  expect(page.context().pages()).toHaveLength(1);
  expect(await page.evaluate(() => location.hash)).toBe('#/table');
});

// At phone width the page is the one scroller (the shell's choice): a box scrolling inside a scrolling page would trap
// the thumb, so the frame grows with its cards and the sticky topbar stays put above them.
test('at 390 with 230 long rows nothing scrolls sideways, no ID is cut, and the page — not the frame — scrolls to the last card', async ({ page }) => {
  await boot(page, { width: 390, height: 844, board: LONG_IDS_BOARD });
  await expect(page.locator('.tbl-row')).toHaveCount(230);
  const look = await page.evaluate(() => {
    const host = document.querySelector('.tbl-host');
    return {
      pageX: document.scrollingElement.scrollWidth - innerWidth,
      hostX: host.scrollWidth - host.clientWidth,
      hostScrolls: host.scrollHeight > host.clientHeight + 1,
      cutIds: [...document.querySelectorAll('.tbl-cell--id .t-id')].filter((el) => el.getBoundingClientRect().right > el.closest('.tbl-row').getBoundingClientRect().right + 0.5 || el.closest('td').scrollWidth > el.closest('td').clientWidth).length,
      lostWords: [...document.querySelectorAll('.tbl-cell--title .truncate')].filter((el) => el.title !== el.textContent).length,
      head: getComputedStyle(document.querySelector('.tbl thead')).display,
      short: [...document.querySelectorAll('.tbl-row')].filter((r) => r.getBoundingClientRect().height < 44).length,
    };
  });
  expect(look).toEqual({ pageX: 0, hostX: 0, hostScrolls: false, cutIds: 0, lostWords: 0, head: 'none', short: 0 });
  await page.evaluate(() => window.scrollTo(0, document.scrollingElement.scrollHeight));
  expect(await page.evaluate(() => scrollY)).toBeGreaterThan(0);
  const last = await page.locator('.tbl-row').last().evaluate((r) => {
    const b = r.getBoundingClientRect();
    return { top: b.top >= document.querySelector('.topbar').getBoundingClientRect().bottom, bottom: b.bottom <= innerHeight + 0.5 };
  });
  expect(last).toEqual({ top: true, bottom: true });
  // The topbar is sticky, not merely scrolled off: part-way down the page it still sits flush with the top edge.
  await page.evaluate(() => window.scrollTo(0, 3000));
  const stuck = await page.evaluate(() => ({ scrolled: scrollY > 0, top: document.querySelector('.topbar').getBoundingClientRect().top }));
  expect(stuck).toEqual({ scrolled: true, top: 0 });
});

// A saved sort naming a gone or unsortable column (or a bad direction) falls back to the default instead of leaving
// the phone Sort select blank.
for (const sort of [{ by: 'bogus', dir: 'asc' }, { by: 'branch', dir: 'asc' }, { by: 'title', dir: 'up' }]) {
  test(`a stale saved sort ${sort.by}:${sort.dir} falls back to priority ascending`, async ({ page }) => {
    await boot(page, { width: 390, height: 844, table: { sort } });
    await expect(page.locator('#tbl-sort')).toHaveValue('priority:asc');
  });
}

test('at 390 a redraw keeps the keyboard on the same card and the page where it was', async ({ page }) => {
  await boot(page, { width: 390, height: 844, board: LONG_IDS_BOARD });
  const link = page.locator('.tbl-row[data-task-id="T-1040"] .tbl-link');
  await link.scrollIntoViewIfNeeded();
  await link.focus();
  const y = await page.evaluate(() => scrollY);
  expect(y).toBeGreaterThan(0);
  await otherWriter(page, 'T-1040', { title: 'Renamed by another writer' });
  await expect(link).toHaveText('Renamed by another writer');
  await expect(link).toBeFocused();
  expect(await page.evaluate(() => scrollY)).toBe(y);
});

test('at 390 a card reads ID · priority, title, status · size, epic — and the whole card opens its task', async ({ page }) => {
  await boot(page, { width: 390, height: 844 });
  const card = page.locator('.tbl-row[data-task-id="T-102"]');
  const box = async (sel) => card.locator(sel).boundingBox();
  const id = await box('.tbl-cell--id'); const pri = await box('.tbl-cell--priority');
  const title = await box('.tbl-cell--title'); const status = await box('.tbl-cell--status'); const epic = await box('.tbl-cell--epic');
  expect(Math.abs(id.y - pri.y)).toBeLessThanOrEqual(2);
  expect(title.y).toBeGreaterThan(id.y);
  expect(status.y).toBeGreaterThan(title.y);
  expect(epic.y).toBeGreaterThan(status.y);
  for (const hidden of ['phase', 'area', 'branch', 'started']) await expect(card.locator(`.tbl-cell--${hidden}`)).toBeHidden();
  // No column divider inside a card, and nothing clips the title link's focus ring.
  await expect(card.locator('.tbl-cell--id')).toHaveCSS('border-right-width', '0px');
  await expect(card.locator('.tbl-cell--title')).toHaveCSS('overflow', 'visible');
  await card.click({ position: { x: 4, y: 4 } });
  await expect(page.getByRole('dialog', { name: DETAIL_TASK.title })).toBeVisible();
});

test('at 390 the Sort select sorts, says the current sort and is saved; at 1440 it is not shown', async ({ page }) => {
  const puts = await boot(page, { width: 390, height: 844 });
  const sort = page.getByLabel('Sort');
  await expect(sort).toHaveValue('priority:asc');
  await sort.selectOption('title:desc');
  const titles = TABLE_BOARD.tasks.map((t) => t.title.toLowerCase()).sort();
  await expect(page.locator('.tbl-row .tbl-cell--title').first()).toHaveText(new RegExp(`^${titles.at(-1)}$`, 'i'));
  await expect.poll(() => puts.filter((b) => b.table?.sort).at(-1)?.table.sort).toEqual({ by: 'title', dir: 'desc' });
  expect(await sort.evaluate((el) => el.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect(page.locator('.tbl-sortbar')).toBeHidden();
});

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}, 390): every card passes contrast and nothing is nested`, async ({ page }) => {
    await boot(page, { theme, width: 390, height: 844, board: LONG_IDS_BOARD });
    await page.evaluate(axeSource);
    const v = await page.evaluate(async () => (await window.axe.run(document.getElementById('screen-mount'),
      { runOnly: { type: 'rule', values: ['color-contrast', 'nested-interactive', 'label', 'select-name'] }, resultTypes: ['violations'] })).violations);
    expect(v.map((x) => `${x.id}: ${x.nodes.length}`)).toEqual([]);
  });
}

// Task 7: the Table's primary and its count live in topbar row 1; row 2 is the search (and the Filters button the
// overflow row keeps there). The count names the whole list and adds " · m visible" only while a filter narrows it.
for (const width of [1440, 390]) {
  test(`at ${width}px Add task and the task count are in topbar row 1; row 2 is the search`, async ({ page }) => {
    await boot(page, { width, height: width === 390 ? 844 : 900 });
    await expect(page.locator('table.tbl')).toBeVisible();
    const add = page.locator('#topbar-primary [aria-label="Add task"]');
    await expect(add).toBeVisible();
    await expect(add).toHaveClass(/\bbtn\b/);
    await expect(add).toHaveClass(/\bbtn--primary\b/);
    if (width === 390) {
      const box = await add.boundingBox();
      expect(box.width).toBeGreaterThanOrEqual(44);
      expect(box.height).toBeGreaterThanOrEqual(44);
    }
    const count = page.locator('#topbar-count');
    const n = TABLE_BOARD.tasks.length;
    await expect(count).toHaveText(`${n} tasks`);
    await expect(count).toHaveAttribute('title', `${n} tasks`);
    // At 390 each chip group is an overflow row of its own: Done may sit behind the Status group's More.
    if (!(await chip(page, 'Status', 'done').isVisible())) await page.getByRole('button', { name: /^More Status/ }).click();
    await chip(page, 'Status', 'done').click();
    await expect(count).toHaveText(`${n} tasks · 3 visible`);
    await page.keyboard.press('Escape');
    const kids = await page.locator('#topbar-actions > *').evaluateAll((els) => els.map((el) => el.className));
    expect(kids).toHaveLength(2);
    expect(kids.some((c) => /\btm-search\b/.test(c))).toBe(true);
    expect(kids.some((c) => /\boverflow-more\b/.test(c))).toBe(true);
    await expect(page.locator('.tm-subcount')).toHaveCount(0);
    if (width === 390) {
      expect(await page.evaluate(() => document.scrollingElement.scrollWidth <= innerWidth)).toBe(true);
    }
    await add.click();
    await expect(page.getByRole('dialog', { name: 'Create task' })).toBeVisible();
  });
}

test('leaving the Table for Epics takes Add task out of row 1 and replaces the count', async ({ page }) => {
  await boot(page);
  await expect(page.locator('#topbar-primary [aria-label="Add task"]')).toBeVisible();
  await expect(page.locator('#topbar-count')).toHaveText(`${TABLE_BOARD.tasks.length} tasks`);
  await page.evaluate(() => { location.hash = '#/epics'; });
  await expect(page.locator('#page-title')).toHaveText('Epics');
  await expect(page.locator('#topbar-primary [aria-label="Add task"]')).toHaveCount(0);
  await expect(page.locator('#topbar-primary')).toBeEmpty();
  await expect(page.locator('#topbar-count')).toHaveText(`${TABLE_BOARD.epics.length} epics`);
});
