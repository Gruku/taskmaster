// User intent: the Dashboard works in a real browser — it opens on four counts that are links and stay put while the board
// redraws; a long note is clamped and opens from the keyboard; notes are pinned, archived and created by keyboard without
// losing focus; and a refused note write is said in words while the typed text stays.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { NOTES, LONG_NOTE, deskMocks, summaryMocks } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

// Every page error, and every note write the page sent (method, path, JSON body).
function watch(page) {
  const errors = [];
  const writes = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  page.on('request', (req) => {
    const { pathname } = new URL(req.url());
    if (req.method() === 'POST' && pathname.startsWith('/api/notes')) writes.push({ path: pathname, body: req.postDataJSON() });
  });
  return { errors, writes };
}

// GET /api/notes answered from a list the test changes as writes land.
async function serveNotes(page, current) {
  await page.route('**/api/notes', (route) => (route.request().method() === 'GET'
    ? route.fulfill({ json: { notes: current() } })
    : route.fallback()));
}

async function openDesk(page, mocks = deskMocks()) {
  await mockApi(page, mocks);
  return page;
}

async function tabTo(page, selector, max = 40) {
  for (let i = 0; i < max; i++) {
    if (await page.evaluate((sel) => document.activeElement?.matches(sel), selector)) return;
    await page.keyboard.press('Tab');
  }
  throw new Error(`Tab never reached ${selector}`);
}

const note = (page, id) => page.locator(`.dk-note[data-note-id="${id}"]`);

const summary = (page) => page.getByRole('navigation', { name: 'Project summary' });

test('the strip counts from the board, issues and bugs, and each count is a link', async ({ page }) => {
  const { errors } = watch(page);
  await openDesk(page, summaryMocks());
  await page.goto('/#/dashboard');
  const strip = summary(page);
  await expect(page.locator('.dk-desk > :first-child')).toHaveClass(/dk-summary/);
  await expect(strip.getByRole('link', { name: '2 In progress' })).toHaveAttribute('href', '#/table?status=in-progress');
  await expect(strip.getByRole('link', { name: '1 Waiting on you' })).toHaveAttribute('href', '#/table?status=in-review');
  await expect(strip.getByRole('link', { name: '3 Open issues' })).toHaveAttribute('href', '#/issues');
  await expect(strip.getByRole('link', { name: '2 Open bugs' })).toHaveAttribute('href', '#/bugs');
  await expect(strip.getByRole('link')).toHaveCount(4);
  await strip.getByRole('link', { name: /Open issues/ }).click();
  await expect(page).toHaveURL(/#\/issues$/);
  await expect(page.locator('.dk-summary')).toHaveCount(0);
  expect(errors).toEqual([]);
});

test('a read that fails leaves the other counts', async ({ page }) => {
  const { errors } = watch(page);
  await openDesk(page, summaryMocks({ '/api/issues': { status: 500, json: {} } }));
  await page.goto('/#/dashboard');
  const strip = summary(page);
  await expect(strip.getByRole('link', { name: '2 In progress' })).toBeVisible();
  const issues = strip.getByRole('link', { name: /Open issues/ });
  await expect(issues.locator('.dk-stat__n')).toHaveText('—');
  await expect(issues).toHaveAttribute('title', 'Not loaded');
  await expect(strip.getByRole('link', { name: '1 Waiting on you' })).toBeVisible();
  await expect(strip.getByRole('link', { name: '2 Open bugs' })).toBeVisible();
  expect(errors).toEqual([]);
});

test('a board redraw updates the counts in place and keeps focus', async ({ page }) => {
  const { errors } = watch(page);
  await openDesk(page, summaryMocks());
  await page.goto('/#/dashboard');
  const link = summary(page).getByRole('link', { name: /In progress/ });
  await expect(link).toHaveText('2 In progress');
  await link.focus();
  await page.evaluate(() => import('/js/store.js').then(({ store }) => {
    const next = structuredClone(store.getBacklog());
    next.revision = 'r2';
    next.tasks = next.tasks.map((t) => (t.id === 'T-104' ? { ...t, status: 'in-progress' } : t));
    store.setBoard(next);
  }));
  await expect(link).toHaveText('3 In progress');
  await expect(link).toBeFocused();
  expect(errors).toEqual([]);
});

test('at 390px the strip is two columns of tall links and nothing scrolls sideways', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openDesk(page, summaryMocks());
  await page.goto('/#/dashboard');
  const links = summary(page).getByRole('link');
  await expect(links).toHaveCount(4);
  await expect(links.first()).toHaveText('2 In progress');
  const boxes = await links.evaluateAll((els) => els.map((el) => el.getBoundingClientRect().toJSON()));
  expect(new Set(boxes.map((b) => Math.round(b.left))).size).toBe(2);
  for (const b of boxes) expect(b.height).toBeGreaterThanOrEqual(44);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await expect(page.locator('#topbar-actions > *:visible')).toHaveCount(0);
});

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): the summary strip shows no violations`, async ({ page }) => {
    await openDesk(page, summaryMocks({ theme }));
    await page.goto('/#/dashboard');
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(summary(page).getByRole('link', { name: '3 Open issues' })).toBeVisible();
    await page.evaluate(axeSource);
    const result = await page.evaluate(() => window.axe.run(document.querySelector('.dk-summary'), { resultTypes: ['violations'] }));
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}

test('the board mounts with no error, no focused composer, and marked loaded locally', async ({ page }) => {
  const { errors } = watch(page);
  await openDesk(page);
  await page.goto('/#/dashboard');
  await expect(note(page, 'NOTE-002')).toBeVisible();
  expect(await page.evaluate(() => document.activeElement?.classList.contains('dk-composer__input'))).toBe(false);
  expect(await page.evaluate(() => typeof window.marked?.parse === 'function')).toBe(true);
  expect(errors).toEqual([]);
});

test('the composer creates a note', async ({ page }) => {
  const { writes } = watch(page);
  await openDesk(page, { ...deskMocks(), 'POST /api/notes': { ok: true, id: 'NOTE-005' } });
  const added = { id: 'NOTE-005', author: 'user', body: 'Ship it', created: new Date().toISOString() };
  await serveNotes(page, () => (writes.length ? [...NOTES.notes, added] : NOTES.notes));
  await page.goto('/#/dashboard');
  const input = page.locator('.dk-composer__input');
  await input.fill('Ship it');
  await input.press('Enter');
  await expect(note(page, 'NOTE-005')).toContainText('Ship it');
  expect(writes).toEqual([{ path: '/api/notes', body: { text: 'Ship it', pinned: false } }]);
  await expect(input).toHaveValue('');
  await expect(input).toBeFocused();
});

test('pin and archive work from the keyboard', async ({ page }) => {
  const { writes } = watch(page);
  await openDesk(page, {
    ...deskMocks(),
    'POST /api/notes/NOTE-002/update': { ok: true },
    'POST /api/notes/NOTE-002/archive': { ok: true },
  });
  let pinned = false;
  let archived = false;
  await serveNotes(page, () => NOTES.notes
    .filter((n) => !(archived && n.id === 'NOTE-002'))
    .map((n) => (n.id === 'NOTE-002' ? { ...n, pinned } : n)));
  page.on('request', (req) => {
    const { pathname } = new URL(req.url());
    if (pathname === '/api/notes/NOTE-002/update') pinned = true;
    if (pathname === '/api/notes/NOTE-002/archive') archived = true;
  });
  await page.goto('/#/dashboard');
  await expect(note(page, 'NOTE-002')).toBeVisible();

  await page.locator('.dk-composer__input').focus();
  await tabTo(page, '[data-note-id="NOTE-002"] .dk-note__pin');
  await page.keyboard.press('Space');
  // The refreshed board shows the note pinned, and focus is on the same note's control.
  const pin = note(page, 'NOTE-002').locator('.dk-note__pin');
  await expect(pin).toHaveText('Unpin');
  await expect(pin).toBeFocused();
  expect(writes).toEqual([{ path: '/api/notes/NOTE-002/update', body: { pinned: true } }]);

  await page.keyboard.press('Tab');
  await expect(note(page, 'NOTE-002').getByRole('button', { name: 'Archive note' })).toBeFocused();
  await page.keyboard.press('Enter');
  await expect(note(page, 'NOTE-002')).toHaveCount(0);
  expect(writes.map((w) => w.path)).toEqual(['/api/notes/NOTE-002/update', '/api/notes/NOTE-002/archive']);
  // The note is gone, so focus lands in the composer, not on <body>.
  await expect(page.locator('.dk-composer__input')).toBeFocused();
});

test('a very long note is clamped and expands from the keyboard', async ({ page }) => {
  await openDesk(page, deskMocks({ notes: { notes: [LONG_NOTE] } }));
  await page.goto('/#/dashboard');
  const card = note(page, 'NOTE-099');
  const body = card.locator('.dk-note__body');
  const more = card.getByRole('button', { name: 'Show more' });
  await expect(more).toBeVisible();
  const clamp = await body.evaluate((el) => ({
    client: el.clientHeight, scroll: el.scrollHeight, font: parseFloat(getComputedStyle(el).fontSize),
  }));
  expect(clamp.client).toBeLessThanOrEqual(16 * clamp.font + 1);
  expect(clamp.scroll).toBeGreaterThan(clamp.client + 1);

  // Tab passes the note's link on the way, and a focused link opens the note; Enter then closes and reopens it.
  await page.locator('.dk-composer__input').focus();
  await tabTo(page, '.dk-note__more');
  const toggle = card.locator('.dk-note__more');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(toggle).toHaveText('Show more');
  expect(await body.evaluate((el) => el.clientHeight)).toBeLessThanOrEqual(16 * clamp.font + 1);
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  await expect(toggle).toHaveText('Show less');
  const open = await body.evaluate((el) => ({ client: el.clientHeight, scroll: el.scrollHeight }));
  expect(open.client).toBeGreaterThanOrEqual(open.scroll - 1);
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(more).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('a link in the clamped-away part of a long note opens the note when Tab reaches it', async ({ page }) => {
  await openDesk(page, deskMocks({ notes: { notes: [LONG_NOTE] } }));
  await page.goto('/#/dashboard');
  const card = note(page, 'NOTE-099');
  await expect(card.locator('.dk-note__more')).toHaveText('Show more');
  await page.locator('.dk-composer__input').focus();
  await tabTo(page, '.dk-note__body a');
  await expect(card).toHaveClass(/is-expanded/);
  await expect(card.locator('.dk-note__more')).toHaveAttribute('aria-expanded', 'true');
  // The focused link is drawn inside the note, not scrolled out of a clipped body.
  const [link, paper, body] = await Promise.all([
    card.locator('.dk-note__body a').boundingBox(), card.boundingBox(), card.locator('.dk-note__body').boundingBox()]);
  expect(link.y).toBeGreaterThanOrEqual(paper.y);
  expect(link.y + link.height).toBeLessThanOrEqual(paper.y + paper.height);
  expect(link.y + link.height).toBeLessThanOrEqual(body.y + body.height);
  expect(await card.locator('.dk-note__body').evaluate((el) => el.scrollTop)).toBe(0);
});

test('an expanded note stays expanded when the board refreshes, and focus stays on the control used', async ({ page }) => {
  const { writes } = watch(page);
  await openDesk(page, { ...deskMocks(), 'POST /api/notes/NOTE-099/update': { ok: true } });
  await serveNotes(page, () => [{ ...LONG_NOTE, pinned: writes.length > 0 }]);
  await page.goto('/#/dashboard');
  const card = note(page, 'NOTE-099');
  await card.getByRole('button', { name: 'Show more' }).click();
  await expect(card).toHaveClass(/is-expanded/);
  await card.locator('.dk-note__pin').focus();
  await page.keyboard.press('Space');
  const pin = card.locator('.dk-note__pin');
  await expect(pin).toHaveText('Unpin');
  await expect(pin).toBeFocused();
  await expect(card).toHaveClass(/is-expanded/);
  await expect(card.locator('.dk-note__more')).toHaveText('Show less');
  await expect(card.locator('.dk-note__more')).toHaveAttribute('aria-expanded', 'true');
  expect(writes).toEqual([{ path: '/api/notes/NOTE-099/update', body: { pinned: true } }]);
});

test('a failed note write is said in words and keeps the text', async ({ page }) => {
  await openDesk(page, {
    ...deskMocks(),
    'POST /api/notes': { status: 500, json: { ok: false, error: 'database is locked' } },
  });
  await page.goto('/#/dashboard');
  const input = page.locator('.dk-composer__input');
  await input.fill('Remember the counts');
  await input.press('Enter');
  await expect(page.locator('.dk-board-error')).toHaveText('The server could not save this change. Try again in a moment.');
  await expect(page.locator('.dk-board-error')).toHaveAttribute('role', 'alert');
  await expect(input).toHaveValue('Remember the counts');
  await expect(input).toBeFocused();
});

test('leaving with a note mid-edit saves it once and throws nothing', async ({ page }) => {
  const { errors, writes } = watch(page);
  await openDesk(page, { ...deskMocks(), 'POST /api/notes/NOTE-003/update': { ok: true } });
  await page.goto('/#/dashboard');
  await note(page, 'NOTE-003').getByRole('button', { name: 'Edit note' }).click();
  const editor = note(page, 'NOTE-003').getByRole('textbox', { name: 'Edit note' });
  await expect(editor).toBeFocused();
  await page.keyboard.type(' later');
  await page.evaluate(() => { location.hash = '#/settings'; });
  await expect(page.locator('.dk-board')).toHaveCount(0);
  await expect.poll(() => writes.length).toBe(1);
  await page.waitForTimeout(300);
  expect(writes).toEqual([{ path: '/api/notes/NOTE-003/update', body: { text: 'Ask about the Linear sync retries. later' } }]);
  expect(errors).toEqual([]);
});

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): the board with a clamped note shows no violations`, async ({ page }) => {
    await openDesk(page, deskMocks({ theme, notes: { notes: [LONG_NOTE, ...NOTES.notes] } }));
    await page.goto('/#/dashboard');
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.locator('.dk-note__more')).toBeVisible();
    await page.evaluate(axeSource);
    const result = await page.evaluate(() => window.axe.run(document.querySelector('.dk-board'), { resultTypes: ['violations'] }));
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}
