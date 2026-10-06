// User intent: the Bugs list in a real browser — rows are links, every status a chip so fixed bugs can be found, archived
// bugs behind a toggle, a labelled Sort, "found in" opening the task, states in words, a full keyboard walk, nothing
// sideways or under 44px at 390, no axe violations in either theme, and nothing left behind when the screen is left.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { bugsMocks, LIST_BUGS, LONG_BUGS, DETAIL_TASK } from './mock-fixtures.js';

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

async function boot(page, { theme = 'dark', width = 1440, height = 900, wait = true, ...overrides } = {}) {
  await page.setViewportSize({ width, height });
  await mockApi(page, { ...bugsMocks({ theme }), ...overrides });
  const puts = [];
  page.on('request', (r) => {
    if (r.method() === 'PUT' && new URL(r.url()).pathname === '/api/viewer/prefs') puts.push(JSON.parse(r.postData() || '{}'));
  });
  await page.goto('/#/bugs');
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  if (wait) await expect(page.locator('.bugs__list .bug-row').first()).toBeVisible();
  return puts;
}

const rowIds = (page) => page.locator('.bugs__list .bug-row').evaluateAll((rows) => rows.map((r) => r.dataset.bugId));
const statusChip = (page, name) => page.getByRole('group', { name: 'Status' }).getByRole('button', { name: new RegExp(`^${name}`) });
const lastBugsPrefs = (puts) => puts.map((b) => b.screens?.bugs).filter(Boolean).at(-1);
const row = (page, id) => page.locator(`.bug-row[data-bug-id="${id}"]`);

test('rows are links and the default shows open and shelved bugs', async ({ page }) => {
  await boot(page);
  expect(await rowIds(page)).toEqual(['B-031', 'B-030', 'B-028']);
  const link = page.getByRole('link', { name: /B-031/ });
  await expect(link).toHaveAttribute('href', '#/bug/B-031');
  await expect(page.locator('#topbar-count')).toHaveText('5 bugs · 3 visible');
  await link.focus();
  await page.keyboard.press('Enter');
  await expect.poll(() => page.evaluate(() => location.hash)).toBe('#/bug/B-031');
});

test('every status is a chip and a fixed bug can be found', async ({ page }) => {
  const puts = await boot(page);
  await statusChip(page, 'Fixed').click();
  await expect.poll(() => rowIds(page)).toEqual(['B-029']);
  await expect.poll(() => lastBugsPrefs(puts)?.statuses).toEqual(['fixed']);
  expect(lastBugsPrefs(puts)).not.toHaveProperty('filters');
  await statusChip(page, 'Adopted').click({ modifiers: ['Shift'] });
  await expect.poll(() => rowIds(page)).toEqual(['B-029', 'B-027']);
});

test('Show archived brings archived bugs in, marked', async ({ page }) => {
  await boot(page);
  const toggle = page.getByRole('button', { name: /^Show archived/ });
  await toggle.click();
  await expect(toggle).toHaveAttribute('aria-pressed', 'true');
  // Archived bugs answer to the status chips like any other: B-026 is a fixed bug, so Fixed shows it.
  await statusChip(page, 'Fixed').click();
  await expect.poll(() => rowIds(page)).toEqual(['B-029', 'B-026']);
  await expect(row(page, 'B-026').locator('.list-tag')).toHaveText('Archived');
  await expect(row(page, 'B-026')).toHaveClass(/bug-row--archived/);
  await expect(page.locator('#topbar-count')).toHaveText('6 bugs · 2 visible');
});

test('a bug whose status is archived is reachable: Show archived brings it in with the default chips', async ({ page }) => {
  await boot(page, { '/api/bugs': [...LIST_BUGS, { id: 'B-040', title: 'Retired bug', status: 'archived', discovered: '2026-01-01' }] });
  await expect(row(page, 'B-040')).toHaveCount(0);
  await page.getByRole('button', { name: /^Show archived/ }).click();
  await expect(row(page, 'B-040')).toBeVisible();
  await expect(row(page, 'B-040')).toHaveClass(/bug-row--archived/);
});

test('a severity is a marker and an unset one is nothing', async ({ page }) => {
  await boot(page);
  await expect(row(page, 'B-031').locator('.bug-row__severity .marker__word')).toHaveText('High');
  await expect(row(page, 'B-030').locator('.marker')).toHaveCount(1);
  await expect(row(page, 'B-030').locator('.marker__word')).toHaveText('Open');
});

test('Sort is a labelled select', async ({ page }) => {
  const puts = await boot(page);
  const sort = page.getByLabel('Sort');
  expect(await sort.evaluate((el) => el.id)).toBe('bugs-sort');
  await sort.selectOption({ label: 'Severity' });
  await expect.poll(() => rowIds(page)).toEqual(['B-031', 'B-028', 'B-030']);
  await expect.poll(() => lastBugsPrefs(puts)?.sort).toBe('severity');
});

test('found in opens the task, never the bug', async ({ page }) => {
  await boot(page);
  await row(page, 'B-031').getByRole('link', { name: 'found in T-102' }).click();
  await expect(page.getByRole('dialog', { name: DETAIL_TASK.title })).toBeVisible();
  expect(await page.evaluate(() => location.hash)).not.toBe('#/bug/B-031');
});

test('keyboard walk: search, Sort, the Status chips, Show archived, Clear, then the rows and their found-in links', async ({ page }) => {
  await boot(page);
  await page.getByRole('textbox', { name: 'Search bugs' }).focus();
  await expect(page.locator('.tm-search__clear')).toBeHidden();
  const names = [];
  for (let i = 0; i < 10; i++) {
    await page.keyboard.press('Tab');
    names.push(await page.evaluate(() => {
      const el = document.activeElement;
      if (el.labels?.length) return el.labels[0].textContent.trim();
      if (el.matches('.chip')) return el.querySelector('.chip__label').textContent;
      if (el.matches('.bug-row > .link-row__link')) return `row ${el.closest('.bug-row').dataset.bugId}`;
      return el.getAttribute('aria-label') || el.textContent.trim();
    }));
  }
  expect(names).toEqual(['Sort', 'Open', 'Fixed', 'Adopted', 'Shelved', 'Show archived', 'Clear filters',
    'row B-031', 'found in T-102', 'row B-030']);
});

test('a failed load is said in words and can be tried again', async ({ page }) => {
  let calls = 0;
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockApi(page, bugsMocks());
  // Registered after mockApi, so it answers first: the first call fails, the second has the list.
  await page.route((url) => url.pathname === '/api/bugs', (route) => (++calls === 1
    ? route.fulfill({ status: 500, json: { ok: false, error: 'sqlite3.OperationalError: database is locked' } })
    : route.fulfill({ json: LIST_BUGS })));
  await page.goto('/#/bugs');
  await expect(page.getByText('Could not load bugs.')).toBeVisible();
  const text = (await page.locator('#screen-mount').innerText()) + (await page.locator('#topbar').innerText());
  for (const bad of ['500', '/api', 'sqlite3', '{']) expect(text).not.toContain(bad);
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.locator('.bugs__list .bug-row')).toHaveCount(3);
  expect(calls).toBe(2);
});

test('no match offers to clear', async ({ page }) => {
  await boot(page);
  const search = page.getByRole('textbox', { name: 'Search bugs' });
  await search.fill('zzz');
  await expect(page.getByText('No bugs match these filters.')).toBeVisible();
  await page.locator('.bugs__state').getByRole('button', { name: 'Clear filters' }).click();
  await expect.poll(() => rowIds(page)).toEqual(['B-031', 'B-030', 'B-029', 'B-028', 'B-027']);
  await expect(search).toHaveValue('');
  await expect(page.locator('#topbar-count')).toHaveText('5 bugs');
  await expect(page.locator('.list-filters__clear')).toBeHidden();
});

test('a search typed just before leaving never writes into the next screen', async ({ page }) => {
  await boot(page);
  // Typed and left inside the search's debounce: the late search must find the screen gone.
  await page.evaluate(() => {
    const input = document.querySelector('#topbar-actions .tm-search input');
    input.value = 'card';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    location.hash = '#/kanban';
  });
  await expect(page.locator('#page-title')).toHaveText('Kanban');
  await page.evaluate(() => new Promise((r) => setTimeout(r, 400)));
  await expect(page.locator('#topbar-count')).not.toContainText('bug');
  await expect(page.locator('.bugs')).toHaveCount(0);
});

test('leaving Bugs with More open leaves nothing behind', async ({ page }) => {
  await boot(page, { width: 390, height: 844, '/api/bugs': LONG_BUGS });
  const more = page.locator('.bugs .chip-row .overflow-more');
  await expect(more).toBeVisible();
  await more.click();
  await expect(page.locator('.popover')).toHaveCount(1);
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('.bugs')).toHaveCount(0);
  await expect(page.locator('.popover')).toHaveCount(0);
});

test('at 390 with 23 long bugs nothing scrolls sideways, every target is 44px and no id wraps', async ({ page }) => {
  await boot(page, { width: 390, height: 844, '/api/bugs': LONG_BUGS });
  await page.evaluate(() => document.fonts.ready);
  // Open and Shelved are pressed by default, so Clear shows: measured, then pressed so all 23 rows are measured.
  const clear = page.locator('.list-filters__clear');
  await expect(clear).toBeVisible();
  expect(await clear.evaluate((el) => el.getBoundingClientRect().height)).toBeGreaterThanOrEqual(44);
  await clear.click();
  await expect(page.locator('.bug-row')).toHaveCount(23);
  const m = await page.evaluate(() => {
    const mount = document.getElementById('screen-mount');
    const tall = (sel) => [...document.querySelectorAll(sel)].filter((el) => el.getClientRects().length)
      .map((el) => ({ sel, h: el.getBoundingClientRect().height, text: el.textContent.trim().slice(0, 30) }));
    const ids = [...document.querySelectorAll('.bug-row__id')].map((el) => {
      const cs = getComputedStyle(el);
      return { h: el.getBoundingClientRect().height, lh: parseFloat(cs.lineHeight) || parseFloat(cs.fontSize) * 1.5 };
    });
    return {
      doc: document.documentElement.scrollWidth, inner: innerWidth, mountScroll: mount.scrollWidth, mountClient: mount.clientWidth,
      targets: [...tall('.bugs .chip'), ...tall('.bugs .overflow-more'), ...tall('.list-filters__clear:not([hidden])'),
        ...tall('.bug-row > .link-row__link'), ...tall('.bug-row__found-in')],
      ids,
      height: Math.max(document.documentElement.scrollHeight, mount.scrollHeight),
    };
  });
  // Bounded height: 23 long bugs stay a list a thumb can cross, never a wall.
  expect(m.height).toBeLessThanOrEqual(8000);
  expect(m.doc).toBeLessThanOrEqual(m.inner);
  expect(m.mountScroll).toBeLessThanOrEqual(m.mountClient);
  expect(m.targets.length).toBeGreaterThan(23 * 2);
  expect(m.targets.filter((t) => t.h < 44)).toEqual([]);
  expect(m.ids.filter((x) => x.h > x.lh + 1)).toEqual([]);

  // Sort is in row 2 or, when row 2 is too narrow for it, behind Filters.
  if (!(await page.locator('#topbar-actions #bugs-sort').count())) {
    await page.locator('#topbar-actions > .overflow-more').click();
    await expect(page.getByRole('dialog', { name: 'Filters' })).toBeVisible();
  }
  const sortHeight = await page.locator('#bugs-sort').evaluate((el) => el.getBoundingClientRect().height);
  expect(sortHeight).toBeGreaterThanOrEqual(44);
});

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): no contrast, nested-interactive, label or aria violation on the list and the topbar`, async ({ page }) => {
    await boot(page, { theme });
    await page.getByRole('button', { name: /^Show archived/ }).click();
    await expect(row(page, 'B-026')).toHaveCount(0);   // fixed and archived: shown only once Fixed is pressed
    await statusChip(page, 'Fixed').click({ modifiers: ['Shift'] });
    await expect(row(page, 'B-026')).toBeVisible();
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(axeSource);
    const result = await page.evaluate(async () => {
      const aria = window.axe.getRules().map((r) => r.ruleId).filter((id) => id.startsWith('aria-'));
      const values = ['color-contrast', 'nested-interactive', 'label', 'select-name', ...aria];
      const opts = { runOnly: { type: 'rule', values }, resultTypes: ['violations'] };
      const out = [];
      for (const id of ['screen-mount', 'topbar']) out.push(...(await window.axe.run(document.getElementById(id), opts)).violations);
      return out;
    });
    expect(result.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}
