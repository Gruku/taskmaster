import { test, expect } from '@playwright/test';

const BASE = process.env.VIEWER_BASE_URL || 'http://127.0.0.1:8765';

// ── Fixture helpers ──────────────────────────────────────────────────────────
// There is no POST /api/decisions endpoint — decisions are created only via MCP.
// We seed via page.route() intercepts so every test is self-contained and
// deterministic regardless of the real project's decision state.

const SEED_DECISION_ITEM = {
  id: 'DEC-e2e-001',
  type: 'decision',
  title: 'Use TypeScript in the viewer',
  action_class: 'decide',
  timestamp: new Date(Date.now() - 2 * 60 * 60 * 1000).toISOString(), // 2h ago
};

const SEED_DECISION_DOC = {
  id: 'DEC-e2e-001',
  title: 'Use TypeScript in the viewer',
  status: 'open',
  options: ['Keep JS modules', 'Migrate to TypeScript', 'Use JSDoc types'],
  recommendation: 2,
};

/** Intercept /api/continuity with a single open decision as hero. */
function seedDecision(page) {
  page.route('**/api/continuity**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [SEED_DECISION_ITEM] }),
    }),
  );
  page.route('**/api/decisions/DEC-e2e-001', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(SEED_DECISION_DOC),
    }),
  );
}

/** Intercept /api/continuity with an empty items array (no decisions). */
function seedEmpty(page) {
  page.route('**/api/continuity**', (route) =>
    route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify({ items: [] }),
    }),
  );
}

// ── Tests ────────────────────────────────────────────────────────────────────

test.describe('continuity dashboard', () => {
  test('route mounts and default view is Action', async ({ page }) => {
    seedEmpty(page);
    await page.goto(`${BASE}/v3#/v2/dashboard`);
    // Root screen element mounts.
    await expect(page.locator('.co-dash')).toBeVisible();
    // View switcher is rendered in the topbar slot (#topbar-actions).
    await expect(page.locator('.co-view-switcher')).toBeVisible();
    // Action button is active by default.
    await expect(page.locator('.co-view-switcher__btn.is-active')).toContainText('Action');
  });

  test('hero surfaces an open decision card', async ({ page }) => {
    seedDecision(page);
    await page.goto(`${BASE}/v3#/v2/dashboard`);
    // Decision title renders in the hero.
    await expect(page.locator('.co-decision__title')).toBeVisible();
    await expect(page.locator('.co-decision__title')).toContainText('TypeScript');
  });

  test('view switcher flips between Action / Time / Entity', async ({ page }) => {
    // This test is independent of decision data; seed empty so it works on any backlog.
    seedEmpty(page);
    await page.goto(`${BASE}/v3#/v2/dashboard`);
    // Action is active on load.
    await expect(page.locator('.co-view-switcher__btn.is-active')).toContainText('Action');

    // Switch to Time.
    await page.locator('.co-view-switcher__btn', { hasText: 'Time' }).click();
    await expect(page.locator('.co-view-switcher__btn.is-active')).toContainText('Time');
    // Time view wrapper mounts.
    await expect(page.locator('.co-time-view')).toBeVisible();

    // Switch to Entity.
    await page.locator('.co-view-switcher__btn', { hasText: 'Entity' }).click();
    await expect(page.locator('.co-view-switcher__btn.is-active')).toContainText('Entity');
    // Entity view wrapper mounts.
    await expect(page.locator('.co-entity-view')).toBeVisible();
  });

  test('resolving a decision via the primary CTA refreshes the hero', async ({ page }) => {
    seedDecision(page);

    // Track resolve POST.
    let resolveBody = null;
    await page.route('**/api/decisions/DEC-e2e-001/resolve', async (route) => {
      resolveBody = await route.request().postDataJSON().catch(() => null);
      await route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ ok: true, id: 'DEC-e2e-001', status: 'resolved' }),
      });
    });

    // After resolve, the screen re-fetches /api/continuity and gets an empty list.
    let continuityCallCount = 0;
    await page.route('**/api/continuity**', (route) => {
      continuityCallCount += 1;
      // Second call (after resolve) returns empty — hero should show empty state.
      const items = continuityCallCount > 1 ? [] : [SEED_DECISION_ITEM];
      route.fulfill({
        status: 200,
        contentType: 'application/json',
        body: JSON.stringify({ items }),
      });
    });

    await page.goto(`${BASE}/v3#/v2/dashboard`);
    await expect(page.locator('.co-decision__title')).toBeVisible();

    // Click the primary CTA ("Pick option 2" — the recommendation).
    await page.locator('.co-decision__primary').click();

    // Resolve request fired with correct option index.
    await expect.poll(() => resolveBody?.resolved_with).toBe(2);

    // Hero refreshes — decision card is gone, empty state appears.
    await expect(page.locator('.co-hero--empty')).toBeVisible({ timeout: 5000 });
    await expect(page.locator('.co-decision__title')).toHaveCount(0);
  });
});
