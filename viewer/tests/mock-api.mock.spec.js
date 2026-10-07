// User intent: a spec must not pass while the page writes to an endpoint nobody mocked — the mock itself has to make that visible.
import { test, expect } from '@playwright/test';
import { mockApi, unmockedReads, unmockedWrites } from './mock-api.js';

const call = (page, method, path) => page.evaluate(async ([m, p]) => {
  const r = await fetch(p, { method: m, headers: { 'Content-Type': 'application/json' }, body: m === 'GET' ? undefined : '{}' });
  return { status: r.status, json: await r.json() };
}, [method, path]);

async function booted(page) {
  await page.goto('/#/settings');
  await expect(page.locator('#sidebar .sidebar-link').first()).toBeVisible();
}

test('an unmocked write fails with 501 and is recorded; an unmocked read still answers {}', async ({ page }) => {
  await mockApi(page);
  await booted(page);
  expect(await call(page, 'GET', '/api/nobody/mocked')).toEqual({ status: 200, json: {} });
  expect(unmockedWrites(page)).toEqual([]);
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
    expect((await call(page, method, '/api/nobody/mocked')).status, method).toBe(501);
  }
  expect(unmockedWrites(page)).toEqual([
    'POST /api/nobody/mocked', 'PUT /api/nobody/mocked', 'PATCH /api/nobody/mocked', 'DELETE /api/nobody/mocked',
  ]);
});

test('a path mocked for reading is not thereby mocked for writing', async ({ page }) => {
  await mockApi(page, { '/api/ideas': { ideas: [{ id: 'I-1' }] } });
  await booted(page);
  expect((await call(page, 'GET', '/api/ideas')).json).toEqual({ ideas: [{ id: 'I-1' }] });
  expect((await call(page, 'POST', '/api/ideas')).status).toBe(501);
  expect(unmockedWrites(page)).toEqual(['POST /api/ideas']);
});

test('a write mocked by "METHOD path" answers and is not recorded', async ({ page }) => {
  await mockApi(page, {
    'POST /api/ideas': { ok: true, id: 'I-2' },
    'DELETE /api/ideas': { status: 409, json: { ok: false } },
  });
  await booted(page);
  expect(await call(page, 'POST', '/api/ideas')).toEqual({ status: 200, json: { ok: true, id: 'I-2' } });
  expect(await call(page, 'DELETE', '/api/ideas')).toEqual({ status: 409, json: { ok: false } });
  expect(unmockedWrites(page)).toEqual([]);
});

test('saving viewer prefs is mocked by default, and stays mocked when the read is overridden', async ({ page }) => {
  await mockApi(page, { '/api/viewer/prefs': { theme: 'light', ui: {}, screens: {} } });
  await booted(page);
  expect(await call(page, 'PUT', '/api/viewer/prefs')).toEqual({ status: 200, json: {} });
  expect((await call(page, 'GET', '/api/viewer/prefs')).json.theme).toBe('light');
  expect(unmockedWrites(page)).toEqual([]);
});

test('a read nobody mocked is listed once by unmockedReads; mocked reads are not', async ({ page }) => {
  await mockApi(page, { '/api/ideas': { ideas: [] } });
  await booted(page);
  await call(page, 'GET', '/api/ideas');
  await call(page, 'GET', '/api/nobody/mocked');
  await call(page, 'GET', '/api/nobody/mocked');
  const reads = unmockedReads(page);
  expect(reads.filter((r) => r === 'GET /api/nobody/mocked')).toHaveLength(1);
  expect(reads).not.toContain('GET /api/ideas');
});

test('unmockedReads is empty when every read the page makes was mocked', async ({ page }) => {
  // A first run lists what the settings screen reads; a second page with all of those mocked lists nothing.
  await mockApi(page);
  await booted(page);
  await page.waitForLoadState('networkidle');
  const reads = unmockedReads(page);
  expect(reads.length).toBeGreaterThan(0);
  const fresh = await page.context().newPage();
  await mockApi(fresh, Object.fromEntries(reads.map((r) => [r.slice(r.indexOf(' ') + 1), {}])));
  await booted(fresh);
  await fresh.waitForLoadState('networkidle');
  expect(unmockedReads(fresh)).toEqual([]);
  await fresh.close();
});
