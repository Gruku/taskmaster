// User intent: one place to mock the viewer's API so UI specs never touch a live backlog or a fixed port.
// `table` maps a pathname to a JSON value, or to { status, json } for non-200 replies.
export async function mockApi(page, table = {}) {
  const base = {
    '/api/identity': { version: '0.0.0-test' },
    '/api/viewer/prefs': { theme: 'system', ui: {}, screens: {} },
  };
  const merged = { ...base, ...table };
  await page.route('**/api/**', (route) => {
    const { pathname } = new URL(route.request().url());
    const hit = merged[pathname];
    if (hit && typeof hit === 'object' && 'status' in hit && 'json' in hit) {
      return route.fulfill({ status: hit.status, json: hit.json });
    }
    return route.fulfill({ json: hit === undefined ? {} : hit });
  });
}
