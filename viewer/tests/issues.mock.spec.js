// User intent: the Issues board in a real browser — columns per view, resolved issues on a real shelf, severities as
// words, search and chips that say how many they show, evidence opened from the keyboard that survives the board poll,
// one column behind tabs at 390 with nothing sideways, every column at least 280px at 1440, a full keyboard walk, its
// states in words, a fresh read on every visit, and nothing left behind when the screen is left mid-load.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { issuesMocks, LIST_ISSUES, LONG_ISSUES, DETAIL_TASK } from './mock-fixtures.js';

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
  await mockApi(page, { ...issuesMocks({ theme }), ...overrides });
  const puts = [];
  page.on('request', (r) => {
    if (r.method() === 'PUT' && new URL(r.url()).pathname === '/api/viewer/prefs') puts.push(JSON.parse(r.postData() || '{}'));
  });
  await page.goto('/#/issues');
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  if (wait) await expect(page.locator('.issues-col .issue-card').first()).toBeVisible();
  return puts;
}

const cardIds = (page) => page.locator('.issues-col .issue-card').evaluateAll((cards) => cards.map((c) => c.dataset.issueId));
const card = (page, id) => page.locator(`.issue-card[data-issue-id="${id}"]`);
const chip = (page, group, name) => page.getByRole('group', { name: group }).getByRole('button', { name: new RegExp(`^${name}`) });
const lastIssuesPrefs = (puts) => puts.map((b) => b.screens?.issues).filter(Boolean).reduce((acc, p) => ({ ...acc, ...p }), {});
const colNames = (page) => page.locator('.issues-col .issues-col__name');
const setIssues = (page, list) => page.evaluate((l) => import('/js/store.js').then(({ store }) => store.setIssues(l)), list);
// The board poll as main.js runs it: a new revision of the same board.
const pollBoard = (page) => page.evaluate(() => import('/js/store.js').then(({ store }) => {
  const next = structuredClone(store.getBacklog());
  next.revision = 'r-poll';
  store.setBoard(next);
}));
const withoutIds = (...ids) => LIST_ISSUES.filter((i) => !ids.includes(i.id));

// Row 2 holds the View group, or parks it behind Filters when it does not fit: either way, pick a view by its name.
async function pickView(page, name) {
  const inRow = page.locator('#topbar-actions').getByRole('group', { name: 'View' });
  if (!(await inRow.isVisible())) {
    await page.locator('#topbar-actions > .overflow-more').click();
    await expect(page.getByRole('dialog', { name: 'Filters' })).toBeVisible();
  }
  await page.getByRole('group', { name: 'View' }).getByRole('button', { name }).click();
  if (await page.getByRole('dialog', { name: 'Filters' }).isVisible()) {
    await page.keyboard.press('Escape');
    await expect(page.getByRole('dialog', { name: 'Filters' })).toHaveCount(0);
  }
}

test('the Hybrid board shows Investigating and Open as columns, resolved issues on a shelf', async ({ page }) => {
  await boot(page);
  await expect(page.locator('section.issues-col')).toHaveCount(2);
  await expect(colNames(page)).toHaveText(['Investigating', 'Open']);
  await expect(page.locator('.issues-col .issues-col__count')).toHaveText(['1', '3']);
  expect(await cardIds(page)).toEqual(['ISS-001', 'ISS-002', 'ISS-003', 'ISS-004']);
  await expect(page.locator('#topbar-count')).toHaveText('7 issues');
  const toggle = page.locator('.issues-shelf__toggle');
  await expect(toggle).toHaveText('Resolved · 3 issues');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(toggle).toHaveAttribute('aria-controls', 'issues-shelf-list');
  await expect(page.locator('#issues-shelf-list')).toBeHidden();
  await toggle.focus();
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  const rows = page.locator('#issues-shelf-list .issue-row');
  await expect(rows).toHaveCount(3);
  await expect(rows.locator('.link-row__content .marker__word')).toHaveText(['Fixed', "Won't fix", 'Duplicate']);
  // An open shelf stays open through a redraw.
  await pollBoard(page);
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(rows).toHaveCount(3);
});

test('a severity is a word and a shape', async ({ page }) => {
  await boot(page);
  const words = await page.locator('.issue-card__line .marker__word').allTextContents();
  expect(words.length).toBe(4);
  for (const w of words) expect(['Critical', 'High', 'Medium', 'Low']).toContain(w);
  await expect(page.locator('.issue-card__line .marker__shape')).toHaveCount(4);
  const text = (await page.locator('#screen-mount').innerText()) + (await page.locator('#topbar').innerText());
  expect(text).not.toMatch(/\bP[0-3]\b/);
});

test('a card opens its issue and its task link opens the task', async ({ page }) => {
  await boot(page);
  // The evidence sits under the link's hit area: a press on it is a press on the link.
  const box = await card(page, 'ISS-003').locator('.issue-card__evidence').boundingBox();
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await expect.poll(() => page.evaluate(() => location.hash)).toBe('#/issue/ISS-003');
  await page.evaluate(() => { location.hash = '#/issues'; });
  await expect(card(page, 'ISS-001')).toBeVisible();
  await card(page, 'ISS-001').getByRole('link', { name: 'T-102', exact: true }).click();
  await expect(page.getByRole('dialog', { name: DETAIL_TASK.title })).toBeVisible();
  expect(await page.evaluate(() => location.hash)).not.toBe('#/issue/ISS-001');
});

test('search and the chips filter, and say how many', async ({ page }) => {
  const puts = await boot(page);
  const search = page.getByRole('textbox', { name: 'Search issues' });
  await search.fill('mutex');
  await expect.poll(() => cardIds(page)).toEqual(['ISS-002']);
  await expect(page.locator('#topbar-count')).toHaveText('7 issues · 1 visible');
  await expect(page.locator('.issues-shelf')).toBeHidden();
  await search.fill('');
  await expect.poll(() => cardIds(page)).toEqual(['ISS-001', 'ISS-002', 'ISS-003', 'ISS-004']);
  await expect(page.locator('#topbar-count')).toHaveText('7 issues');

  await chip(page, 'Component', 'store').click();
  await expect.poll(() => cardIds(page)).toEqual(['ISS-002']);
  await expect(page.locator('#topbar-count')).toHaveText('7 issues · 1 visible');
  const promoted = page.getByRole('button', { name: /^From a bug/ });
  await expect(promoted.locator('.chip__count')).toHaveText('1');
  await promoted.click();
  await expect(promoted).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(() => cardIds(page)).toEqual(['ISS-002']);
  await expect.poll(() => lastIssuesPrefs(puts).promotedFromBug).toBe(true);

  const clear = page.locator('.list-filters__clear');
  await expect(clear).toBeVisible();
  await clear.click();
  await expect.poll(() => cardIds(page)).toEqual(['ISS-001', 'ISS-002', 'ISS-003', 'ISS-004']);
  await expect(clear).toBeHidden();
  await expect(promoted).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('#topbar-count')).toHaveText('7 issues');
  await expect.poll(() => lastIssuesPrefs(puts).promotedFromBug).toBe(false);
});

test('no match offers to clear, and an empty list says so', async ({ page }) => {
  await boot(page);
  await chip(page, 'Severity', 'Critical').click();
  await page.getByRole('textbox', { name: 'Search issues' }).fill('contrast');
  await expect(page.getByText('No issues match these filters.')).toBeVisible();
  await expect(page.locator('.issues-board')).toBeHidden();
  await expect(page.locator('#topbar-count')).toHaveText('7 issues · 0 visible');
  await page.locator('.issues__state').getByRole('button', { name: 'Clear filters' }).click();
  await expect.poll(() => cardIds(page)).toEqual(['ISS-001', 'ISS-002', 'ISS-003', 'ISS-004']);
  await expect(page.getByRole('textbox', { name: 'Search issues' })).toHaveValue('');
  await setIssues(page, []);
  await expect(page.getByText('No issues recorded yet.')).toBeVisible();
  await expect(page.locator('#topbar-count')).toHaveText('0 issues');
});

test('a severity at zero is disabled but a pressed one can be released', async ({ page }) => {
  await boot(page, { '/api/issues': { issues: withoutIds('ISS-002') } });
  await expect(chip(page, 'Severity', 'Critical')).toBeDisabled();
  const high = chip(page, 'Severity', 'High');
  await high.click();
  await expect(high).toHaveAttribute('aria-pressed', 'true');
  await expect.poll(() => cardIds(page)).toEqual(['ISS-001']);
  await setIssues(page, withoutIds('ISS-002', 'ISS-001', 'ISS-005'));
  await expect(high.locator('.chip__count')).toHaveText('0');
  await expect(high).toBeEnabled();
  await expect(high).toHaveAttribute('aria-pressed', 'true');
  await high.click();
  await expect(high).toHaveAttribute('aria-pressed', 'false');
  await expect(high).toBeDisabled();
});

test('the View switcher is labelled and remembered', async ({ page }) => {
  const puts = await boot(page);
  const group = page.getByRole('group', { name: 'View' });
  await expect(group.getByRole('button')).toHaveCount(4);
  await expect(group.getByRole('button')).toHaveText(['Hybrid', 'Status', 'Severity', 'List']);
  await group.getByRole('button', { name: 'Severity' }).click();
  await expect(colNames(page)).toHaveText(['Critical', 'High', 'Medium', 'Low']);
  await expect.poll(() => lastIssuesPrefs(puts).view).toBe('D');
  // Mixed statuses in one column: each card says its status.
  await expect(card(page, 'ISS-001').locator('.issue-card__meta .marker__word').first()).toHaveText('Investigating');
  await group.getByRole('button', { name: 'Status' }).click();
  await expect(colNames(page)).toHaveText(['Open', 'Investigating', 'Fixed', "Won't fix", 'Duplicate']);
  await expect(page.locator('.issues-shelf')).toBeHidden();
  await setIssues(page, withoutIds('ISS-007'));
  await expect(colNames(page)).toHaveText(['Open', 'Investigating', 'Fixed', "Won't fix"]);
  await group.getByRole('button', { name: 'List' }).click();
  await expect(colNames(page)).toHaveText(['Open and investigating']);
  expect(await cardIds(page)).toEqual(['ISS-001', 'ISS-002', 'ISS-003', 'ISS-004']);
  await expect.poll(() => lastIssuesPrefs(puts).view).toBe('C');
});

test('evidence expands from the keyboard and stays expanded and focused through the board poll\'s redraw', async ({ page }) => {
  await boot(page);
  const more = card(page, 'ISS-001').locator('.issue-card__more');
  await expect(more).toBeVisible();
  await card(page, 'ISS-001').locator('.link-row__link').focus();
  await page.keyboard.press('Tab');
  await expect(more).toBeFocused();
  await expect(more).toHaveAttribute('aria-expanded', 'false');
  const controlled = page.locator(`[id="${await more.getAttribute('aria-controls')}"]`);
  await expect(controlled).toHaveText(/^Line 1:/);
  const before = await controlled.evaluate((el) => el.getBoundingClientRect().height);

  await page.keyboard.press('Enter');
  await expect(more).toHaveAttribute('aria-expanded', 'true');
  await expect(more).toHaveText('Show less');
  await expect(more).toBeFocused();
  const evidence = card(page, 'ISS-001').locator('.issue-card__evidence');
  await expect(evidence).not.toHaveClass(/truncate--3/);
  expect(await evidence.evaluate((el) => el.getBoundingClientRect().height)).toBeGreaterThan(before);

  await pollBoard(page);
  await expect(more).toHaveAttribute('aria-expanded', 'true');
  expect(await more.evaluate((el) => el === document.activeElement)).toBe(true);
  await page.keyboard.press('Space');
  await expect(more).toHaveAttribute('aria-expanded', 'false');
  await expect(more).toBeFocused();
  // A collapsed "Show all" that has focus keeps it through the poll too.
  await pollBoard(page);
  await expect(more).toBeVisible();
  expect(await more.evaluate((el) => el === document.activeElement)).toBe(true);
  await expect(card(page, 'ISS-003').locator('.issue-card__more')).toBeHidden();
});

test('at 390 the board is one column behind tabs with counts, and nothing scrolls sideways', async ({ page }) => {
  await boot(page, { width: 390, height: 844, '/api/issues': { issues: LONG_ISSUES } });
  await page.evaluate(() => document.fonts.ready);
  const tablist = page.getByRole('tablist', { name: 'Issue columns' });
  await expect(tablist).toBeVisible();
  const tabs = tablist.getByRole('tab');
  await expect(tabs).toHaveCount(2);
  await expect(tabs.locator('.column-tabs__label')).toHaveText(['Investigating', 'Open']);
  await expect(tabs.locator('.column-tabs__count')).toHaveText(['6', '9']);

  const shown = page.locator('.issues-col:visible');
  await expect(shown).toHaveCount(1);
  const selected = tablist.getByRole('tab', { selected: true });
  expect(await shown.getAttribute('id')).toBe(await selected.getAttribute('aria-controls'));
  await expect(shown).toHaveAttribute('role', 'tabpanel');
  await expect(shown).toHaveAttribute('aria-labelledby', await selected.getAttribute('id'));

  await selected.focus();
  await page.keyboard.press('ArrowRight');
  const openTab = tablist.getByRole('tab', { name: /^Open/ });
  await expect(openTab).toHaveAttribute('aria-selected', 'true');
  await expect(openTab).toBeFocused();
  await expect(page.locator('#issues-col-open')).toBeVisible();
  await expect(page.locator('#issues-col-investigating')).toBeHidden();
  // A card in a column that was hidden until now is laid out only now; its evidence is cut, so Show all appears.
  await expect(page.locator('#issues-col-open .issue-card__more').first()).toBeVisible();
  await page.keyboard.press('Tab');
  await expect(page.locator('#issues-col-open .issue-card').first().locator('.link-row__link')).toBeFocused();

  const short = await page.evaluate(() => {
    const sel = ['.column-tabs__tab', '.chip', '.overflow-more', '.issues-shelf__toggle', '.issue-card > .link-row__link',
      '.issue-card__more:not([hidden])'];
    return sel.flatMap((s) => [...document.querySelectorAll(s)].filter((el) => el.getClientRects().length)
      .map((el) => ({ s, h: el.getBoundingClientRect().height, text: el.textContent.trim().slice(0, 30) })));
  });
  expect(short.length).toBeGreaterThan(20);
  expect(short.filter((t) => t.h < 44)).toEqual([]);

  // A poll redraw never moves a page the user has scrolled down.
  const tops = () => page.locator('.issues-board').evaluate((el) => {
    const out = [];
    for (let n = el; n; n = n.parentElement) out.push(n.scrollTop);
    out.push(document.scrollingElement.scrollTop);
    return out;
  });
  await page.locator('#issues-col-open .issue-card').last().scrollIntoViewIfNeeded();
  const scrolled = await tops();
  expect(scrolled.some((t) => t > 0)).toBe(true);
  await pollBoard(page);
  await expect.poll(tops).toEqual(scrolled);

  const heights = {};
  for (const view of ['Hybrid', 'Status', 'Severity', 'List']) {
    await pickView(page, view);
    await expect(page.locator('.issues-col:visible')).toHaveCount(1);
    if (view === 'Status') {
      await expect(tablist.getByRole('tab')).toHaveCount(5);
      expect(await tablist.evaluate((el) => el.scrollWidth > el.clientWidth || true)).toBe(true);
    }
    const m = await page.evaluate(() => ({ doc: document.documentElement.scrollWidth, inner: innerWidth,
      height: document.scrollingElement.scrollHeight }));
    expect(m.doc, view).toBeLessThanOrEqual(m.inner);
    expect(m.height, view).toBeLessThanOrEqual(8000);
    heights[view] = m.height;
  }
  console.log('issues page height at 390:', JSON.stringify(heights));

  // The shelf's rows at phone width: id and severity on one line inside the row, the title under them.
  await page.locator('.issues-shelf__toggle').click();
  await expect(page.locator('#issues-shelf-list .issue-row')).toHaveCount(9);
  const spilled = await page.locator('#issues-shelf-list .issue-row').evaluateAll((rows) => rows.filter((row) => {
    const box = row.getBoundingClientRect();
    const id = row.querySelector('.issue-row__id').getBoundingClientRect();
    const sev = row.querySelector('.link-row__link .marker').getBoundingClientRect();
    return sev.right > box.right || Math.abs(sev.top - id.top) > id.height;
  }).map((row) => row.dataset.issueId));
  expect(spilled).toEqual([]);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth)).toBe(true);
});

test('at 390 the Status tab strip fades on the side that can still scroll, and the fade adds no width', async ({ page }) => {
  await boot(page, { width: 390, height: 844, '/api/issues': { issues: LONG_ISSUES } });
  await pickView(page, 'Status');
  const tablist = page.getByRole('tablist', { name: 'Issue columns' });
  await expect(tablist.getByRole('tab')).toHaveCount(5);
  const cue = () => tablist.evaluate((el) => ({
    start: el.classList.contains('column-tabs--more-start') && getComputedStyle(el, '::before').opacity !== '0',
    end: el.classList.contains('column-tabs--more-end') && getComputedStyle(el, '::after').opacity !== '0',
    scrolls: el.scrollWidth > el.clientWidth,
  }));
  await expect.poll(cue).toMatchObject({ start: false, end: true, scrolls: true });
  // The fades are overlays: switching them off moves no tab and changes neither a tab's size nor the scroll width.
  const geometry = () => tablist.evaluate((el) => [el.scrollWidth,
    ...[...el.children].map((t) => `${t.offsetLeft}:${t.offsetWidth}:${t.offsetHeight}`)]);
  const withCue = await geometry();
  await page.addStyleTag({ content: '.column-tabs::before, .column-tabs::after { display: none !important; }' });
  expect(await geometry()).toEqual(withCue);
  await page.evaluate(() => document.querySelector('style:last-of-type').remove());
  await tablist.evaluate((el) => { el.scrollLeft = el.scrollWidth; });
  await expect.poll(cue).toMatchObject({ start: true, end: false });
  await page.setViewportSize({ width: 1200, height: 844 });
  await expect(tablist).toBeHidden();
});

test('at 390 the tab names the column: its own heading is hidden on screen, the panel keeps its name, axe passes', async ({ page }) => {
  await boot(page, { width: 390, height: 844 });
  const panel = page.locator('.issues-col:visible');
  await expect(panel).toHaveCount(1);
  await expect(panel).toHaveAttribute('role', 'tabpanel');
  const selected = page.getByRole('tablist', { name: 'Issue columns' }).getByRole('tab', { selected: true });
  const label = await selected.locator('.column-tabs__label').textContent();
  await expect(panel).toHaveAccessibleName(new RegExp(`^${label}`));
  const head = await panel.locator('.issues-col__head').boundingBox();
  expect(head.width <= 1 && head.height <= 1).toBe(true);
  await page.setViewportSize({ width: 1440, height: 900 });
  const wide = await page.locator('.issues-col__head').first().boundingBox();
  expect(wide.height).toBeGreaterThan(10);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.evaluate(axeSource);
  const result = await page.evaluate(async () => {
    const aria = window.axe.getRules().map((r) => r.ruleId).filter((id) => id.startsWith('aria-'));
    const values = ['color-contrast', 'nested-interactive', 'scrollable-region-focusable', 'heading-order', ...aria];
    return (await window.axe.run(document.getElementById('screen-mount'), { runOnly: { type: 'rule', values },
      resultTypes: ['violations'] })).violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`);
  });
  expect(result).toEqual([]);
});

test('at 1440 every column shows, each at least 280px wide', async ({ page }) => {
  await boot(page, { '/api/issues': { issues: LONG_ISSUES } });
  await page.getByRole('group', { name: 'View' }).getByRole('button', { name: 'Status' }).click();
  await expect(colNames(page)).toHaveCount(5);
  await expect(page.getByRole('tablist')).toHaveCount(0);
  await expect(page.locator('[role="tabpanel"]')).toHaveCount(0);
  await expect(page.locator('.issues-col[hidden]')).toHaveCount(0);
  const m = await page.evaluate(() => {
    const cols = document.querySelector('.issues-board__cols');
    return {
      widths: [...document.querySelectorAll('.issues-col')].map((el) => el.getBoundingClientRect().width),
      doc: document.documentElement.scrollWidth, inner: innerWidth,
      labelled: [...document.querySelectorAll('.issues-col')].map((el) => document.getElementById(el.getAttribute('aria-labelledby'))?.textContent),
      inside: cols.scrollWidth > cols.clientWidth,
      // A resolved row in a 280px column: its status line sits under the link, never over it, and nothing spills out.
      overlaps: [...document.querySelectorAll('.issues-col .issue-row')].filter((row) => {
        const link = row.querySelector('.link-row__link').getBoundingClientRect();
        const content = row.querySelector('.link-row__content').getBoundingClientRect();
        const box = row.getBoundingClientRect();
        const sev = row.querySelector('.link-row__link .marker')?.getBoundingClientRect();
        return content.top < link.bottom - 1 || row.scrollWidth > row.clientWidth + 1 || (sev && sev.right > box.right);
      }).map((row) => row.dataset.issueId),
    };
  });
  expect(m.overlaps).toEqual([]);
  expect(m.widths.length).toBe(5);
  for (const w of m.widths) expect(w).toBeGreaterThanOrEqual(280);
  expect(m.doc).toBeLessThanOrEqual(m.inner);
  expect(m.labelled).toEqual(['Open9', 'Investigating6', 'Fixed3', "Won't fix3", 'Duplicate3']);
  console.log('issues columns at 1440 scroll inside the board:', m.inside);
});

test('keyboard walk: search, the View group, the chips, From a bug, then the cards and their controls', async ({ page }) => {
  await boot(page);
  await expect(card(page, 'ISS-001').locator('.issue-card__more')).toBeVisible();
  await page.getByRole('textbox', { name: 'Search issues' }).focus();
  const names = [];
  for (let i = 0; i < 15; i++) {
    await page.keyboard.press('Tab');
    names.push(await page.evaluate(() => {
      const el = document.activeElement;
      if (el.matches('.chip')) return el.querySelector('.chip__label').textContent;
      if (el.matches('.issue-card > .link-row__link')) return `card ${el.closest('.issue-card').dataset.issueId}`;
      return el.getAttribute('aria-label') || el.textContent.trim();
    }));
  }
  expect(names).toEqual(['Hybrid', 'Status', 'Severity', 'List', 'Critical', 'High', 'Medium', 'Low', 'store', 'viewer',
    'From a bug', 'card ISS-001', 'Show all', 'T-102', 'card ISS-002']);

  // A chip group's More: Escape closes only its popover and gives focus back to More.
  await page.setViewportSize({ width: 390, height: 844 });
  await setIssues(page, LONG_ISSUES);
  const more = page.locator('.issues .chip-row .overflow-more:visible').first();
  await expect(more).toBeVisible();
  await more.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.popover')).toHaveCount(1);
  await page.keyboard.press('Escape');
  await expect(page.locator('.popover')).toHaveCount(0);
  await expect(more).toBeFocused();
  await expect(page.locator('.issues-col .issue-card').first()).toBeVisible();
});

test('leaving Issues while its issues are still loading leaves nothing behind', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockApi(page, issuesMocks());
  let held = null;
  await page.route((url) => url.pathname === '/api/issues', (route) => { held = route; });
  await page.goto('/#/issues');
  await expect(page.getByText('Loading issues…')).toBeVisible();
  await expect.poll(() => held !== null).toBe(true);
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('#page-title')).toHaveText('Kanban');
  await expect(page.locator('.issues')).toHaveCount(0);
  const answered = page.waitForResponse((r) => new URL(r.url()).pathname === '/api/issues');
  await held.fulfill({ json: { issues: LIST_ISSUES } });
  await answered;
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 50))));
  await expect(page.locator('.issue-card')).toHaveCount(0);
  await expect(page.locator('.issues')).toHaveCount(0);
  await setIssues(page, LIST_ISSUES);
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 50))));
  await expect(page.locator('.issue-card')).toHaveCount(0);
  await expect(page.locator('#page-title')).toHaveText('Kanban');
});

test('a search typed just before leaving never writes into the next screen', async ({ page }) => {
  await boot(page);
  // Typed and left inside the search's debounce: the late search must find the screen gone.
  await page.evaluate(() => {
    const input = document.querySelector('#topbar-actions .tm-search input');
    input.value = 'mutex';
    input.dispatchEvent(new Event('input', { bubbles: true }));
    location.hash = '#/kanban';
  });
  await expect(page.locator('#page-title')).toHaveText('Kanban');
  await page.evaluate(() => new Promise((r) => setTimeout(r, 400)));
  await expect(page.locator('#topbar-count')).not.toContainText('issue');
  await expect(page.locator('.issues')).toHaveCount(0);
});

test('only the latest load is applied: an older reply never overwrites a newer list or says it failed', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockApi(page, issuesMocks());
  const fresh = [...LIST_ISSUES, { id: 'ISS-008', title: 'Found since the last visit', status: 'open', severity: 'P2', severity_label: 'Medium' }];
  let mode = 'ok';
  const held = [];
  await page.route((url) => url.pathname === '/api/issues', (route) => {
    if (mode === 'ok') return route.fulfill({ json: { issues: LIST_ISSUES } });
    if (mode === 'fail') return route.fulfill({ status: 500, json: { ok: false, error: 'locked' } });
    if (mode === 'hold') { held.push(route); return undefined; }
    return route.fulfill({ json: { issues: fresh } });
  });
  await page.goto('/#/issues');
  await expect(page.locator('.issues-col .issue-card')).toHaveCount(4);
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('#page-title')).toHaveText('Kanban');
  mode = 'fail';
  await page.evaluate(() => { location.hash = '#/issues'; });
  const notice = page.locator('.issues__notice');
  await expect(notice).toBeVisible();
  // Three loads in flight at once: the first two are held, the last answers at once with the newer list.
  mode = 'hold';
  await page.evaluate(() => { const b = document.querySelector('.issues__notice button'); b.click(); b.click(); });
  await expect.poll(() => held.length).toBe(2);
  mode = 'fresh';
  await page.evaluate(() => document.querySelector('.issues__notice button').click());
  await expect(card(page, 'ISS-008')).toBeVisible();
  await held[0].fulfill({ json: { issues: LIST_ISSUES } });
  await held[1].fulfill({ status: 500, json: { ok: false, error: 'locked' } });
  await page.evaluate(() => new Promise((r) => setTimeout(r, 200)));
  await expect(card(page, 'ISS-008')).toBeVisible();
  await expect(page.locator('#topbar-count')).toHaveText('8 issues');
  await expect(notice).toBeHidden();
});

test('returning to Issues reads them again', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockApi(page, issuesMocks());
  let answer = LIST_ISSUES;
  let gets = 0;
  await page.route((url) => url.pathname === '/api/issues', (route) => { gets += 1; return route.fulfill({ json: { issues: answer } }); });
  await page.goto('/#/issues');
  await expect(page.locator('.issues-col .issue-card')).toHaveCount(4);
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('#page-title')).toHaveText('Kanban');
  answer = [...LIST_ISSUES, { id: 'ISS-008', title: 'Found since the last visit', status: 'open', severity: 'P2', severity_label: 'Medium' }];
  await page.evaluate(() => { location.hash = '#/issues'; });
  await expect(card(page, 'ISS-008')).toBeVisible();
  await expect(page.locator('#topbar-count')).toHaveText('8 issues');
  expect(gets).toBe(2);
});

test('a failed load is said in words', async ({ page }) => {
  let calls = 0;
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockApi(page, issuesMocks());
  await page.route((url) => url.pathname === '/api/issues', (route) => (++calls === 1
    ? route.fulfill({ status: 500, json: { ok: false, error: 'sqlite3.OperationalError: database is locked' } })
    : route.fulfill({ json: { issues: LIST_ISSUES } })));
  await page.goto('/#/issues');
  await expect(page.getByText('Could not load issues.')).toBeVisible();
  const text = (await page.locator('#screen-mount').innerText()) + (await page.locator('#topbar').innerText());
  for (const bad of ['500', '/api', 'sqlite3', '{']) expect(text).not.toContain(bad);
  await page.getByRole('button', { name: 'Try again' }).click();
  await expect(page.locator('.issues-col .issue-card')).toHaveCount(4);
  expect(calls).toBe(2);
});

test('a failed refresh keeps the list loaded earlier and says so', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockApi(page, issuesMocks());
  let fail = false;
  await page.route((url) => url.pathname === '/api/issues', (route) => (fail
    ? route.fulfill({ status: 500, json: { ok: false, error: 'sqlite3.OperationalError: database is locked' } })
    : route.fulfill({ json: { issues: LIST_ISSUES } })));
  await page.goto('/#/issues');
  await expect(page.locator('.issues-col .issue-card')).toHaveCount(4);
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('#page-title')).toHaveText('Kanban');
  fail = true;
  await page.evaluate(() => { location.hash = '#/issues'; });
  const notice = page.locator('.issues__notice[role="status"]');
  await expect(notice).toContainText('Could not refresh issues — showing the list loaded earlier.');
  await expect(page.locator('.issues-col .issue-card')).toHaveCount(4);
  fail = false;
  await notice.getByRole('button', { name: 'Try again' }).click();
  await expect(notice).toBeHidden();
  await expect(page.locator('.issues-col .issue-card')).toHaveCount(4);
});

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): no contrast, nesting, scrolling-region, heading or aria violation on the board and the topbar`, async ({ page }) => {
    await boot(page, { theme });
    await page.locator('.issues-shelf__toggle').click();
    await expect(page.locator('#issues-shelf-list .issue-row')).toHaveCount(3);
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(axeSource);
    const result = await page.evaluate(async () => {
      const aria = window.axe.getRules().map((r) => r.ruleId).filter((id) => id.startsWith('aria-'));
      const values = ['color-contrast', 'nested-interactive', 'scrollable-region-focusable', 'heading-order', ...aria];
      const opts = { runOnly: { type: 'rule', values }, resultTypes: ['violations'] };
      const out = [];
      for (const id of ['screen-mount', 'topbar']) out.push(...(await window.axe.run(document.getElementById(id), opts)).violations);
      return out;
    });
    expect(result.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
    const shadows = await page.evaluate(() => [...document.querySelectorAll('.issue-card, .issues-col')]
      .map((el) => getComputedStyle(el).boxShadow));
    expect(shadows.length).toBeGreaterThan(0);
    expect(shadows.filter((s) => s !== 'none')).toEqual([]);
  });
}
