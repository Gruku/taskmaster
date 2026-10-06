// User intent: the Ideas list in a real browser — rows are links that select in place and deep-link, markers only when a
// status is set, status chips + Tags + Show archived filtering together, New idea in row 1, states in words, a keyboard
// walk, a phone layout with nothing sideways or under 44px, no axe violations, and nothing left behind on leaving.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { ideasMocks, LIST_IDEAS, LONG_IDEAS } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

let errors = [];
let dialogs = [];
test.beforeEach(async ({ page }) => {
  errors = [];
  dialogs = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('dialog', (d) => { dialogs.push(d.type()); d.dismiss().catch(() => {}); });
  await page.emulateMedia({ reducedMotion: 'reduce' });
});
test.afterEach(async ({ page }) => {
  expect(unmockedWrites(page)).toEqual([]);
  expect(errors).toEqual([]);
  expect(dialogs).toEqual([]);
});

async function boot(page, { theme = 'dark', width = 1440, height = 900, wait = true, hash = '#/ideas', ...overrides } = {}) {
  await page.setViewportSize({ width, height });
  await mockApi(page, { ...ideasMocks({ theme }), ...overrides });
  const gets = { count: 0 };
  page.on('request', (r) => {
    if (r.method() === 'GET' && new URL(r.url()).pathname === '/api/ideas') gets.count++;
  });
  await page.goto(`/${hash}`);
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  if (wait) await expect(page.locator('.ideas__list .idea-row').first()).toBeVisible();
  return gets;
}

const rowIds = (page) => page.locator('.ideas__list .idea-row a[data-id]').evaluateAll((as) => as.map((a) => a.dataset.id));
const link = (page, id) => page.locator(`.ideas__list a[data-id="${id}"]`);
// The inner locator is scoped to the row, so it cannot name the list that contains the row.
const rowOf = (page, id) => page.locator('.ideas__list .idea-row').filter({ has: page.locator(`a[data-id="${id}"]`) });
const pane = (page) => page.locator('section.ideas-detail');
const statusChip = (page, name) => page.getByRole('group', { name: 'Status' }).getByRole('button', { name: new RegExp(`^${name}`) });
const tagsButton = (page) => page.locator('.ideas button.tag-filter');
const tagDialog = (page) => page.getByRole('dialog', { name: 'Filter by tag' });
const choice = (page, key) => tagDialog(page).locator(`.tag-filter__list input[type="checkbox"][value="${key}"]`);
const setIdeas = (page, list) => page.evaluate((l) => import('/js/store.js').then(({ store }) => store.setIdeas(l)), list);

test('rows are links with markers, tags and age; an idea with no status has no marker', async ({ page }) => {
  await boot(page);
  expect(await rowIds(page)).toEqual(['IDEA-4', 'IDEA-3', 'IDEA-2', 'IDEA-1']);
  await expect(link(page, 'IDEA-1')).toHaveAttribute('href', '#/ideas/IDEA-1');
  await expect(rowOf(page, 'IDEA-1')).toContainText('Exploring');
  await expect(rowOf(page, 'IDEA-4').locator('.marker')).toHaveCount(0);
  await expect(rowOf(page, 'IDEA-3').locator('.idea-row__tags .list-tag')).toHaveCount(3);
  const more = rowOf(page, 'IDEA-3').locator('.idea-row__more');
  await expect(more).toHaveText('+2');
  const title = await more.getAttribute('title');
  expect(title).toContain('layout');
  expect(title).toContain('phone');
  await expect(rowOf(page, 'IDEA-1').locator('time.idea-row__age')).toBeVisible();
});

test('a click selects in place and the address follows', async ({ page, context }) => {
  const gets = await boot(page);
  const before = gets.count;
  await link(page, 'IDEA-2').click();
  await expect(pane(page).getByRole('heading', { level: 2 })).toHaveText('Faster store writes');
  await expect.poll(() => page.evaluate(() => location.hash)).toBe('#/ideas/IDEA-2');
  await expect(link(page, 'IDEA-2')).toHaveAttribute('aria-current', 'true');
  expect(gets.count).toBe(before);
  const [popup] = await Promise.all([context.waitForEvent('page'), link(page, 'IDEA-3').click({ modifiers: ['Control'] })]);
  await popup.close();
  await expect(pane(page).getByRole('heading', { level: 2 })).toHaveText('Faster store writes');
  await expect(link(page, 'IDEA-3')).not.toHaveAttribute('aria-current', 'true');
});

test('a pasted address opens that idea; an unknown one says so', async ({ page }) => {
  await boot(page, { hash: '#/ideas/IDEA-1' });
  await expect(pane(page).getByRole('heading', { level: 2 })).toHaveText('Board swimlanes by epic');
  await expect(pane(page).locator('.ideas-detail__body strong')).toHaveText('board');
  await expect(pane(page).locator('.ideas-detail__body li')).toHaveCount(2);
  await expect(pane(page).locator('a[href="#/issue/ISS-001"]')).toBeVisible();
  await page.goto('/#/ideas/IDEA-99');
  await expect(page.getByText('IDEA-99 is not in this project.')).toBeVisible();
});

test('status chips, Tags and Show archived filter together', async ({ page }) => {
  await boot(page);
  await statusChip(page, 'Candidate').click();
  await expect.poll(() => rowIds(page)).toEqual(['IDEA-2']);
  await expect(page.locator('#topbar-count')).toHaveText('4 ideas · 1 visible');
  await page.getByRole('button', { name: /^Clear/ }).click();
  await expect.poll(() => rowIds(page)).toEqual(['IDEA-4', 'IDEA-3', 'IDEA-2', 'IDEA-1']);
  await tagsButton(page).click();
  await expect(tagDialog(page).locator('.tag-filter__name', { hasText: /^ux$/i })).toHaveText('UX');
  await choice(page, 'ux').check();
  await expect.poll(() => rowIds(page)).toEqual(['IDEA-3', 'IDEA-1']);
  await page.keyboard.press('Escape');
  await page.getByRole('button', { name: /^Show archived/ }).click();
  await page.getByRole('button', { name: /^Clear/ }).click();
  await page.getByRole('button', { name: /^Show archived/ }).click();
  await expect(rowOf(page, 'IDEA-5').locator('.list-tag', { hasText: 'Archived' })).toBeVisible();
  await link(page, 'IDEA-5').click();
  await pane(page).locator('a.link-pill[href="#/task/T-111"]').click();
  await expect(page.getByRole('dialog').filter({ hasText: 'T-111' })).toBeVisible();
});

test('a list redraw while Tags is open keeps it open, its choices and its focus', async ({ page }) => {
  await boot(page);
  await tagsButton(page).click();
  await choice(page, 'ux').check();
  await choice(page, 'perf').focus();
  await setIdeas(page, [...LIST_IDEAS, { id: 'IDEA-6', title: 'Board density', status: 'exploring', tags: ['board'], created: new Date().toISOString() }]);
  await expect(link(page, 'IDEA-6')).toHaveCount(0);
  await expect(tagDialog(page)).toBeVisible();
  await expect(choice(page, 'ux')).toBeChecked();
  await expect(choice(page, 'perf')).toBeFocused();
  await expect(tagDialog(page).locator('.tag-filter__option').filter({ has: page.locator('input[type="checkbox"][value="board"]') })).toContainText('2');
  expect(await rowIds(page)).toEqual(['IDEA-3', 'IDEA-1']);
});

test('leaving Ideas with Tags open leaves nothing behind', async ({ page }) => {
  await boot(page);
  await tagsButton(page).click();
  await expect(tagDialog(page)).toBeVisible();
  await page.evaluate(() => { location.hash = '#/kanban'; });
  await expect(page.locator('#topbar').getByRole('textbox', { name: 'Find tasks' })).toBeVisible();
  await expect(page.locator('.popover')).toHaveCount(0);
  await expect(page.locator('.tag-filter__popover')).toHaveCount(0);
  await expect(page.locator('.ideas')).toHaveCount(0);
});

test('New idea is in row 1 and a created idea appears', async ({ page }) => {
  const reads = { created: false };
  await page.setViewportSize({ width: 1440, height: 900 });
  await mockApi(page, { ...ideasMocks(), 'POST /api/ideas': { ok: true, id: 'IDEA-9' } });
  await page.route((url) => url.pathname === '/api/ideas', (route) => {
    if (route.request().method() !== 'GET') { reads.created = true; return route.fallback(); }
    const extra = reads.created ? [{ id: 'IDEA-9', title: 'Faster board', status: 'exploring', tags: [], created: new Date().toISOString() }] : [];
    return route.fulfill({ json: { ideas: [...LIST_IDEAS, ...extra] } });
  });
  await page.goto('/#/ideas');
  await expect(page.locator('.ideas__list .idea-row').first()).toBeVisible();
  await expect(page.locator('#topbar-count')).toHaveText('4 ideas');
  await page.locator('#topbar-primary').getByRole('button', { name: 'Create a new idea' }).click();
  const dialog = page.getByRole('dialog', { name: 'Create idea' });
  await dialog.locator('[data-key="title"]').locator('input, textarea').first().fill('Faster board');
  await dialog.getByRole('button', { name: 'Save', exact: true }).click();
  await expect(link(page, 'IDEA-9')).toBeVisible();
  await expect(page.locator('#topbar-count')).toHaveText('5 ideas');
});

test('keyboard walk: search, chips, Tags, Show archived, then rows newest first; Enter selects and keeps focus', async ({ page }) => {
  await boot(page);
  await page.getByPlaceholder('Search ideas…').focus();
  const seen = [];
  for (let i = 0; i < 20; i++) {
    await page.keyboard.press('Tab');
    const d = await page.evaluate(() => {
      const el = document.activeElement;
      // A chip's label and count are adjacent spans with no space between them, so read the label alone.
      return el.dataset.id || (el.closest('.tag-filter') ? 'Tags' : '') || (el.querySelector('.chip__label') || el).textContent.trim().split(/\s/)[0];
    });
    seen.push(d);
    if (d === 'IDEA-2') break;
  }
  const at = (x) => seen.indexOf(x);
  for (const x of ['Exploring', 'Candidate', 'Tags', 'Show', 'IDEA-4', 'IDEA-3', 'IDEA-2']) expect(at(x), seen.join(',')).toBeGreaterThanOrEqual(0);
  expect(at('Exploring')).toBeLessThan(at('Tags'));
  expect(at('Tags')).toBeLessThan(at('Show'));
  expect(seen.slice(at('Show') + 1)).toEqual(['IDEA-4', 'IDEA-3', 'IDEA-2']);
  await page.keyboard.press('Enter');
  await expect(pane(page).getByRole('heading', { level: 2 })).toHaveText('Faster store writes');
  await expect(link(page, 'IDEA-2')).toBeFocused();
});

test('phone: the pane replaces the list, Back returns focus to the row', async ({ page }) => {
  await boot(page, { width: 390, height: 844 });
  await link(page, 'IDEA-1').focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.ideas__list')).toBeHidden();
  await expect(pane(page).getByRole('heading', { level: 2 })).toBeFocused();
  await page.getByRole('button', { name: 'Back to ideas' }).click();
  await expect(page.locator('.ideas__list')).toBeVisible();
  await expect.poll(() => page.evaluate(() => location.hash)).toBe('#/ideas');
  await expect(link(page, 'IDEA-1')).toBeFocused();
});

test('a failed load is said in words', async ({ page }) => {
  await boot(page, { wait: false, '/api/ideas': { status: 500, json: { ok: false, error: 'sqlite3.OperationalError at /api/ideas' } } });
  const mount = page.locator('#screen-mount');
  await expect(mount.getByText('Could not load ideas.')).toBeVisible();
  const text = await mount.innerText();
  for (const bad of ['500', '/api', 'sqlite3', '{']) expect(text).not.toContain(bad);
  await page.unrouteAll({ behavior: 'ignoreErrors' });
});

test('a 404 counts as no ideas', async ({ page }) => {
  await boot(page, { wait: false, '/api/ideas': { status: 404, json: { ok: false, code: 404, error: 'not found' } } });
  await expect(page.locator('#screen-mount').getByText('No ideas yet.')).toBeVisible();
});

test('at 390 with long data nothing scrolls sideways and every control is 44px tall', async ({ page }) => {
  await boot(page, { width: 390, height: 844, '/api/ideas': { ideas: LONG_IDEAS } });
  await page.evaluate(() => document.fonts.ready);
  await statusChip(page, 'Exploring').click();
  const m = await page.evaluate(() => {
    const mount = document.getElementById('screen-mount');
    return { doc: document.documentElement.scrollWidth, inner: innerWidth, ms: mount.scrollWidth, mc: mount.clientWidth, h: document.documentElement.scrollHeight };
  });
  console.log(`ideas 390 page height: ${m.h}`);
  expect(m.doc).toBeLessThanOrEqual(m.inner);
  expect(m.ms).toBeLessThanOrEqual(m.mc);
  expect(m.h).toBeLessThanOrEqual(8000);
  const controls = page.locator([
    // .list-filters holds every chip, More, Tags, Show archived and Clear.
    '.ideas .list-filters button', '.ideas .ideas__list .link-row__link',
    '#topbar-primary button',
  ].join(', '));
  const heights = await controls.evaluateAll((els) => els.filter((e) => e.offsetParent).map((e) => [e.textContent.trim().slice(0, 20), e.getBoundingClientRect().height]));
  expect(heights.length).toBeGreaterThan(10);
  expect(heights.filter(([, hgt]) => hgt < 44)).toEqual([]);
});

for (const theme of ['dark', 'light']) {
  test(`axe: no violations with IDEA-1 selected (${theme})`, async ({ page }) => {
    await boot(page, { theme, hash: '#/ideas/IDEA-1' });
    await expect(pane(page).getByRole('heading', { level: 2 })).toHaveText('Board swimlanes by epic');
    await page.evaluate(() => document.fonts.ready);
    await page.evaluate(axeSource);
    const result = await page.evaluate(async () => {
      const aria = window.axe.getRules().map((r) => r.ruleId).filter((id) => id.startsWith('aria-'));
      const values = ['color-contrast', 'nested-interactive', 'heading-order', 'list', 'listitem', ...aria];
      const opts = { runOnly: { type: 'rule', values }, resultTypes: ['violations'] };
      const out = [];
      for (const id of ['screen-mount', 'topbar']) out.push(...(await window.axe.run(document.getElementById(id), opts)).violations);
      return out;
    });
    expect(result.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}
