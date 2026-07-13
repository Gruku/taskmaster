// Route-mocked handover-status coverage — rewritten from the dead todo/in-progress/done
// vocabulary (pre-Task-7) to the open/closed/superseded reality.
//
// Bootstrap: playwright.threads.config.js serves the STATIC viewer and every
// `**/api/**` call is mocked here (no live backlog_server, no port 8765 — the
// ISS-025 landmine). The handover rail opens off the mocked /api/sessions/<id>
// detail, so the status pill + override menu are exercised without live data.
import { test, expect } from '@playwright/test';

const HID = '2026-07-13-m1-shipped';

const SESSIONS = [
  { id: 'team-relayout', kind: 'thread', status: 'open',
    start: '2026-07-12T09:00:00+00:00', end: '2026-07-13T10:00:00+00:00',
    duration: 90000, time_resolution: 'full',
    handover_ids: [HID],
    handovers: [{ id: HID, status: 'open', viewer_kind: 'checkpoint', tldr: 'M1 shipped' }],
    task_ids: ['T-1'], tldr: 'M1 shipped', next_action: 'start M2' },
];

const SESSION_DETAIL = {
  session: SESSIONS[0],
  handovers: [
    { id: HID, status: 'open', viewer_kind: 'checkpoint', tldr: 'M1 shipped',
      created: '2026-07-13T10:00:00+00:00',
      done_items: ['landed M1'], open_items: ['start M2'],
      task_ids: ['T-1'], files_touched: [], next_action: 'start M2',
      resume_prompt: 'Resume team-relayout: start M2' },
  ],
};

async function mockApi(page) {
  // Catch-all first (harmless {} for boot fetches); specific mocks registered
  // last so they win — Playwright evaluates most-recently-added routes first.
  await page.route('**/api/**', r => r.fulfill({ json: {} }));
  await page.route('**/api/threads', r => r.fulfill({ json: [] }));
  await page.route('**/api/sessions', r => r.fulfill({ json: SESSIONS }));
  await page.route('**/api/sessions/*', r => r.fulfill({ json: SESSION_DETAIL }));
}

async function openHandoverRail(page) {
  await page.goto('/#/sessions');
  await expect(page.locator('.sessions-page')).toBeVisible();
  const hoChild = page.locator('.ho-child').first();
  await expect(hoChild).toBeVisible();
  await hoChild.click();
  await expect(page.locator('.right-rail')).toBeVisible();
}

test.describe('handover status pill', () => {
  test.beforeEach(async ({ page }) => { await mockApi(page); });

  test('renders open / closed / superseded with a distinct status class', async ({ page }) => {
    await openHandoverRail(page);
    const pill = page.locator('.ho-status-pill').first();
    await expect(pill).toBeVisible();
    const cls = await pill.getAttribute('class');
    const valid = ['ho-status-pill-open', 'ho-status-pill-closed', 'ho-status-pill-superseded'];
    expect(valid.some(c => cls?.includes(c))).toBe(true);
  });

  test('clicking the pill opens an override menu with the three statuses', async ({ page }) => {
    await openHandoverRail(page);
    await page.locator('.ho-status-pill').first().click();
    const menu = page.locator('.ho-status-menu');
    await expect(menu).toBeVisible();
    await expect(menu.getByText('open', { exact: true })).toBeVisible();
    await expect(menu.getByText('closed', { exact: true })).toBeVisible();
    await expect(menu.getByText('superseded', { exact: true })).toBeVisible();
  });

  test('selecting a status POSTs {status} to /api/handover/*/status', async ({ page }) => {
    let captured = null;
    await page.route('**/api/handover/*/status', (route) => {
      captured = route.request().postDataJSON();
      route.fulfill({ status: 200, body: JSON.stringify({ ok: true, status: 'closed' }) });
    });
    await openHandoverRail(page);
    await page.locator('.ho-status-pill').first().click();
    await page.locator('.ho-status-menu').getByText('closed', { exact: true }).click();
    expect(captured).toBeTruthy();
    expect(captured.status).toBe('closed');
  });
});

test.describe('handover status filter chips', () => {
  test.beforeEach(async ({ page }) => { await mockApi(page); });

  test('default to open + closed visible, superseded hidden', async ({ page }) => {
    await page.goto('/#/sessions');
    const chips = page.locator('.handover-status-chips .status-chip');
    await expect(chips.filter({ hasText: 'open' })).toHaveClass(/on/);
    await expect(chips.filter({ hasText: 'closed' })).toHaveClass(/on/);
    await expect(chips.filter({ hasText: 'superseded' })).not.toHaveClass(/on/);
  });

  test('toggling the superseded chip activates it', async ({ page }) => {
    await page.goto('/#/sessions');
    const superseded = page.locator('.handover-status-chips .status-chip', { hasText: 'superseded' });
    await superseded.click();
    await expect(superseded).toHaveClass(/on/);
  });
});
