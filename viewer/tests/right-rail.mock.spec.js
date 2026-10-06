// User intent: the generic right rail must behave in a real browser — a panel in the page (no floating sheet, no
// shadow) that takes focus to its heading and gives it back, fits a phone — and a handover status change the server
// refuses is said in words beside the pill, never as a status code, a URL or the server's internals.
import { test, expect } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { createRequire } from 'node:module';
import { mockApi, unmockedWrites } from './mock-api.js';
import {
  BOARD, DETAIL_TASK, RICH_RELATED, taskDetail, SESSIONS, SESSION_DETAILS, THREADS,
} from './mock-fixtures.js';

const axeSource = readFileSync(createRequire(import.meta.url).resolve('axe-core/axe.min.js'), 'utf8');

const HO = '2026-09-30-kanban-reskin';
const STATUS_URL = `**/api/handover/${HO}/status`;

export const railMocks = ({ theme = 'dark' } = {}) => ({
  '/api/viewer/prefs': { theme, ui: {}, screens: {} },
  '/api/board': BOARD, '/api/backlog': BOARD,
  '/api/task/T-102/detail': taskDetail(DETAIL_TASK, 't1:fixture', RICH_RELATED),
  '/api/bugs': [],
  '/api/sessions': SESSIONS,
  '/api/threads': THREADS,
  '/api/sessions/team-relayout': SESSION_DETAILS['team-relayout'],
});

test.beforeEach(async ({ page }) => {
  await page.emulateMedia({ reducedMotion: 'reduce' });
});
test.afterEach(async ({ page }) => { expect(unmockedWrites(page)).toEqual([]); });

// The status POST answered here, not by mockApi, so a test can change the answer midway.
async function answerStatus(page, reply) {
  await page.unroute(STATUS_URL);
  await page.route(STATUS_URL, (route) => route.fulfill(reply));
}

async function taskPage(page, theme = 'dark') {
  await mockApi(page, railMocks({ theme }));
  await page.goto('/#/task/T-102');
  await expect(page.locator('.td-doc--page .td-handover')).toBeVisible();
  return page.locator('.td-doc--page .ho-status-pill');
}

async function choose(page, pill, word) {
  await pill.click();
  await page.locator('.ho-status-menu').getByRole('menuitemradio', { name: word }).click();
}

async function sessionsRail(page, theme = 'dark') {
  await mockApi(page, railMocks({ theme }));
  await page.goto('/#/sessions');
  await page.locator('.ho-child[data-handover-id="2026-07-13-m1-shipped"]').click();
  const rail = page.locator('[data-role=rail-host] > aside#right-rail');
  await expect(rail).toBeVisible();
  return rail;
}

test('a failed handover status change is said in words beside the pill', async ({ page }) => {
  const pill = await taskPage(page);
  await answerStatus(page, { status: 500, json: { ok: false, error: 'sqlite3.OperationalError: database is locked' } });
  await choose(page, pill, 'closed');

  const alert = page.locator('.td-doc--page .ho-status-pill + .ho-status-error[role="alert"]');
  await expect(alert).toHaveText('The server could not save this change. Try again in a moment.');
  await expect(pill).toHaveAttribute('aria-describedby', await alert.getAttribute('id'));
  await expect(pill).toHaveAttribute('data-status', 'open');
  await expect(pill.locator('.ho-status-pill__word')).toHaveText('open');
  const text = await page.locator('#screen-mount').textContent();
  for (const raw of ['sqlite3', '500', '/api']) expect(text, `no "${raw}" on the page`).not.toContain(raw);

  await answerStatus(page, { json: { ok: true } });
  await choose(page, pill, 'closed');
  await expect(pill).toHaveAttribute('data-status', 'closed');
  await expect(pill.locator('.ho-status-pill__word')).toHaveText('closed');
  await expect(page.locator('.ho-status-error')).toHaveCount(0);
  await expect(pill).not.toHaveAttribute('aria-describedby', /.+/);
});

test('a refusal gives the server\'s reason', async ({ page }) => {
  const pill = await taskPage(page);
  await answerStatus(page, { status: 409, json: { ok: false, error: 'Handover is already superseded by 2026-10-02-wrap' } });
  await choose(page, pill, 'closed');
  await expect(page.locator('.td-doc--page .ho-status-pill + .ho-status-error'))
    .toHaveText('Handover is already superseded by 2026-10-02-wrap');
  await expect(pill).toHaveAttribute('data-status', 'open');
});

// Measured on a one-word reason: a long sentence fills its row whatever the rules, a short one only when they hold.
test('even a one-word refusal is a full-width line of its own under the pill', async ({ page }) => {
  const pill = await taskPage(page);
  await answerStatus(page, { status: 409, json: { ok: false, error: 'No.' } });
  await choose(page, pill, 'closed');
  const alert = page.locator('.td-doc--page .ho-status-pill + .ho-status-error[role="alert"]');
  await expect(alert).toHaveText('No.');
  const geo = await alert.evaluate((el) => {
    const row = el.parentElement;
    const cs = getComputedStyle(row);
    const inner = row.getBoundingClientRect().width - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight)
      - parseFloat(cs.borderLeftWidth) - parseFloat(cs.borderRightWidth);
    const box = el.getBoundingClientRect();
    return { width: box.width, inner, top: box.top, pillBottom: el.previousElementSibling.getBoundingClientRect().bottom };
  });
  expect(Math.abs(geo.width - geo.inner), 'as wide as its row').toBeLessThanOrEqual(1);
  expect(geo.top, 'below the pill').toBeGreaterThanOrEqual(geo.pillBottom);
});

test('the sessions rail is a panel in the page', async ({ page }) => {
  const rail = await sessionsRail(page);
  const style = await rail.evaluate((el) => {
    const cs = getComputedStyle(el);
    return { position: cs.position, boxShadow: cs.boxShadow };
  });
  expect(style).toEqual({ position: 'static', boxShadow: 'none' });
  const title = rail.locator('h2.rr-title');
  await expect(title).toHaveText('M1 shipped');
  await expect(title).toBeFocused();
  await expect(rail).toHaveAttribute('aria-labelledby', await title.getAttribute('id'));
  await expect(rail.getByRole('button', { name: 'Copy path .taskmaster/handovers/2026-07-13-m1-shipped.md', exact: true }))
    .toBeVisible();
  await expect(rail.locator('a.rr-task[href="#/task/T-102"]')).toBeVisible();
  // Eight files shown, the rest counted; the long path keeps its words in its title.
  await expect(rail.locator('.rr-files > li')).toHaveCount(9);
  await expect(rail.locator('.rr-files > li').last()).toHaveText('+ 2 more');
  const longPath = SESSION_DETAILS['team-relayout'].handovers[1].files_touched.find((f) => f.length === 140);
  const longRow = rail.locator('.rr-files > li').nth(5).locator('span');
  await expect(longRow).toHaveAttribute('title', longPath);
  await expect(longRow).toHaveText(longPath);

  await page.keyboard.press('Escape');
  await expect(page.locator('#right-rail')).toHaveCount(0);
});

test('the sessions rail fits a phone screen', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const rail = await sessionsRail(page);
  const fit = await rail.evaluate((el) => ({
    right: el.getBoundingClientRect().right,
    innerWidth,
    scrollWidth: document.documentElement.scrollWidth,
  }));
  expect(fit.right).toBeLessThanOrEqual(fit.innerWidth);
  expect(fit.scrollWidth).toBeLessThanOrEqual(fit.innerWidth);
  // Touch targets: every control in the rail is at least 44px tall.
  const short = await rail.evaluate((el) => [...el.querySelectorAll('button, .ho-status-pill')]
    .filter((b) => b.getBoundingClientRect().height < 44).map((b) => b.className));
  expect(short).toEqual([]);
});

test('a session opens its own rail, and a handover listed there opens the handover', async ({ page }) => {
  await mockApi(page, railMocks());
  await page.goto('/#/sessions');
  await page.locator('.ho[data-session-id="team-relayout"]').click();
  const rail = page.locator('[data-role=rail-host] > aside#right-rail.right-rail--session');
  await expect(rail.locator('h2.rr-title')).toHaveText('M1 shipped');
  await expect(rail.locator('.rr-slug')).toHaveText('team-relayout');
  const hoBtn = rail.locator('button.rr-ho[data-handover-id="2026-07-12-scope"]');
  await expect(hoBtn).toContainText('Mid-task');
  await expect(hoBtn).not.toContainText('Scope the relayout');
  await hoBtn.click();
  const hoRail = page.locator('aside#right-rail.right-rail--handover');
  await expect(hoRail.locator('h2.rr-title')).toHaveText('Scope the relayout');
  await expect(hoRail.locator('.rr-h .ho-status-pill')).toHaveAttribute('data-status', 'closed');
  await hoRail.locator('.rr-close').click();
  await expect(page.locator('#right-rail')).toHaveCount(0);
});

async function axe(page, scope) {
  await page.evaluate(axeSource);
  const result = await page.evaluate((sel) => window.axe.run(document.querySelector(sel), {
    resultTypes: ['violations'],
  }), scope);
  return result.violations.map((v) => `${v.id}: ${v.nodes.map((n) => n.target.join(' ')).join(' | ')}`);
}

for (const theme of ['dark', 'light']) {
  test(`axe (${theme}): the sessions rail`, async ({ page }) => {
    await sessionsRail(page, theme);
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    expect(await axe(page, '#right-rail')).toEqual([]);
  });

  test(`axe (${theme}): the task page's rail with the status menu open`, async ({ page }) => {
    const pill = await taskPage(page, theme);
    await expect(page.locator('html')).toHaveAttribute('data-theme', theme);
    await pill.click();
    await expect(page.locator('.ho-status-menu')).toBeVisible();
    expect(await axe(page, 'body')).toEqual([]);
  });
}
