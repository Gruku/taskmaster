// User intent: the Dashboard's notes work in a real browser — a long note is clamped and opens from the keyboard, notes
// are pinned, archived and created by keyboard without losing focus, and a refused note write is said in words while
// the typed text stays.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { NOTES, LONG_NOTE, deskMocks } from './mock-fixtures.js';

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

  await page.locator('.dk-composer__input').focus();
  await tabTo(page, '.dk-note__more');
  await page.keyboard.press('Enter');
  await expect(card.locator('.dk-note__more')).toHaveAttribute('aria-expanded', 'true');
  await expect(card.locator('.dk-note__more')).toHaveText('Show less');
  const open = await body.evaluate((el) => ({ client: el.clientHeight, scroll: el.scrollHeight }));
  expect(open.client).toBeGreaterThanOrEqual(open.scroll - 1);
  await page.keyboard.press('Enter');
  await expect(card.locator('.dk-note__more')).toHaveAttribute('aria-expanded', 'false');
  await expect(card.locator('.dk-note__more')).toHaveText('Show more');

  await page.setViewportSize({ width: 390, height: 844 });
  await expect(more).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
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
