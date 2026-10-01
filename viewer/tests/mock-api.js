// User intent: one place to mock the viewer's API so UI specs never touch a live backlog or a fixed port,
// and a spec can never pass while the page writes to an endpoint nobody mocked.
// `table` maps a key to a JSON value, or to { status, json } for non-200 replies.
// A key is a pathname (answers reads: GET/HEAD) or "METHOD pathname" (answers that method only).
// An unmocked read answers {}; an unmocked write answers 501 and is listed by unmockedWrites(page).
const unmocked = new WeakMap();   // page → ["METHOD pathname", …]

export function unmockedWrites(page) {
  return [...(unmocked.get(page) ?? [])];
}

export async function mockApi(page, table = {}) {
  const base = {
    '/api/identity': { version: '0.0.0-test' },
    '/api/viewer/prefs': { theme: 'dark', ui: {}, screens: {} },
    // Boot, navigation and the theme toggle all save prefs.
    'PUT /api/viewer/prefs': {},
    '/api/ideas': { ideas: [] },
  };
  const merged = { ...base, ...table };
  if (!unmocked.has(page)) unmocked.set(page, []);
  await page.route('**/api/**', (route) => {
    const method = route.request().method();
    const { pathname } = new URL(route.request().url());
    const isRead = method === 'GET' || method === 'HEAD';
    const hit = merged[`${method} ${pathname}`] ?? (isRead ? merged[pathname] : undefined);
    if (hit === undefined && !isRead) {
      unmocked.get(page).push(`${method} ${pathname}`);
      return route.fulfill({ status: 501, json: { ok: false, error: `unmocked ${method} ${pathname}` } });
    }
    if (hit && typeof hit === 'object' && 'status' in hit && 'json' in hit) {
      return route.fulfill({ status: hit.status, json: hit.json });
    }
    return route.fulfill({ json: hit === undefined ? {} : hit });
  });
}
