// Route-mocked: thread board renders, copy button present, diary lanes keyed by thread.
//
// Bootstrap: playwright.threads.config.js serves the STATIC viewer (no python
// backlog_server, no port 8765). Every `**/api/**` call is mocked here, so the
// screen renders purely from the fixtures below — independent of live data.
import { test, expect } from '@playwright/test';

const THREADS = [
  { name: 'team-relayout', status: 'open', tldr: 'M1 shipped', next_action: 'start M2',
    task_ids: ['T-1'], branch: 'feat/relayout', last_touched: '2026-07-13T10:00:00+00:00', staleness_days: 0 },
  { name: 'guard-hooks-polish', status: 'parked', tldr: 'awaiting review', next_action: '',
    task_ids: [], branch: '', last_touched: '2026-07-10T10:00:00+00:00', staleness_days: 3 },
];
const SESSIONS = [
  { id: 'team-relayout', kind: 'thread', status: 'open',
    start: '2026-07-12T09:00:00+00:00', end: '2026-07-13T10:00:00+00:00',
    duration: 90000, time_resolution: 'full',
    handover_ids: ['2026-07-13-m1-shipped'],
    handovers: [{ id: '2026-07-13-m1-shipped', status: 'open', viewer_kind: 'checkpoint', tldr: 'M1 shipped' }],
    task_ids: ['T-1'], tldr: 'M1 shipped', next_action: 'start M2' },
];

test.beforeEach(async ({ page }) => {
  // Catch-all first so unrelated boot fetches (identity, prefs, backlog, …) resolve
  // to a harmless empty object; the specific mocks below are registered last and
  // therefore win (Playwright evaluates routes most-recently-added first).
  await page.route('**/api/**', r => r.fulfill({ json: {} }));
  await page.route('**/api/threads', r => r.fulfill({ json: THREADS }));
  await page.route('**/api/sessions', r => r.fulfill({ json: SESSIONS }));
});

test('board shows open thread cards with resume copy', async ({ page }) => {
  await page.goto('/#/sessions');
  const card = page.locator('.thread-card-open');
  await expect(card).toHaveCount(1);
  await expect(card).toContainText('team-relayout');
  await expect(card).toContainText('start M2');
  await expect(card.locator('.tc-copy')).toBeVisible();
});

test('parked threads sit under a fold', async ({ page }) => {
  await page.goto('/#/sessions');
  const fold = page.locator('.tb-parked');
  await expect(fold.locator('summary')).toContainText('1 parked');
});

test('diary lane is keyed by thread name with THREAD label', async ({ page }) => {
  await page.goto('/#/sessions');
  await expect(page.locator('.ho-title', { hasText: 'team-relayout' })).toBeVisible();
  await expect(page.locator('.ho-kind.session').first()).toHaveText('THREAD');
});

test('status chips speak open/closed/superseded', async ({ page }) => {
  await page.goto('/#/sessions');
  const chips = page.locator('[data-role=ho-status] .status-chip');
  await expect(chips).toHaveText([/open/, /closed/, /superseded/]);
});
