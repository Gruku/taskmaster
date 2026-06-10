// plugins/taskmaster/viewer/tests/epic-detail.spec.js
import { test, expect } from '@playwright/test';

// Fixture epic used for architecture-map e2e tests.
// Three components with two edges: ingest→thumb, thumb→cdn.
// One unassigned task so the trailing _unassigned bucket is also rendered.
const ARCH_EPIC_FIXTURE = {
  id: 'arch-test',
  name: 'Architecture Test Epic',
  status: 'active',
  design_status: 'exploring',
  description: 'Fixture epic for architecture-map e2e tests.',
  docs: {},
  stats: { total: 4, done: 1 },
  components: {
    ingest: { title: 'Ingest', after: [] },
    thumb:  { title: 'Thumbnailer', after: ['ingest'] },
    cdn:    { title: 'CDN', after: ['thumb'] },
  },
  component_rollup: {
    ingest: { status: 'done',        total: 1, done: 1 },
    thumb:  { status: 'in-progress', total: 2, done: 0 },
    cdn:    { status: 'todo',        total: 0, done: 0 },
    _unassigned: { status: 'todo', total: 1, done: 0 },
  },
  attention: [],
  tasks: [
    { id: 'ING-1', title: 'Decode frames', status: 'done',    component: 'ingest', priority: 'high' },
    { id: 'THM-1', title: 'Resize',        status: 'todo',    component: 'thumb',  priority: 'medium' },
    { id: 'THM-2', title: 'Watermark',     status: 'todo',    component: 'thumb',  priority: 'low' },
    { id: 'X-1',   title: 'Loose task',    status: 'todo',    component: null,     priority: 'low' },
  ],
};

// ARCH_EPIC_FIXTURE has 3 named components + 1 _unassigned bucket = 4 blocks total.
const EXPECTED_BLOCK_COUNT = 4;

test.describe('Epic detail', () => {
  test('route #/epic resolves with page-title Epic', async ({ page }) => {
    const errors = [];
    page.on('pageerror', e => errors.push(String(e)));
    await page.goto('/v3');
    await page.evaluate(() => location.hash = '#/epic/__none__');
    await expect(page.locator('#page-title')).toHaveText('Epic');
    expect(errors).toEqual([]);
  });

  test('unknown epic id shows a not-found state with a back link', async ({ page }) => {
    await page.goto('/v3');
    await page.evaluate(() => location.hash = '#/epic/__definitely_missing__');
    await expect(page.locator('.ed-empty')).toContainText('not found');
    await expect(page.locator('.ed-empty a[href="#/epics"]')).toBeVisible();
  });

  test.describe('Architecture Map (C2)', () => {
    let pageErrors;

    test.beforeEach(async ({ page }) => {
      pageErrors = [];
      page.on('pageerror', e => pageErrors.push(String(e)));
      // Intercept the epic API call and return the fixture so the test is
      // independent of whatever the real backlog contains.
      await page.route('**/api/epic/arch-test', route =>
        route.fulfill({
          status: 200,
          contentType: 'application/json',
          body: JSON.stringify(ARCH_EPIC_FIXTURE),
        })
      );
      await page.goto('/v3');
      await page.evaluate(() => { location.hash = '#/epic/arch-test'; });
      // Gate on the diagram's own output (.cd-map), not just the .ed-diagram
      // section shell — the shell is appended before mountComponentDiagram runs,
      // so waiting on it alone would not catch a mount that bails early.
      await expect(page.locator('.ed-diagram .cd-map')).toBeVisible();
    });

    test.afterEach(() => {
      // No runtime JS errors during any architecture-map interaction
      // (drawEdges runs via requestAnimationFrame after the assertions).
      expect(pageErrors).toEqual([]);
    });

    test('svg.cd-connectors is present inside .ed-diagram', async ({ page }) => {
      await expect(page.locator('.ed-diagram svg.cd-connectors')).toBeVisible();
    });

    test('cd-block count matches fixture component count + unassigned bucket', async ({ page }) => {
      const count = await page.locator('.ed-diagram .cd-block').count();
      expect(count).toBe(EXPECTED_BLOCK_COUNT);
    });

    test('cd-edge paths exist for each after-edge in the fixture (2 edges)', async ({ page }) => {
      // Edges: ingest→thumb and thumb→cdn.  Paths may still have d="" in
      // jsdom/headless where getBoundingClientRect() returns zeros, but the
      // path elements must be present.
      const edgeCount = await page.locator('.ed-diagram path.cd-edge').count();
      expect(edgeCount).toBe(2);
    });

    test('unassigned bucket renders a cd-block--unassigned block', async ({ page }) => {
      await expect(page.locator('.ed-diagram .cd-block--unassigned')).toBeVisible();
    });

    test('.ed-diagram appears in DOM before the side column within the epic detail', async ({ page }) => {
      // The architecture map lives in .ed-main; .ed-side is a sibling that comes
      // after it in the .ed-grid container.  Verify DOM order by comparing the
      // element positions: diagram section offsetTop should be <= side offsetTop.
      const order = await page.evaluate(() => {
        const diagram = document.querySelector('.ed-diagram');
        const side    = document.querySelector('.ed-side');
        if (!diagram || !side) return 'missing';
        // DOCUMENT_POSITION_FOLLOWING (4) is set when the ARGUMENT (side)
        // follows the REFERENCE (diagram) — i.e. diagram precedes side. Good.
        return (diagram.compareDocumentPosition(side) & Node.DOCUMENT_POSITION_FOLLOWING)
          ? 'diagram-before-side'
          : 'diagram-after-side';
      });
      expect(order).toBe('diagram-before-side');
    });
  });
});
