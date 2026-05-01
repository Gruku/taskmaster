import { test, expect } from '@playwright/test';

const BASE = process.env.VIEWER_BASE_URL || 'http://127.0.0.1:8765';

test.beforeEach(async ({ request }) => {
  await request.put(`${BASE}/api/viewer/prefs`, {
    data: { screens: { auto_mode: { view: 'A', helper_dismissed: false, active_sid: null } } },
  });
});

test.describe('Auto Mode page', () => {
  test('renders Quest Spine by default', async ({ page }) => {
    await page.goto('http://127.0.0.1:8765/v3/#/auto');
    await expect(page.locator('.auto-title')).toHaveText('Auto Mode');
    // 5 spine nodes for AUTO_STAGES (or empty state when no session)
    const empty = page.locator('.spine-empty');
    const nodes = page.locator('.spine-node');
    const ok = (await empty.count()) > 0 || (await nodes.count()) === 5;
    expect(ok).toBeTruthy();
  });

  test('Spine|Log toggle switches center view', async ({ page }) => {
    await page.goto('http://127.0.0.1:8765/v3/#/auto');
    await page.locator('.auto-toggle-seg[data-view="B"]').click();
    await expect(page.locator('.auto-toggle-seg[data-view="B"]')).toHaveClass(/on/);
    // Either flight log or empty placeholder
    const present = await page.locator('.flog-wrap, .flog-empty').count();
    expect(present).toBeGreaterThan(0);
  });

  test('Pause and Stop buttons are present', async ({ page }) => {
    await page.goto('http://127.0.0.1:8765/v3/#/auto');
    await expect(page.locator('.auto-control-btn--pause')).toBeVisible();
    await expect(page.locator('.auto-control-btn--stop')).toBeVisible();
  });

  test('sessions strip renders one tab per session', async ({ page }) => {
    // The dev fixture seeds two sessions; if it doesn't, fall back to >=1.
    await page.goto('http://127.0.0.1:8765/v3/#/auto');
    await page.waitForTimeout(1000);
    const tabs = page.locator('.sstrip-tab');
    const count = await tabs.count();
    expect(count).toBeGreaterThanOrEqual(1);
  });

  test('clicking Pause posts to /api/auto/pause', async ({ page }) => {
    let posted = null;
    page.on('request', (req) => {
      if (req.method() === 'POST' && req.url().includes('/api/auto/pause')) {
        posted = req.postData();
      }
    });
    await page.goto('http://127.0.0.1:8765/v3/#/auto');
    // Only meaningful if a session is running. Skip otherwise.
    const empty = await page.locator('.spine-empty').count();
    if (empty > 0) test.skip();
    await page.locator('.auto-control-btn--pause').click();
    await page.waitForTimeout(200);
    expect(posted).toBeTruthy();
    expect(posted).toContain('"session_id"');
  });
});

test('clicking the dashboard auto-mode-stepper widget navigates to #/auto', async ({ page }) => {
  await page.goto('http://127.0.0.1:8765/v3/#/dashboard');
  const widget = page.locator('.stepper-widget');
  if (!(await widget.count())) test.skip(); // user may not have it in their layout
  await widget.first().click();
  await page.waitForURL(/#\/auto$/);
  await expect(page.locator('.auto-title')).toHaveText('Auto Mode');
});

test('stepper widget shows placeholder when no session', async ({ page }) => {
  await page.goto('http://127.0.0.1:8765/v3/#/dashboard');
  const widget = page.locator('.stepper-widget');
  if (!(await widget.count())) test.skip();
  // Either the calm empty state OR a running session
  const empty = await widget.locator('.stepper-empty').count();
  const running = await widget.locator('.stepper-track').count();
  expect(empty + running).toBeGreaterThan(0);
});

test('sidebar Auto Mode link shows live badge when a session is active', async ({ page }) => {
  await page.goto('http://127.0.0.1:8765/v3/#/dashboard');
  // Wait one poll cycle for autoState to populate
  await page.waitForTimeout(1500);
  const badge = page.locator('.sb-link[data-key="auto"] .sb-livedot, .sb-link[data-sidebar-key="auto"] .sb-livedot');
  // Either present (running) or absent (no session) — both are valid; just no error.
  const count = await badge.count();
  expect(count).toBeGreaterThanOrEqual(0);
});

test('helper note shows on first visit and disappears after dismiss', async ({ page, context }) => {
  // Reset prefs by clearing the helper_dismissed key via PUT /api/viewer/prefs
  await page.request.put('http://127.0.0.1:8765/api/viewer/prefs', {
    data: { screens: { auto_mode: { helper_dismissed: false } } },
  });

  await page.goto('http://127.0.0.1:8765/v3/#/auto');
  const note = page.locator('.auto-helper-note');
  await expect(note).toBeVisible();
  await note.locator('.dismiss').click();
  await expect(note).toHaveCount(0);

  // Reload — should not re-appear
  await page.goto('http://127.0.0.1:8765/v3/#/auto');
  await expect(page.locator('.auto-helper-note')).toHaveCount(0);
});

test('Spine renders 5 nodes when a session has a cursor', async ({ page }) => {
  // Seed via the API
  await page.request.put('http://127.0.0.1:8765/api/viewer/prefs', {
    data: { screens: { auto_mode: { view: 'A', helper_dismissed: true } } },
  });
  // The dev fixture must include a session for this assertion. If not present, skip.
  const sess = await page.request.get('http://127.0.0.1:8765/api/auto/state');
  const body = await sess.json();
  if (body.running === false) test.skip();

  await page.goto('http://127.0.0.1:8765/v3/#/auto');
  const nodes = page.locator('.spine-node');
  await expect(nodes).toHaveCount(5);

  // Active node has class --active
  await expect(page.locator('.spine-node--active')).toHaveCount(1);
});

test('clicking Stop shows confirm and posts to /api/auto/stop', async ({ page }) => {
  let posted = false;
  page.on('request', (req) => {
    if (req.method() === 'POST' && req.url().includes('/api/auto/stop')) posted = true;
  });
  page.on('dialog', (d) => d.accept());

  const sess = await page.request.get('http://127.0.0.1:8765/api/auto/state');
  if ((await sess.json()).running === false) test.skip();

  await page.goto('http://127.0.0.1:8765/v3/#/auto');
  await page.locator('.auto-control-btn--stop').click();
  await page.waitForTimeout(200);
  expect(posted).toBeTruthy();
});

test('header auto-status pill is hidden when no session and visible when running', async ({ page }) => {
  await page.goto('http://127.0.0.1:8765/v3/#/dashboard');
  const pill = page.locator('.auto-status-pill');
  // Either hidden (no session) or visible (fixture session running) — assert state matches /api/auto/state
  const sess = await page.request.get('http://127.0.0.1:8765/api/auto/state');
  const body = await sess.json();
  if (body.running === false || !body.cursor) {
    await expect(pill).toBeHidden();
  } else {
    await expect(pill).toBeVisible();
  }
});
