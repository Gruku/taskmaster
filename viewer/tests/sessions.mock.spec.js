// User intent: the Sessions screen in a real browser — chips are buttons that filter and remember, rows open the rail
// by keyboard and give focus back, a refused status change is said in words, thread cards are links with a separate
// copy button, search hides rather than dims, leaving leaves nothing behind, thirty long sessions fit a phone, and
// neither theme has an axe violation.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import { sessionsMocks, manySessions } from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

const M1 = '2026-07-13-m1-shipped';
const STATUS_URL = `**/api/handover/${M1}/status`;

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

async function boot(page, { theme = 'dark', width = 1440, height = 900, hash = '#/sessions', wait = true, ...overrides } = {}) {
  await page.setViewportSize({ width, height });
  await mockApi(page, { ...sessionsMocks({ theme }), ...overrides });
  await page.goto(`/${hash}`);
  await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
  if (wait) await expect(page.locator(`.ho-child[data-handover-id="${M1}"]`)).toBeVisible();
}

const search = (page) => page.getByRole('textbox', { name: 'Search sessions' });
const chip = (page, group, name) => page.getByRole('group', { name: group }).getByRole('button', { name: new RegExp(`^${name}`) });
const rail = (page) => page.locator('[data-role=rail-host] > aside#right-rail');
const hoRow = (page, id) => page.locator(`.ho-child[data-handover-id="${id}"]`);
const sessionRow = (page, id) => page.locator(`.ho[data-session-id="${id}"]`);

async function axe(page, scope, rules) {
  await page.evaluate(axeSource);
  const result = await page.evaluate(([sel, only]) => window.axe.run(document.querySelector(sel), {
    resultTypes: ['violations'], ...(only ? { runOnly: { type: 'rule', values: only } } : {}),
  }), [scope, rules]);
  return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`);
}

test('chips are buttons and filter the timeline', async ({ page }) => {
  await boot(page);
  const filters = page.locator('.sessions-page > .sessions-filters');
  await expect(page.locator('.sessions-page > :first-child')).toHaveClass(/sessions-filters/);
  await expect(filters.getByRole('group', { name: 'Show' })).toBeVisible();
  await expect(filters.getByRole('group', { name: 'Status' })).toBeVisible();

  const superseded = chip(page, 'Status', 'Superseded');
  await expect(superseded).toHaveAttribute('aria-pressed', 'false');
  await expect(page.locator('.ho-child', { hasText: 'Old hook plan' })).toHaveCount(0);

  const saved = page.waitForRequest((req) => req.method() === 'PUT' && new URL(req.url()).pathname === '/api/viewer/prefs'
    && (req.postDataJSON()?.screens?.sessions?.handoverStatus || []).includes('superseded'));
  await superseded.click({ modifiers: ['Shift'] });
  await expect(superseded).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.ho-child', { hasText: 'Old hook plan' })).toHaveCount(1);
  await saved;

  await chip(page, 'Show', 'Handovers').click();
  await expect(page.locator('.ho-child')).toHaveCount(0);
  await expect(page.locator('#topbar-count')).toHaveText('2 threads · 3 handovers');

  await search(page).fill('scope');
  await expect(page.locator('#topbar-count')).toHaveText('2 threads · 3 handovers · 1 visible');
  await expect(page.locator('.ho')).toHaveCount(1);
});

test('keyboard: a row opens the rail, Escape closes it and focus returns to the row', async ({ page }) => {
  await boot(page);
  await search(page).focus();
  const target = hoRow(page, M1);
  let reached = false;
  for (let i = 0; i < 20 && !reached; i++) {
    await page.keyboard.press('Tab');
    const visible = await page.evaluate(() => {
      const el = document.activeElement;
      const box = el?.getBoundingClientRect();
      return !!el && el !== document.body && box.width > 0 && box.height > 0 && getComputedStyle(el).visibility !== 'hidden';
    });
    expect(visible, `Tab ${i + 1} lands on something visible`).toBe(true);
    reached = await target.evaluate((el) => el === document.activeElement);
  }
  expect(reached, 'the handover row is reached by Tab').toBe(true);

  await page.keyboard.press('Enter');
  const title = rail(page).getByRole('heading', { name: 'M1 shipped' });
  await expect(title).toBeFocused();
  await expect(target).toHaveAttribute('aria-current', 'true');

  await page.keyboard.press('Escape');
  await expect(page.locator('#right-rail')).toHaveCount(0);
  await expect(target).toBeFocused();
  await expect(target).not.toHaveAttribute('aria-current', /.*/);
});

test('keyboard: a session row opened with Enter gets focus back when Escape closes its rail', async ({ page }) => {
  await boot(page);
  const row = sessionRow(page, 'team-relayout');
  await row.focus();
  await page.keyboard.press('Enter');
  await expect(rail(page).locator('h2.rr-title')).toBeFocused();
  await expect(row).toHaveAttribute('aria-current', 'true');
  await expect(page.locator('#screen-mount [aria-controls="right-rail"]')).toHaveCount(4);
  await page.keyboard.press('Escape');
  await expect(page.locator('#right-rail')).toHaveCount(0);
  await expect(row).toBeFocused();
  await expect(page.locator('#screen-mount [aria-current]')).toHaveCount(0);
  await expect(page.locator('#screen-mount [aria-controls]')).toHaveCount(0);
});

test('the mark moves with the rail: a handover opened from a session\'s rail is the marked row', async ({ page }) => {
  await boot(page);
  await sessionRow(page, 'team-relayout').click();
  await expect(sessionRow(page, 'team-relayout')).toHaveAttribute('aria-current', 'true');
  await rail(page).locator(`button.rr-ho[data-handover-id="${M1}"]`).click();
  await expect(rail(page).locator('h2.rr-title')).toHaveText('M1 shipped');
  await expect(hoRow(page, M1)).toHaveAttribute('aria-current', 'true');
  await expect(page.locator('#screen-mount [aria-current]')).toHaveCount(1);
  // The timeline is never dimmed or made unclickable while the rail is open.
  await hoRow(page, '2026-07-12-scope').click();
  await expect(rail(page).locator('h2.rr-title')).toHaveText('Scope the relayout');
  await expect(hoRow(page, '2026-07-12-scope')).toHaveAttribute('aria-current', 'true');
  expect(await page.locator('.tl').evaluate((el) => getComputedStyle(el).opacity)).toBe('1');
});

// The opener and the screen's fallback must point at different rows, or the test cannot tell them apart: the handover
// rail inherits the session row as its opener, while the fallback would focus the handover's own row.
test('Escape on a handover opened from a session\'s rail gives focus to the session row, not the handover row', async ({ page }) => {
  await boot(page);
  const row = sessionRow(page, 'team-relayout');
  await row.click();
  await rail(page).locator(`button.rr-ho[data-handover-id="${M1}"]`).click();
  await expect(rail(page).locator('h2.rr-title')).toHaveText('M1 shipped');
  await expect(hoRow(page, M1)).toHaveAttribute('aria-current', 'true');
  await page.keyboard.press('Escape');
  await expect(page.locator('#right-rail')).toHaveCount(0);
  await expect(row).toBeFocused();
  await expect(hoRow(page, M1)).not.toBeFocused();
});

test('a late detail for an earlier click does not open over the row clicked after it', async ({ page }) => {
  await boot(page);
  const later = sessionRow(page, 'team-relayout');
  const earlier = sessionRow(page, 'guard-hooks-polish');
  await later.click(); // its detail is now cached
  await expect(rail(page).locator('h2.rr-title')).toHaveText('M1 shipped');

  let release;
  const held = new Promise((r) => { release = r; });
  let asked;
  const requested = new Promise((r) => { asked = r; });
  await page.route('**/api/sessions/guard-hooks-polish', async (route) => { asked(); await held; await route.fallback(); });
  await earlier.click();
  await requested;
  await later.click();
  await expect(later).toHaveAttribute('aria-current', 'true');

  const answered = page.waitForResponse('**/api/sessions/guard-hooks-polish');
  release();
  await answered;
  await page.evaluate(() => new Promise((r) => requestAnimationFrame(() => setTimeout(r, 50))));
  await expect(rail(page).locator('h2.rr-title')).toHaveText('M1 shipped');
  await expect(later).toHaveAttribute('aria-current', 'true');
  await expect(earlier).not.toHaveAttribute('aria-current', /.*/);
});

test('Show chips that hide every session say so in words, with one way back', async ({ page }) => {
  await boot(page);
  await chip(page, 'Show', 'Handovers').click();
  await chip(page, 'Show', 'Threads').click();
  const empty = page.locator('.sessions-mount > .tm-empty');
  await expect(empty.locator('.tm-empty__label')).toHaveText('Filters');
  await expect(empty.locator('.tm-empty__headline')).toHaveText('The Show filters hide every session');
  await expect(empty.getByRole('button')).toHaveCount(1);
  await expect(page.locator('.sessions-mount .tl')).toHaveCount(0);

  await empty.getByRole('button', { name: 'Show everything' }).click();
  await expect(chip(page, 'Show', 'Threads')).toHaveAttribute('aria-pressed', 'true');
  await expect(chip(page, 'Show', 'Handovers')).toHaveAttribute('aria-pressed', 'true');
  await expect(chip(page, 'Show', 'Threads')).toBeFocused();
  await expect(hoRow(page, M1)).toBeVisible();
});

test('the sessions rail says a failed status change in words', async ({ page }) => {
  await boot(page, { [`POST /api/handover/${M1}/status`]: { status: 500, json: { ok: false, error: 'sqlite3.OperationalError: database is locked' } } });
  await hoRow(page, M1).click();
  const pill = rail(page).locator('.ho-status-pill');
  await pill.click();
  await page.locator('.ho-status-menu').getByRole('menuitemradio', { name: 'closed' }).click();
  await expect(rail(page).locator('.ho-status-pill + .ho-status-error[role="alert"]'))
    .toHaveText('The server could not save this change. Try again in a moment.');
  await expect(pill.locator('.ho-status-pill__word')).toHaveText('open');
  await expect(hoRow(page, M1).locator('.ho-status')).toHaveText('Open');
});

test('a status change the server takes is the timeline\'s too', async ({ page }) => {
  await boot(page, { [`POST /api/handover/${M1}/status`]: { ok: true } });
  await hoRow(page, M1).click();
  await rail(page).locator('.ho-status-pill').click();
  await page.locator('.ho-status-menu').getByRole('menuitemradio', { name: 'closed' }).click();
  await expect(hoRow(page, M1).locator('.ho-status')).toHaveText('Closed');
  await expect(chip(page, 'Status', 'Closed').locator('.chip__count')).toHaveText('2');
  await expect(hoRow(page, M1)).toHaveAttribute('aria-current', 'true');
});

test('a thread card is a link and its copy button is not', async ({ page }) => {
  await boot(page);
  const card = page.locator('.thread-card-open');
  await expect(card).toHaveCount(1);
  // The link's hit area covers the card, so a click on its tldr is the link's.
  const box = await card.locator('.tc-tldr').boundingBox();
  await page.mouse.click(box.x + box.width / 2, box.y + box.height / 2);
  await expect.poll(() => page.evaluate(() => location.hash)).toBe('#/sessions/team-relayout');
  await expect(rail(page).locator('h2.rr-title')).toHaveText('M1 shipped');
  await expect(sessionRow(page, 'team-relayout')).toHaveAttribute('aria-current', 'true');

  await page.evaluate(() => { location.hash = '#/sessions'; });
  await expect(page.locator('#right-rail')).toHaveCount(0);
  await page.getByRole('button', { name: 'Copy resume line for team-relayout' }).click();
  expect(await page.evaluate(() => location.hash)).toBe('#/sessions');
  expect(await axe(page, '#screen-mount', ['nested-interactive'])).toEqual([]);
});

test('the route opens a session; an id with no session opens nothing', async ({ page }) => {
  await boot(page, { hash: '#/sessions/team-relayout' });
  await expect(rail(page).locator('h2.rr-title')).toHaveText('M1 shipped');
  await page.keyboard.press('Escape');
  await expect(page.locator('#right-rail')).toHaveCount(0);
  // Opened by the route, it had no opener: its row takes focus.
  await expect(sessionRow(page, 'team-relayout')).toBeFocused();

  await page.evaluate(() => { location.hash = '#/sessions/no-such-thread'; });
  await expect(page.locator('.ho[data-session-id="team-relayout"]')).toBeVisible();
  await expect(page.locator('#right-rail')).toHaveCount(0);
});

test('search hides what does not match and offers to clear it', async ({ page }) => {
  await boot(page);
  await search(page).fill('zzz');
  await expect(page.locator('.tm-empty__headline')).toHaveText('No sessions match your search');
  await expect(page.locator('.ho')).toHaveCount(0);
  await page.locator('#screen-mount').getByRole('button', { name: 'Clear search' }).click();
  await expect(page.locator('.ho')).toHaveCount(2);
  await expect(search(page)).toBeFocused();
  await expect(search(page)).toHaveValue('');
});

test('no sessions is said in words', async ({ page }) => {
  await boot(page, { '/api/sessions': [], '/api/threads': [], wait: false });
  await expect(page.locator('.tm-empty__headline')).toHaveText('No sessions yet');
  await expect(page.locator('.tm-empty__label')).toHaveText('Sessions');
  await expect(page.locator('.tm-empty__hint')).toHaveText('Sessions appear here as you start and end your work cycles.');
});

test('sessions that cannot be loaded are said in words', async ({ page }) => {
  await boot(page, { '/api/sessions': { status: 500, json: { ok: false, error: 'boom' } }, wait: false });
  await expect(page.locator('.tm-empty__headline')).toHaveText('Sessions could not be loaded.');
  await expect(page.locator('.tm-empty__label')).toHaveText('Error');
  await expect(page.locator('#screen-mount')).not.toContainText('boom');
});

// Polled: with reduced motion, tokens.css gives every element a 0.01ms transition, and `order` and the grid's columns
// are animatable — a read in the same frame as the change still sees the old value.
test('the rail sits beside the timeline on a wide screen and above it on a narrow one', async ({ page }) => {
  const layout = () => page.evaluate(() => {
    const host = document.querySelector('[data-role=rail-host]').getBoundingClientRect();
    const list = document.querySelector('[data-role=mount]').getBoundingClientRect();
    return {
      side: host.left >= list.right, above: host.bottom <= list.top,
      columns: getComputedStyle(document.querySelector('.sessions-body')).gridTemplateColumns.split(' ').length,
    };
  });
  await boot(page);
  await hoRow(page, M1).click();
  await expect(rail(page)).toBeVisible();
  await expect.poll(layout, 'rail to the right of the timeline').toEqual({ side: true, above: false, columns: 2 });
  const host = page.locator('[data-role=rail-host]');
  const box = await host.evaluate((el) => ({ width: el.getBoundingClientRect().width, position: getComputedStyle(el).position }));
  expect(box.width).toBeGreaterThanOrEqual(320);
  expect(box.width).toBeLessThanOrEqual(420);
  expect(box.position).toBe('sticky');

  // Resized with the rail open, the layout follows both ways.
  await page.setViewportSize({ width: 900, height: 900 });
  await expect.poll(layout, 'one column, the rail above the timeline').toEqual({ side: false, above: true, columns: 1 });
  await page.setViewportSize({ width: 1440, height: 900 });
  await expect.poll(layout).toEqual({ side: true, above: false, columns: 2 });

  // Closed, the timeline takes the whole width again.
  await page.keyboard.press('Escape');
  await expect(page.locator('#right-rail')).toHaveCount(0);
  await expect.poll(() => page.locator('.sessions-body').evaluate((el) => getComputedStyle(el).gridTemplateColumns.split(' ').length)).toBe(1);
});

test('leaving with the rail and its status menu open leaves nothing behind', async ({ page }) => {
  await boot(page);
  await hoRow(page, M1).click();
  await rail(page).locator('.ho-status-pill').click();
  await expect(page.locator('.ho-status-menu')).toBeVisible();
  await page.evaluate(() => { location.hash = '#/settings'; });
  await expect(page.locator('#right-rail')).toHaveCount(0);
  await expect(page.locator('.popover')).toHaveCount(0);
  await expect.poll(() => page.evaluate(() => import('/js/components/popover.js').then((m) => m.openPopoverCount()))).toBe(0);
  await page.keyboard.press('Escape');
});

test('leaving within the search debounce leaves the next screen\'s count alone', async ({ page }) => {
  await boot(page);
  await search(page).press('t');
  await page.evaluate(() => { location.hash = '#/settings'; });
  await page.waitForTimeout(400);
  await expect(page.locator('#topbar-count')).toHaveText('');
});

test('thirty sessions with long slugs stay inside a phone screen', async ({ page }) => {
  const { sessions, details } = manySessions(30);
  await boot(page, {
    width: 390, height: 844, wait: false,
    '/api/sessions': sessions,
    ...Object.fromEntries(Object.entries(details).map(([id, d]) => [`/api/sessions/${id}`, d])),
  });
  await expect(page.locator('.ho')).toHaveCount(30);
  const fit = await page.evaluate(() => ({ scrollWidth: document.documentElement.scrollWidth, innerWidth }));
  expect(fit.scrollWidth).toBeLessThanOrEqual(fit.innerWidth);
  const cut = await page.locator('.ho-title, .ho-slug').evaluateAll((els) => els
    .filter((el) => el.scrollWidth > el.clientWidth + 1 && el.title !== el.textContent).map((el) => el.textContent));
  expect(cut).toEqual([]);
  const short = await page.locator('.sessions-page .chip, .sessions-page .overflow-more, .ho, .ho-child, .tc-copy')
    .evaluateAll((els) => els.filter((el) => el.getClientRects().length && el.getBoundingClientRect().height < 44)
      .map((el) => el.className));
  expect(short).toEqual([]);
});

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): the screen with the rail closed, then open, and the topbar`, async ({ page }) => {
    await boot(page, { theme });
    expect(await axe(page, '#screen-mount')).toEqual([]);
    await hoRow(page, M1).click();
    await expect(rail(page)).toBeVisible();
    expect(await axe(page, '#screen-mount')).toEqual([]);
    expect(await axe(page, '#topbar')).toEqual([]);
  });
}

// Plan 4's accessibility gate reuses sessionsMocks() once per theme; this pins that it loads real content, not a state block.
for (const theme of ['dark', 'light']) {
  test(`sessions loads its content from sessionsMocks() in ${theme}`, async ({ page }) => {
    await mockApi(page, sessionsMocks({ theme }));
    await page.goto('/#/sessions');
    await expect(page.locator('.ho-child[data-handover-id="2026-07-13-m1-shipped"]')).toBeVisible({ timeout: 15_000 });
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await expect(page.locator('.tm-empty[data-state="error"]')).toHaveCount(0);
  });
}
