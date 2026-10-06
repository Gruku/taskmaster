// User intent: the Epics list says each epic's real lifecycle and progress — counted from its tasks — in columns that
// line up, opens an epic by mouse or keyboard, survives another writer's change, and reads at phone width in both themes.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD, LONG_IDS_BOARD, LONG_CLOSEABLE_EPIC, epicPayload } from './mock-fixtures.js';
import { epicSwatch } from '../js/lib/epics.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');
// BOARD plus an epic with no tasks, one whose tasks are all closed, one planned and one in a status the map does not know.
const EPICS_BOARD = {
  ...BOARD,
  epics: [...BOARD.epics,
    { id: 'empty', name: 'No tasks yet', status: 'active' },
    { id: 'closed', name: 'All closed', status: 'active', done_when: 'Both tasks are done.' },
    { id: 'later', name: 'Planned work', status: 'planned' },
    { id: 'odd', name: 'Odd status', status: 'paused' },
    { id: 'bare' }],
  tasks: [...BOARD.tasks,
    { id: 'T-301', title: 'Closed one', status: 'done', priority: 'low', epic: 'closed', phase: 'P1', depends_on: [] },
    { id: 'T-302', title: 'Closed two', status: 'archived', priority: 'low', epic: 'closed', phase: 'P1', depends_on: [] }],
};

test.beforeEach(async ({ page }) => { await page.emulateMedia({ reducedMotion: 'reduce' }); });
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

async function boot(page, { theme = 'dark', width = 1440, height = 900, board = EPICS_BOARD } = {}) {
  await page.setViewportSize({ width, height });
  await mockApi(page, {
    '/api/viewer/prefs': { theme, ui: {}, screens: {} },
    '/api/board': board, '/api/backlog': board, '/api/bugs': [],
    '/api/epic/viewer': epicPayload(board, 'viewer'),
  });
  await page.goto('/#/epics');
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await expect(page.locator('.epic-row').first()).toBeVisible();
}
const row = (page, id) => page.locator(`.epic-row[data-epic-id="${id}"]`);
// The colour `var(--cat-N)` paints in the page's theme.
const catColour = (page, n) => page.evaluate((n) => {
  const probe = document.createElement('span');
  probe.style.background = `var(--cat-${n})`;
  document.body.append(probe);
  const colour = getComputedStyle(probe).backgroundColor;
  probe.remove();
  return colour;
}, n);

test('each epic is one link row with its lifecycle word, a bar and closed/total', async ({ page }) => {
  await boot(page);
  await expect(page.locator('.epic-row')).toHaveCount(EPICS_BOARD.epics.length);
  await expect(row(page, 'viewer').locator('a.link-row__link')).toHaveAttribute('href', '#/epic/viewer');
  await expect(row(page, 'viewer').getByRole('link')).toHaveAccessibleName('Viewer re-skin');
  await expect(row(page, 'bare').getByRole('link')).toHaveAccessibleName('bare');
  const word = (id) => row(page, id).locator('.epic-row__status .marker__word');
  await expect(word('viewer')).toHaveText('Active');
  await expect(word('later')).toHaveText('Planned');
  await expect(word('odd')).toHaveText('paused');
  await expect(word('bare')).toHaveText('Active');
  await expect(page.getByText('Exploring')).toHaveCount(0);
  await expect(row(page, 'viewer').locator('.epic-row__count')).toHaveText('1/4');
  await expect(row(page, 'viewer').locator('.epic-row__count')).toHaveAttribute('title', '1/4 closed · 1 done');
  await expect(row(page, 'empty').locator('.epic-row__count')).toHaveText('0/0');
  await expect(row(page, 'empty').locator('.epic-row__bar')).toBeVisible();
  await expect(row(page, 'empty').locator('.epic-row__fill')).toHaveAttribute('style', 'width: 0%;');
  await expect(row(page, 'closed').locator('.epic-tag')).toHaveText('Closeable');
  await expect(row(page, 'empty').locator('.epic-tag')).toHaveCount(0);
  await expect(row(page, 'closed').getByRole('link')).toHaveAttribute('title', 'All closed\nDone when: Both tasks are done.');
});

test('on a real backlog\'s volume the one closeable epic says so, and no other row does', async ({ page }) => {
  await boot(page, { board: LONG_IDS_BOARD });
  expect(epicPayload(LONG_IDS_BOARD, LONG_CLOSEABLE_EPIC).closeable).toBe(true);
  await expect(row(page, LONG_CLOSEABLE_EPIC).locator('.epic-tag')).toHaveText('Closeable');
  await expect(row(page, LONG_CLOSEABLE_EPIC).locator('.epic-row__fill')).toHaveAttribute('style', 'width: 100%;');
  await expect(page.locator('.epic-tag')).toHaveCount(1);
});

test('the epic swatch is its categorical colour', async ({ page }) => {
  await boot(page, { board: LONG_IDS_BOARD });
  const cats = [];
  for (const n of [1, 2, 3, 4, 5, 6]) cats.push(await catColour(page, n));
  const rows = await page.locator('.epic-row').evaluateAll((els) => els.map((el) => {
    const name = el.querySelector('.epic-row__name');
    return {
      id: el.dataset.epicId,
      swatches: [...el.querySelectorAll('.epic-swatch')].map((s) => getComputedStyle(s).backgroundColor),
      first: name.firstElementChild?.classList.contains('epic-swatch') ?? false,
      name: name.textContent,
    };
  }));
  expect(rows).toEqual(LONG_IDS_BOARD.epics.map((ep) => ({
    id: ep.id, swatches: [cats[epicSwatch(ep.id, LONG_IDS_BOARD.epics) - 1]], first: true, name: ep.name,
  })));
  // Six swatches, then round again: the 7th epic is the 1st one's colour.
  expect(rows[6].swatches).toEqual(rows[0].swatches);
  expect(rows[6].swatches).toEqual([cats[0]]);
  // Decorative: the link's name is still the epic's name alone.
  await expect(row(page, 'epic-07').getByRole('link')).toHaveAccessibleName(LONG_IDS_BOARD.epics[6].name);
});

test('the root keeps no class or style, and rows carry no colour', async ({ page }) => {
  await boot(page);
  expect(await page.locator('#screen-mount').evaluate((el) => [el.className, el.getAttribute('style')])).toEqual(['screen-mount', null]);
  await expect(page.locator('.epic-row[style], .epic-row [style]:not(.epic-row__fill)')).toHaveCount(0);
  await page.evaluate(() => { location.hash = '#/table'; });
  await expect(page.locator('table.tbl')).toBeVisible();
  expect(await page.locator('#screen-mount').evaluate((el) => [el.className, el.getAttribute('style')])).toEqual(['screen-mount', null]);
});

test('the columns line up on every row', async ({ page }) => {
  await boot(page, { board: LONG_IDS_BOARD });
  for (const cls of ['epic-row__status', 'epic-row__bar', 'epic-row__count']) {
    const lefts = await page.locator(`.${cls}`).evaluateAll((els) => els.map((el) => Math.round(el.getBoundingClientRect().left)));
    expect(new Set(lefts).size, cls).toBe(1);
  }
});

test('search filters by name or id; no match says so and Clear search brings everything back', async ({ page }) => {
  await boot(page);
  const search = page.getByPlaceholder('Filter epics…');
  await search.fill('STORE');
  await expect(page.locator('.epic-row')).toHaveCount(1);
  await search.fill('zzz');
  await expect(page.locator('.tm-empty__headline')).toHaveText('No epic matches “zzz”.');
  // The state block's own button; the field's × (also "Clear search") is beside the input.
  await page.locator('.tm-empty').getByRole('button', { name: 'Clear search' }).click();
  await expect(page.locator('.epic-row')).toHaveCount(EPICS_BOARD.epics.length);
  await expect(search).toBeFocused();
});

test('no epics is a state block', async ({ page }) => {
  const none = { ...BOARD, epics: [], tasks: [] };
  await mockApi(page, { '/api/viewer/prefs': { theme: 'dark', ui: {}, screens: {} }, '/api/board': none, '/api/backlog': none, '/api/bugs': [] });
  await page.goto('/#/epics');
  await expect(page.locator('.tm-empty__headline')).toHaveText('No epics yet.');
});

test('a keyboard user opens an epic and comes back to it', async ({ page }) => {
  await boot(page);
  const first = row(page, 'viewer').getByRole('link');
  await first.focus();
  // Rows are one tab stop each: the next Tab is the next epic.
  await page.keyboard.press('Tab');
  await expect(row(page, 'store').getByRole('link')).toBeFocused();
  await page.keyboard.press('Shift+Tab');
  await expect(first).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(page.getByRole('dialog', { name: 'Viewer re-skin' })).toBeVisible();
  await page.keyboard.press('Escape');
  await expect(first).toBeFocused();
});

test('a redraw keeps focus on the same epic', async ({ page }) => {
  await boot(page);
  const link = row(page, 'store').getByRole('link');
  await link.focus();
  await page.evaluate(() => import('/js/store.js').then(({ store }) => {
    const next = structuredClone(store.getBacklog());
    next.epics.find((e) => e.id === 'store').name = 'Native store, renamed';
    next.revision = 'r-2';
    store.setBoard(next);
  }));
  await expect(link).toHaveAccessibleName('Native store, renamed');
  await expect(link).toBeFocused();
  // The focused epic goes away: the row now at its place takes the keyboard, never <body>.
  await page.evaluate(() => import('/js/store.js').then(({ store }) => {
    const next = structuredClone(store.getBacklog());
    next.epics = next.epics.filter((e) => e.id !== 'store');
    next.revision = 'r-3';
    store.setBoard(next);
  }));
  await expect(row(page, 'store')).toHaveCount(0);
  await expect(page.locator('.epic-row').nth(1).getByRole('link')).toBeFocused();
});

test('at 390 a row stacks and nothing scrolls sideways', async ({ page }) => {
  await boot(page, { width: 390, height: 844, board: LONG_IDS_BOARD });
  const look = await page.evaluate(() => ({
    pageX: document.scrollingElement.scrollWidth - innerWidth,
    spill: [...document.querySelectorAll('.epic-row')].filter((r) => {
      const b = r.querySelector('.epic-row__bar').getBoundingClientRect();
      return b.right > r.getBoundingClientRect().right + 0.5 || b.width < 40;
    }).length,
    lostWords: [...document.querySelectorAll('.epic-row__name .truncate')].filter((el) => el.title !== el.textContent).length,
  }));
  expect(look).toEqual({ pageX: 0, spill: 0, lostWords: 0 });
  const r = row(page, 'database-native-tracking');
  const name = await r.locator('.epic-row__name').boundingBox();
  const status = await r.locator('.epic-row__status').boundingBox();
  const bar = await r.locator('.epic-row__bar').boundingBox();
  expect(status.y).toBeGreaterThanOrEqual(name.y + name.height - 1);
  expect(bar.y).toBeGreaterThan(status.y);
});

for (const theme of ['dark', 'light']) for (const [w, h] of [[1440, 900], [390, 844]]) {
  test(`axe (${theme}, ${w}): Epics passes contrast, nesting and aria`, async ({ page }) => {
    await boot(page, { theme, width: w, height: h, board: LONG_IDS_BOARD });
    await page.evaluate(axeSource);
    const v = await page.evaluate(async () => {
      const aria = window.axe.getRules().map((r) => r.ruleId).filter((id) => id.startsWith('aria-'));
      return (await window.axe.run(document.getElementById('screen-mount'),
        { runOnly: { type: 'rule', values: ['color-contrast', 'nested-interactive', 'list', 'listitem', ...aria] }, resultTypes: ['violations'] })).violations;
    });
    expect(v.map((x) => `${x.id}: ${x.nodes.length}`)).toEqual([]);
  });
}
