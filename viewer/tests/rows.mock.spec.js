// User intent: in a real browser, a row that is a link behaves like a link — a plain click opens the task's dialog,
// a modified click is the browser's (new tab, no dialog), the row's own button only does its own job, the keyboard
// reaches link then control with the ring drawn on the row — and a sortable header is a button that sorts.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { BOARD, DETAIL_TASK, RICH_RELATED, taskDetail } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
});
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

// Boots the Kanban (so the detail interceptor is installed), then mounts above it a focusable "before" button, one
// link row and a table whose header is built from sortHeader. onSort records the sort and re-renders the header.
async function boot(page, { theme = 'dark' } = {}) {
  await mockApi(page, {
    '/api/viewer/prefs': { theme, ui: {}, screens: {} },
    '/api/board': BOARD, '/api/backlog': BOARD, '/api/bugs': [],
    '/api/task/T-102/detail': taskDetail(DETAIL_TASK, 't1:fixture', RICH_RELATED),
  });
  await page.goto('/#/kanban');
  await expect(page.locator('.card-task[data-task-id="T-102"]')).toBeVisible();
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  await page.evaluate(async () => {
    const { linkRow } = await import('/js/components/link-row.js');
    const { sortHeader } = await import('/js/components/sort-header.js');
    const host = document.createElement('div');
    host.id = 'rows-host';
    const before = document.createElement('button');
    before.type = 'button';
    before.id = 'before';
    before.textContent = 'Before';
    const status = document.createElement('span');
    status.textContent = 'In progress';
    const copy = document.createElement('button');
    copy.type = 'button';
    copy.className = 'btn btn--ghost btn--sm copy';   // a real row control takes the shared button styles
    copy.textContent = 'Copy id';
    copy.addEventListener('click', () => { window.__copied = true; });
    const row = linkRow({ href: '#/task/T-102', name: 'T-102 · Re-skin the Kanban cards and columns', content: [status], controls: [copy] });

    const table = document.createElement('table');
    const thead = table.createTHead();
    const tbody = table.createTBody();
    tbody.insertRow().append(...['T-102', 'Re-skin the Kanban cards and columns'].map((t) => Object.assign(document.createElement('td'), { textContent: t })));
    window.__sorts = [];
    const render = (sort) => {
      const tr = document.createElement('tr');
      const onSort = (next) => { window.__sorts.push(next); render(next); };
      tr.append(sortHeader({ key: 'id', label: 'ID', sort, onSort }), sortHeader({ key: 'title', label: 'Title', sort, onSort }));
      thead.replaceChildren(tr);
    };
    render({ by: 'id', dir: 'asc' });

    host.append(before, row, table);
    document.getElementById('screen-mount').prepend(host);
  });
  return page.locator('#rows-host .link-row');
}

// The link's ::after covers the row, so the pointer lands on the link wherever the row is pressed. A locator click on
// the content would refuse (another element receives the pointer), so the press goes to the content's centre instead.
async function pressContent(page, row, modifiers = [], button = 'left') {
  const box = await row.locator('.link-row__content').boundingBox();
  for (const m of modifiers) await page.keyboard.down(m);
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2, { button });
  for (const m of modifiers) await page.keyboard.up(m);
}

test('a click on the row\'s content opens the task\'s dialog; Escape closes it and focus is on the row\'s link', async ({ page }) => {
  const row = await boot(page);
  await pressContent(page, row);
  const dialog = page.locator('.modal--detail');
  await expect(dialog).toBeVisible();
  await expect(dialog.locator('.modal-eyebrow')).toHaveText('T-102');
  await expect(page).toHaveURL(/#\/kanban$/);
  await page.keyboard.press('Escape');
  await expect(page.locator('.modal')).toHaveCount(0);
  await expect(row.locator('.link-row__link')).toBeFocused();
});

test('modified clicks are the browser\'s and a row\'s own control never opens the row', async ({ page }) => {
  const row = await boot(page);
  // Ctrl+click and middle-click open a new tab, Shift+click a new window: each is a new page and no dialog here.
  for (const [modifiers, button] of [[['Control'], 'left'], [[], 'middle'], [['Shift'], 'left']]) {
    const how = `${modifiers.join('+') || 'no modifier'} ${button}`;
    const opened = page.context().waitForEvent('page');
    await pressContent(page, row, modifiers, button);
    const tab = await opened;
    await expect(tab, how).toHaveURL(/#\/task\/T-102$/);
    await tab.close();
    await expect(page.locator('.modal'), how).toHaveCount(0);
    await expect(page, how).toHaveURL(/#\/kanban$/);
  }

  await row.getByRole('button', { name: 'Copy id' }).click();
  expect(await page.evaluate(() => window.__copied)).toBe(true);
  // Give a wrongly opened dialog the time it takes a real one to load before saying there is none.
  await page.waitForTimeout(300);
  await expect(page.locator('.modal')).toHaveCount(0);
});

test('a cut name keeps its words on hover: what the pointer is over carries the full text as its title', async ({ page }) => {
  await boot(page);
  const full = 'T-105 · A task title long enough that no row on any screen could ever show it on a single line without cutting it';
  await page.evaluate(async (text) => {
    const { linkRow } = await import('/js/components/link-row.js');
    const { truncate } = await import('/js/lib/text.js');
    const row = linkRow({ href: '#/task/T-105', name: truncate(text), content: ['Todo'], className: 'cut-row' });
    row.style.width = '240px';
    document.getElementById('rows-host').append(row);
  }, full);
  const name = page.locator('.cut-row .truncate');
  expect(await name.evaluate((el) => el.scrollWidth > el.clientWidth), 'the name is cut').toBe(true);
  const box = await name.boundingBox();
  const x = box.x + box.width / 2;
  const y = box.y + box.height / 2;
  await page.mouse.move(x, y);
  const hit = await page.evaluate(([px, py]) => {
    const el = document.elementFromPoint(px, py);
    return { tag: el.localName, title: el.closest('[title]')?.title ?? null };
  }, [x, y]);
  expect(hit).toEqual({ tag: 'a', title: full });
});

test('390×844: the sort headers and the row\'s own buttons and links are at least 44px; icon-only ones 44 wide', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const row = await boot(page);
  await page.evaluate(async () => {
    const { icon } = await import('/js/components/icon.js');
    const more = document.createElement('button');
    more.type = 'button';
    more.className = 'btn btn--ghost btn--icon btn--sm';
    more.setAttribute('aria-label', 'More actions');
    more.append(icon('more', { size: 16 }));
    const epic = document.createElement('a');
    epic.href = '#/epics';
    epic.className = 'row-epic';
    epic.textContent = 'E';
    document.querySelector('#rows-host .link-row__controls').append(more, epic);
  });
  const sizes = async (loc) => loc.evaluateAll((els) => els.map((el) => {
    const r = el.getBoundingClientRect();
    return { name: el.textContent || el.getAttribute('aria-label'), w: Math.round(r.width), h: Math.round(r.height) };
  }));
  const controls = await sizes(row.locator('.link-row__controls > *'));
  const headers = await sizes(page.locator('#rows-host .sort-header'));
  expect(controls).toHaveLength(3);
  expect(headers).toHaveLength(2);
  for (const s of [...controls, ...headers]) expect(s.h, `${s.name} height`).toBeGreaterThanOrEqual(44);
  for (const s of controls) expect(s.w, `${s.name} width`).toBeGreaterThanOrEqual(44);
});

test('Tab reaches the link, then the copy button; a keyboard-focused link rings the row', async ({ page }) => {
  const row = await boot(page);
  await page.locator('#before').focus();
  await page.keyboard.press('Tab');
  const link = row.locator('.link-row__link');
  await expect(link).toBeFocused();
  expect(await row.evaluate((el) => getComputedStyle(el).outlineStyle)).toBe('solid');
  expect(await link.evaluate((el) => getComputedStyle(el).outlineColor)).toBe('rgba(0, 0, 0, 0)');
  await page.keyboard.press('Tab');
  await expect(row.getByRole('button', { name: 'Copy id' })).toBeFocused();
  expect(await row.evaluate((el) => getComputedStyle(el).outlineStyle)).toBe('none');
});

test('a sortable header is a button: Enter on Title sorts by title ascending and moves aria-sort to it', async ({ page }) => {
  await boot(page);
  const table = page.locator('#rows-host table');
  await expect(table.locator('th[aria-sort]')).toHaveCount(1);
  await expect(table.locator('th[aria-sort="ascending"]')).toHaveText('ID');
  await table.getByRole('button', { name: 'ID' }).focus();
  await page.keyboard.press('Tab');
  await expect(table.getByRole('button', { name: 'Title' })).toBeFocused();
  await page.keyboard.press('Enter');
  expect(await page.evaluate(() => window.__sorts)).toEqual([{ by: 'title', dir: 'asc' }]);
  await expect(table.locator('th[aria-sort]')).toHaveCount(1);
  await expect(table.locator('th[aria-sort="ascending"]')).toHaveText('Title');
  await expect(table.getByRole('columnheader', { name: 'Title' })).toHaveAttribute('aria-sort', 'ascending');
  await expect(table.locator('th').nth(0).locator('.sort-header__dir--none')).toHaveCount(1);
});

async function axe(page, selector) {
  await page.evaluate(axeSource);
  const result = await page.evaluate((sel) => {
    const aria = window.axe.getRules().map((r) => r.ruleId).filter((id) => id.startsWith('aria-'));
    return window.axe.run(document.querySelector(sel), {
      runOnly: { type: 'rule', values: ['nested-interactive', 'color-contrast', ...aria] },
      resultTypes: ['violations'],
    });
  }, selector);
  return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`);
}

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): the row and the table have no nested-interactive, contrast or aria violation`, async ({ page }) => {
    await boot(page, { theme });
    expect(await axe(page, '#rows-host .link-row')).toEqual([]);
    expect(await axe(page, '#rows-host table')).toEqual([]);
  });
}
