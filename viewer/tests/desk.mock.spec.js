// User intent: the Dashboard works in a real browser — it opens on four counts that are links and stay put while the board
// redraws; a long note is clamped and opens from the keyboard; notes are pinned, archived and created by keyboard without
// losing focus; a refused note write is said in words while the typed text stays; and the continuity band's rows open by
// link or by disclosure, its decisions say when the server refuses, and its "+N older" is a control.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import {
  NOTES, LONG_NOTE, BOARD, EMPTY_TASK, CONTINUITY, DECISION, summaryMocks, taskDetail, dashboardMocks,
} from './mock-fixtures.js';

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

// The base table is plan 4's dashboardMocks(); a test narrows the notes or continuity it needs on top.
const deskRoutes = ({ theme = 'dark', notes = NOTES, continuity = { items: [] } } = {}) => ({
  ...dashboardMocks({ theme }), '/api/notes': notes, '/api/continuity': continuity,
});

async function openDesk(page, mocks = deskRoutes()) {
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

test('a summary link lands on the filtered Table', async ({ page }) => {
  const { errors } = watch(page);
  await openDesk(page, summaryMocks());
  await page.goto('/#/dashboard');
  await summary(page).getByRole('link', { name: /In progress/ }).click();
  await expect.poll(() => page.evaluate(() => location.hash)).toBe('#/table?status=in-progress');
  await expect(page.getByRole('group', { name: 'Status' }).locator('.chip[data-value="in-progress"]')).toHaveAttribute('aria-pressed', 'true');
  const words = page.locator('.tbl-row .tbl-cell--status .marker__word');
  await expect(words.first()).toBeVisible();
  expect(new Set(await words.allTextContents())).toEqual(new Set(['In progress']));
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
  await openDesk(page, { ...deskRoutes(), 'POST /api/notes': { ok: true, id: 'NOTE-005' } });
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
    ...deskRoutes(),
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
  await openDesk(page, deskRoutes({ notes: { notes: [LONG_NOTE] } }));
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
  await openDesk(page, deskRoutes({ notes: { notes: [LONG_NOTE] } }));
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
  await openDesk(page, { ...deskRoutes(), 'POST /api/notes/NOTE-099/update': { ok: true } });
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
    ...deskRoutes(),
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
  await openDesk(page, { ...deskRoutes(), 'POST /api/notes/NOTE-003/update': { ok: true } });
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
    await openDesk(page, deskRoutes({ theme, notes: { notes: [LONG_NOTE, ...NOTES.notes] } }));
    await page.goto('/#/dashboard');
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.locator('.dk-note__more')).toBeVisible();
    await page.evaluate(axeSource);
    const result = await page.evaluate(() => window.axe.run(document.querySelector('.dk-board'), { resultTypes: ['violations'] }));
    expect(result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`)).toEqual([]);
  });
}

// ── Continuity band ─────────────────────────────────────────────────────────

// The Dashboard with a full continuity band behind it; `extra` overrides any route.
const bandMocks = ({ theme = 'dark', ...extra } = {}) => ({
  ...deskRoutes({ theme }),
  '/api/continuity': CONTINUITY,
  '/api/decisions/DEC-001': DECISION,
  '/api/handover/2026-10-05-r1': { body: '<lc>Cards done</lc>' },
  '/api/board': BOARD,
  '/api/task/T-106/detail': taskDetail({ ...EMPTY_TASK, id: 'T-106' }),
  ...extra,
});

const band = (page) => page.locator('.dk-continuity');
const spine = (page, label) => band(page).locator('.co-spine')
  .filter({ has: page.getByRole('heading', { name: label, exact: true }) });

test('rails cap at five with a +n older link', async ({ page }) => {
  const { errors } = watch(page);
  await openDesk(page, bandMocks());
  await page.goto('/#/dashboard');
  const resume = spine(page, 'Resume');
  await expect(resume.locator('.co-row')).toHaveCount(5);
  await expect(resume.locator('.co-spine__count')).toHaveText('5');
  const older = resume.locator('a.dk-older');
  await expect(older).toHaveText('+2 older');
  await expect(older).toHaveAttribute('href', '#/sessions');
  await expect(older).toHaveAttribute('aria-label', '2 older resume items');
  await expect(older).toHaveClass(/btn btn--ghost btn--sm/);
  for (const label of ['Review', 'Decide', 'Clean-up']) await expect(spine(page, label)).toHaveCount(1);
  expect(errors).toEqual([]);
});

test('clean-up rows are links', async ({ page }) => {
  await openDesk(page, bandMocks());
  await page.goto('/#/dashboard');
  await spine(page, 'Clean-up').locator('a[href="#/issue/ISS-012"]').click();
  await expect(page).toHaveURL(/#\/issue\/ISS-012$/);
  // Going back re-mounts the band; wait for its fetch to land and the row to paint, so the click does not race
  // the redraw on a loaded machine.
  const remounted = page.waitForResponse((res) => new URL(res.url()).pathname === '/api/continuity');
  await page.goBack();
  await expect(page).toHaveURL(/#\/dashboard$/);
  await remounted;
  const task = spine(page, 'Clean-up').locator('a[href="#/task/T-106"]');
  await expect(task).toBeVisible({ timeout: 15_000 });
  await task.click({ timeout: 15_000 });
  const dialog = page.locator('.modal--detail');
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText('T-106');
  await expect(spine(page, 'Clean-up').locator('a[href="#/ideas"]')).toHaveText('Pin a handover to the desk');
});

test('a handover row expands from the keyboard', async ({ page }) => {
  await openDesk(page, bandMocks());
  await page.goto('/#/dashboard');
  await expect(spine(page, 'Resume').locator('.co-row__toggle')).toHaveCount(5);
  await page.locator('.dk-composer__input').focus();
  await tabTo(page, '.co-row__toggle', 120);
  const toggle = spine(page, 'Resume').locator('.co-row__toggle').first();
  await expect(toggle).toBeFocused();
  await expect(toggle).toContainText('Cards done, columns next');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'true');
  const region = page.getByRole('region', { name: 'Handover 2026-10-05-r1' });
  await expect(region).toContainText('Cards done');
  await expect(toggle).toHaveAttribute('aria-controls', await region.getAttribute('id'));
  await page.keyboard.press('Enter');
  await expect(toggle).toHaveAttribute('aria-expanded', 'false');
  await expect(region).toHaveCount(0);
});

test('a handover body that cannot be read is said in words', async ({ page }) => {
  await openDesk(page, bandMocks({ '/api/handover/2026-10-05-r2': { status: 500, json: { ok: false, error: 'locked' } } }));
  await page.goto('/#/dashboard');
  await spine(page, 'Resume').locator('.co-row__toggle').nth(1).click();
  await expect(page.getByRole('region', { name: 'Handover 2026-10-05-r2' }))
    .toHaveText('This could not be loaded. Try again in a moment.');
});

test('a failed decision is said in words', async ({ page }) => {
  await openDesk(page, bandMocks({ 'POST /api/decisions/DEC-001/resolve': { status: 500, json: { ok: false, error: 'locked' } } }));
  await page.goto('/#/dashboard');
  const card = page.locator('.co-decision');
  await expect(card.getByRole('button')).toHaveCount(5);
  await expect(card.locator('button.co-decision__opt').nth(1)).toContainText('Recommended');
  await card.getByRole('button', { name: 'Pick option 2' }).click();
  await expect(card.getByRole('alert')).toHaveText('The server could not save this change. Try again in a moment.');
  for (const b of await card.getByRole('button').all()) await expect(b).toBeEnabled();
});

test('a resolved decision leaves the band redrawn without it', async ({ page }) => {
  const writes = [];
  page.on('request', (req) => {
    if (req.method() === 'POST') writes.push({ path: new URL(req.url()).pathname, body: req.postDataJSON() });
  });
  await openDesk(page, bandMocks({ 'POST /api/decisions/DEC-001/resolve': { ok: true } }));
  const left = { items: CONTINUITY.items.filter((i) => i.id !== 'DEC-001') };
  await page.route('**/api/continuity', (route) => route.fulfill({ json: writes.length ? left : CONTINUITY }));
  await page.goto('/#/dashboard');
  await page.locator('.co-decision').getByRole('button', { name: /Push the MR/ }).click();
  await expect(page.locator('.co-decision')).toHaveCount(0);
  await expect(spine(page, 'Decide')).toHaveCount(0);
  expect(writes).toEqual([{ path: '/api/decisions/DEC-001/resolve', body: { resolved_with: 1, rationale: '' } }]);
});

// The band answers GET /api/continuity from `serve`, which is called with the number of decision writes so far.
async function serveContinuity(page, serve) {
  let wrote = 0;
  page.on('request', (req) => { if (req.method() === 'POST' && /\/api\/decisions\//.test(req.url())) wrote += 1; });
  await page.route('**/api/continuity', (route) => {
    const reply = serve(wrote);
    return reply.status ? route.fulfill(reply) : route.fulfill({ json: reply });
  });
}
const withoutDecision = { items: CONTINUITY.items.filter((i) => i.id !== 'DEC-001') };
const focusInBand = (page) => page.evaluate(() => {
  const a = document.activeElement;
  return { inBand: !!a && a !== document.body && !!a.closest('.dk-continuity'), cls: a?.className ?? null };
});

test('a decision settled from the keyboard leaves focus in the band', async ({ page }) => {
  await openDesk(page, bandMocks({ 'POST /api/decisions/DEC-001/resolve': { ok: true } }));
  await serveContinuity(page, (wrote) => (wrote ? withoutDecision : CONTINUITY));
  await page.goto('/#/dashboard');
  await page.locator('.co-decision').getByRole('button', { name: 'Pick option 2' }).focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.co-decision')).toHaveCount(0);
  await expect.poll(() => focusInBand(page).then((f) => f.inBand)).toBe(true);
});

test('focus let go onto blank space is not pulled back into the band by a later redraw', async ({ page }) => {
  await openDesk(page, bandMocks({ 'POST /api/decisions/DEC-001/resolve': { ok: true } }));
  await serveContinuity(page, (wrote) => (wrote ? withoutDecision : CONTINUITY));
  await page.goto('/#/dashboard');
  await spine(page, 'Resume').locator('.co-row__toggle').first().focus();
  // A click on blank space: the toggle loses focus to <body> while it is still there and enabled.
  await page.evaluate(() => document.activeElement.blur());
  // A redraw that does not come from a focused control (a programmatic click moves no focus).
  await page.locator('.co-decision').getByRole('button', { name: 'Pick option 2' }).dispatchEvent('click');
  await expect(page.locator('.co-decision')).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => document.activeElement === document.body)).toBe(true);
  await page.waitForTimeout(150);
  expect(await focusInBand(page).then((f) => f.inBand)).toBe(false);
});

test('a decision refused from the keyboard puts focus back on the pressed button', async ({ page }) => {
  await openDesk(page, bandMocks({ 'POST /api/decisions/DEC-001/drop': { status: 500, json: { ok: false, error: 'locked' } } }));
  await page.goto('/#/dashboard');
  const drop = page.locator('.co-decision').getByRole('button', { name: 'Drop' });
  await drop.focus();
  await page.keyboard.press('Enter');
  await expect(page.locator('.co-decision').getByRole('alert')).toHaveText('The server could not save this change. Try again in a moment.');
  await expect(drop).toBeEnabled();
  await expect(drop).toBeFocused();
});

test('an open handover stays open when the band redraws', async ({ page }) => {
  await openDesk(page, bandMocks({ 'POST /api/decisions/DEC-001/resolve': { ok: true } }));
  await serveContinuity(page, (wrote) => (wrote ? withoutDecision : CONTINUITY));
  await page.goto('/#/dashboard');
  const toggle = spine(page, 'Resume').locator('.co-row__toggle').first();
  await toggle.click();
  await expect(page.getByRole('region', { name: 'Handover 2026-10-05-r1' })).toContainText('Cards done');
  await page.locator('.co-decision').getByRole('button', { name: 'Pick option 2' }).click();
  await expect(page.locator('.co-decision')).toHaveCount(0);
  await expect(spine(page, 'Resume').locator('.co-row__toggle').first()).toHaveAttribute('aria-expanded', 'true');
  await expect(page.getByRole('region', { name: 'Handover 2026-10-05-r1' })).toContainText('Cards done');
});

test('a rail with only older items shows its link without saying it is empty', async ({ page }) => {
  const old = CONTINUITY.items.slice(0, 2).map((i, n) => ({ ...i, age_days: 40 + n }));
  await openDesk(page, bandMocks({ '/api/continuity': { items: old } }));
  await page.goto('/#/dashboard');
  const resume = spine(page, 'Resume');
  await expect(resume.locator('a.dk-older')).toHaveText('+2 older');
  await expect(resume.locator('.co-spine__count')).toHaveText('0');
  await expect(resume.locator('.co-spine__empty')).toHaveCount(0);
  await expect(resume).not.toContainText('Nothing here');
});

test('a band that cannot be re-read after a decision says so', async ({ page }) => {
  await openDesk(page, bandMocks({ 'POST /api/decisions/DEC-001/resolve': { ok: true } }));
  await serveContinuity(page, (wrote) => (wrote ? { status: 500, json: { ok: false, error: 'locked' } } : CONTINUITY));
  await page.goto('/#/dashboard');
  await page.locator('.co-decision').getByRole('button', { name: 'Pick option 2' }).focus();
  await page.keyboard.press('Enter');
  const block = band(page).locator('.tm-empty[data-state="error"]');
  await expect(block).toBeVisible();
  await expect(block).toContainText('Could not load what to pick up next');
  await expect(band(page).locator('.co-spine')).toHaveCount(0);
  const retry = block.getByRole('button', { name: 'Try again' });
  await expect(retry).toBeFocused();
  await expect(page.locator('.co-decision')).toHaveCount(0);
});

test('leaving while a handover body loads draws nothing and throws nothing', async ({ page }) => {
  const { errors } = watch(page);
  await openDesk(page, bandMocks());
  let release;
  const held = new Promise((r) => { release = r; });
  await page.route('**/api/handover/2026-10-05-r3', async (route) => { await held; await route.fulfill({ json: { body: 'Late' } }); });
  await page.goto('/#/dashboard');
  await spine(page, 'Resume').locator('.co-row__toggle').nth(2).click();
  await page.evaluate(() => { location.hash = '#/settings'; });
  await expect(page.locator('.dk-continuity')).toHaveCount(0);
  release();
  await page.waitForTimeout(300);
  expect(errors).toEqual([]);
});

test('at 390px the band has 44px controls and nothing scrolls sideways', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await openDesk(page, bandMocks());
  await page.goto('/#/dashboard');
  await expect(page.locator('.co-decision')).toBeVisible();
  const heights = await band(page).locator('.co-row__toggle, .co-decision__opt, .co-decision .btn, .dk-older')
    .evaluateAll((els) => els.map((el) => el.getBoundingClientRect().height));
  expect(heights.length).toBeGreaterThan(8);
  for (const tall of heights) expect(tall).toBeGreaterThanOrEqual(44);
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): the continuity band shows no violations`, async ({ page }) => {
    await openDesk(page, bandMocks({ theme }));
    await page.goto('/#/dashboard');
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.locator('.co-decision')).toBeVisible();
    await spine(page, 'Resume').locator('.co-row__toggle').first().click();
    await expect(page.getByRole('region', { name: 'Handover 2026-10-05-r1' })).toContainText('Cards done');
    await page.evaluate(axeSource);
    const result = await page.evaluate(() => window.axe.run(document.querySelector('.dk-continuity'), { resultTypes: ['violations'] }));
    const found = result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`);
    expect(found.filter((v) => v.startsWith('nested-interactive'))).toEqual([]);
    expect(found).toEqual([]);
  });
}

// Plan 4's accessibility gate reuses dashboardMocks() once per theme; this pins that it loads real content, not a state block.
for (const theme of ['dark', 'light']) {
  test(`dashboard loads its content from dashboardMocks() in ${theme}`, async ({ page }) => {
    await mockApi(page, dashboardMocks({ theme }));
    await page.goto('/#/dashboard');
    await expect(page.locator('.dk-note[data-note-id="NOTE-001"] .dk-note__body')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.locator('.tm-empty[data-state="error"]')).toHaveCount(0);
  });
}

test('leaving with a summary fetch in flight throws nothing and paints nothing into the next screen', async ({ page }) => {
  const errors = [];
  page.on('pageerror', (e) => errors.push(String(e)));
  await mockApi(page, summaryMocks());
  let release;
  const held = new Promise((r) => { release = r; });
  let arrived;
  const asked = new Promise((r) => { arrived = r; });
  await page.route((url) => url.pathname === '/api/issues', async (route) => {
    arrived();
    await held;
    await route.fulfill({ json: summaryMocks()['/api/issues'] }).catch(() => {});
  });
  await page.goto('/#/dashboard');
  await asked;
  await page.evaluate(() => { location.hash = '#/settings'; });
  await expect(page.locator('.set-control[role="group"]').first()).toBeVisible({ timeout: 15_000 });
  const landed = page.waitForResponse((res) => new URL(res.url()).pathname === '/api/issues');
  release();
  await landed;
  // Let the abandoned mount finish its remaining awaits before looking.
  await page.evaluate(() => new Promise((r) => setTimeout(r, 200)));
  await expect(page.locator('#screen-mount .dk-summary, #screen-mount .dk-board, #screen-mount .dk-continuity')).toHaveCount(0);
  await expect(page.locator('#screen-mount .set-control[role="group"]').first()).toBeVisible();
  expect(errors).toEqual([]);
});

test('review and clean-up rows say status and severity as a shape plus a word, never the stored slug', async ({ page }) => {
  await mockApi(page, dashboardMocks({ theme: 'dark' }));
  await page.goto('/#/dashboard');
  const row = (id) => page.locator(`.dk-continuity [data-item-id="${id}"]`);
  await expect(row('T-107').locator('.co-row__next .marker__word')).toHaveText('In review', { timeout: 15_000 });
  await expect(row('T-107').locator('.co-row__next .marker__shape')).toHaveAttribute('data-shape', 'triangle');
  await expect(row('T-106').locator('.co-row__next .marker__word')).toHaveText('In progress');
  await expect(row('ISS-012').locator('.co-row__next .marker__word')).toHaveText(['Medium', 'Open']);
  const text = await page.locator('.dk-continuity').innerText();
  for (const slug of ['in-review', 'in-progress', 'P2 · open']) expect(text).not.toContain(slug);
});

test('an idea row says its status once, as a shape plus a word', async ({ page }) => {
  await mockApi(page, dashboardMocks({ theme: 'dark' }));
  await page.goto('/#/dashboard');
  const idea = page.locator('.dk-continuity [data-item-id="IDEA-7"]');
  await expect(idea.locator('.co-row__next .marker__word')).toHaveText('Brainstorm', { timeout: 15_000 });
  expect((await idea.innerText()).match(/brainstorm/gi)).toHaveLength(1);
});

test('at 390px the "+N older" control of the real dashboard is at least 44px tall', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await mockApi(page, dashboardMocks({ theme: 'light' }));
  await page.goto('/#/dashboard');
  const older = page.locator('.dk-older').first();
  await expect(older).toBeVisible({ timeout: 15_000 });
  expect((await older.boundingBox()).height).toBeGreaterThanOrEqual(44);
});

// Final review: an expanded note stayed in its column — a ~2,400px strip at 1440 (5,000px at 390) beside empty columns,
// overlapping a neighbour. Expanded, it takes the board's full width, sits upright, and nothing intersects it.
for (const width of [1440, 390]) {
  test(`an expanded note spans the notes grid and overlaps no other note (${width})`, async ({ page }) => {
    await page.setViewportSize({ width, height: 900 });
    await openDesk(page, deskRoutes({ notes: { notes: [...NOTES.notes, LONG_NOTE] } }));
    await page.goto('/#/dashboard');
    const card = note(page, 'NOTE-099');
    await expect(card).toBeVisible();
    const bandTop = () => page.locator('.dk-continuity').evaluate((el) => el.getBoundingClientRect().top + scrollY);
    const before = await bandTop();
    await card.getByRole('button', { name: 'Show more' }).click();
    await expect(card).toHaveClass(/is-expanded/);
    const geo = await page.evaluate(() => {
      const box = (el) => { const r = el.getBoundingClientRect(); return { l: r.left, r: r.right, t: r.top, b: r.bottom }; };
      const board = document.querySelector('.dk-board');
      const cs = getComputedStyle(board);
      const inner = box(board);
      inner.l += parseFloat(cs.paddingLeft); inner.r -= parseFloat(cs.paddingRight);
      const me = document.querySelector('.dk-note[data-note-id="NOTE-099"]');
      const others = [...board.querySelectorAll('.dk-note')].filter((n) => n !== me).map(box);
      return { board: inner, me: box(me), others };
    });
    expect(Math.abs(geo.me.l - geo.board.l)).toBeLessThanOrEqual(2);
    expect(Math.abs(geo.me.r - geo.board.r)).toBeLessThanOrEqual(2);
    for (const o of geo.others) {
      const hit = o.l < geo.me.r && o.r > geo.me.l && o.t < geo.me.b && o.b > geo.me.t;
      expect(hit).toBe(false);
    }
    const after = await bandTop();
    expect(after - before).toBeLessThanOrEqual(geo.me.b - geo.me.t);
  });
}

// Final review: nothing said the four count tiles were links — the number carries the link-row underline cue.
test('each count tile is a link whose number is underlined', async ({ page }) => {
  await openDesk(page, summaryMocks());
  await page.goto('/#/dashboard');
  const tiles = page.locator('.dk-summary a.dk-stat');
  await expect(tiles).toHaveCount(4);
  const lines = await tiles.evaluateAll((els) => els.map((a) => getComputedStyle(a.querySelector('.dk-stat__n')).textDecorationLine));
  for (const l of lines) expect(l).toContain('underline');
});
