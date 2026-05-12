import { test, expect } from '@playwright/test';

const BASE = 'http://127.0.0.1:8765';

test.beforeEach(async ({ request }) => {
  // Reset issues prefs slice so view toggle tests don't bleed.
  await request.put(`${BASE}/api/viewer/prefs`, {
    data: { screens: { issues: { view: 'A' } } },
  });
});

test('issues screen renders Investigating + Open columns', async ({ page }) => {
  await page.goto('/v3/#/issues');
  await expect(page.locator('.issues')).toBeVisible();
  await expect(page.locator('.issues__column-header').filter({ hasText: 'Investigating' })).toBeVisible();
  await expect(page.locator('.issues__column-header').filter({ hasText: 'Open' })).toBeVisible();
});

test('issue card shows severity word, not P0/P1', async ({ page }) => {
  await page.goto('/v3/#/issues');
  const sevChips = page.locator('.issue-card__sev-chip');
  const count = await sevChips.count();
  for (let i = 0; i < count; i++) {
    const text = await sevChips.nth(i).textContent();
    expect(text.trim()).toMatch(/^(Critical|High|Medium|Low)$/);
  }
});

test('repro block expands on click', async ({ page }) => {
  await page.goto('/v3/#/issues');
  const repro = page.locator('.issue-card__repro').first();
  await expect(repro).toBeVisible();
  await expect(repro).not.toHaveAttribute('open', '');
  await repro.locator('summary').click();
  await expect(repro).toHaveAttribute('open', '');
});

test('resolved shelf collapsed by default and expandable', async ({ page }) => {
  await page.goto('/v3/#/issues');
  const list = page.locator('.issues__resolved-list');
  await expect(list).toBeHidden();
  await page.locator('.issues__resolved-header').click();
  await expect(list).toBeVisible();
});

test('issue card surfaces all §3.14 elements', async ({ page }) => {
  await page.goto('/v3/#/issues');
  const card = page.locator('.issue-card').first();
  // severity hexagon glyph
  await expect(card.locator('.sev-glyph svg')).toBeVisible();
  // console-style location
  await expect(card.locator('.issue-card__location')).toBeVisible();
  await expect(card.locator('.issue-card__location-num')).toBeVisible();
  // italic-serif symptom
  const symptomFont = await card.locator('.issue-card__symptom').evaluate(el => getComputedStyle(el).fontStyle);
  expect(symptomFont).toBe('italic');
  // aging bar
  await expect(card.locator('.aging-bar')).toBeVisible();
  await expect(card.locator('.aging-bar__chip')).toHaveText(/^(Fresh|Aging|Stale)$/);
});

test('blocks chip appears when issue blocks non-done tasks', async ({ page }) => {
  await page.goto('/v3/#/issues');
  const chip = page.locator('.issue-card__blocks').first();
  if (await chip.count() === 0) test.skip();
  await expect(chip).toHaveText(/⊘ blocks \d+/);
});

test('issue card click navigates to issue detail', async ({ page }) => {
  await page.goto('/v3/#/issues');
  const card = page.locator('.issue-card').first();
  const id = await card.getAttribute('data-issue-id');
  await card.locator('.issue-card__title').click();
  await page.waitForURL(`**/#/issue/${id}`);
  await expect(page.locator('.issue-detail .id-title')).toBeVisible();
  await expect(page.locator('.issue-detail .id-id')).toHaveText(id);
});

test('issue card task pill click does NOT trigger card-level navigation', async ({ page }) => {
  await page.goto('/v3/#/issues');
  const pill = page.locator('.issue-card__task-pill').first();
  if (await pill.count() === 0) test.skip();
  const tid = (await pill.textContent()).trim();
  await pill.click();
  await expect(page).toHaveURL(new RegExp(`#/task/${tid}`));
});

test('issue search filters cards by title', async ({ page }) => {
  await page.goto('/v3/#/issues');
  await expect(page.locator('.issue-card').first()).toBeVisible();
  const initial = await page.locator('.issue-card').count();
  expect(initial).toBeGreaterThan(0);
  const firstTitle = (await page.locator('.issue-card__title').first().textContent()).trim();
  const probe = firstTitle.split(/\s+/).find(w => w.length > 4) || firstTitle.slice(0, 5);
  await page.locator('.tm-search input').fill(probe);
  await page.waitForTimeout(300);
  const filtered = await page.locator('.issue-card').count();
  expect(filtered).toBeLessThanOrEqual(initial);
  expect(filtered).toBeGreaterThan(0);
  await expect(page.locator('.tm-subcount')).toHaveText(/of \d+ issues/);
});

test('issue component chip filters cards', async ({ page }) => {
  await page.goto('/v3/#/issues');
  await expect(page.locator('.issue-card').first()).toBeVisible();
  const chips = page.locator('.issues__comp-chip');
  expect(await chips.count()).toBeGreaterThan(0);
  await expect(chips.filter({ hasText: 'viewer' })).toBeVisible();
  const totalBefore = await page.locator('.issue-card').count();
  // Click a non-viewer chip (e.g., 'server') so we get fewer cards
  await chips.filter({ hasText: 'server' }).click();
  await page.waitForTimeout(80);
  const filtered = await page.locator('.issue-card').count();
  expect(filtered).toBeLessThan(totalBefore);
  expect(filtered).toBeGreaterThan(0);
  await expect(page.locator('.tm-subcount')).toHaveText(/of \d+ issues/);
});

test('issue search + component chip combine via AND', async ({ page }) => {
  await page.goto('/v3/#/issues');
  await expect(page.locator('.issue-card').first()).toBeVisible();
  await page.locator('.issues__comp-chip').filter({ hasText: 'viewer' }).click();
  await page.waitForTimeout(80);
  const afterChip = await page.locator('.issue-card').count();
  expect(afterChip).toBeGreaterThan(0);
  await page.locator('.tm-search input').fill('zzznoSuchIssue');
  await page.waitForTimeout(300);
  const afterBoth = await page.locator('.issue-card').count();
  expect(afterBoth).toBe(0);
  await expect(page.locator('.tm-subcount')).toHaveText(/0 of \d+ issues/);
});

test('issue detail back link returns to issues list', async ({ page }) => {
  await page.goto('/v3/#/issues');
  const id = await page.locator('.issue-card').first().getAttribute('data-issue-id');
  await page.goto(`/v3/#/issue/${id}`);
  await expect(page.locator('.issue-detail')).toBeVisible();
  await page.locator('.issue-detail .id-back').click();
  await page.waitForURL('**/#/issues');
  await expect(page.locator('.issues')).toBeVisible();
});

test('view B renders 4 status kanban columns', async ({ page }) => {
  await page.goto('/v3/#/issues');
  await page.getByRole('button', { name: 'Status' }).click();
  // After switching to view B, the kanban shell is visible and status columns
  // are un-hidden. Severity columns are hidden (display:none), so :visible gives us 4.
  const visibleCols = page.locator('.issues__kanban-col').filter({ visible: true });
  await expect(visibleCols).toHaveCount(4);
  // Each expected status key is represented by exactly one column.
  for (const key of ['open', 'investigating', 'fixed', 'wontfix']) {
    await expect(page.locator(`.issues__kanban-col[data-key="${key}"]`)).toBeVisible();
  }
});

test('view D renders 4 severity kanban columns and suppresses sev chip on cards', async ({ page }) => {
  await page.goto('/v3/#/issues');
  await page.getByRole('button', { name: 'Severity' }).click();
  // Four severity columns should be visible; status columns are hidden.
  const visibleCols = page.locator('.issues__kanban-col').filter({ visible: true });
  await expect(visibleCols).toHaveCount(4);
  // Column names match the four severity labels.
  const colNames = page.locator('.issues__kanban-col:visible .issues__column-name');
  await expect(colNames.filter({ hasText: 'Critical' })).toHaveCount(1);
  await expect(colNames.filter({ hasText: 'High' })).toHaveCount(1);
  await expect(colNames.filter({ hasText: 'Medium' })).toHaveCount(1);
  await expect(colNames.filter({ hasText: 'Low' })).toHaveCount(1);
  // Cards in view D should not carry .issue-card__sev-chip (suppressSeverityChip=true).
  const chipsInKanban = page.locator('.issues__columns--kanban .issue-card__sev-chip');
  await expect(chipsInKanban).toHaveCount(0);
});

test('severity "All" chip resets active filters', async ({ page }) => {
  await page.goto('/v3/#/issues');
  // Activate the Critical filter chip.
  await page.locator('.issues__sev-chip[data-sev="Critical"]').click();
  await expect(page.locator('.issues__sev-chip[data-sev="Critical"]')).toHaveAttribute('aria-pressed', 'true');
  // Click "All" to clear all active severity filters.
  await page.locator('.issues__sev-chip--all').click();
  await expect(page.locator('.issues__sev-chip--all')).toHaveAttribute('aria-pressed', 'true');
  await expect(page.locator('.issues__sev-chip[data-sev="Critical"]')).toHaveAttribute('aria-pressed', 'false');
});
