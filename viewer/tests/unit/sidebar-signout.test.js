// User intent: a signed-in member can sign out from the sidebar footer in server mode only, and a failed sign-out is never shown as a success.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { JSDOM } from 'jsdom';

function boot({ serverMode, fetchImpl }) {
  const meta = serverMode ? '<meta name="taskmaster-sign-out" content="/logout"><meta name="taskmaster-sign-in" content="/login">' : '';
  const dom = new JSDOM(`<!doctype html><html><head>${meta}</head><body><div class="shell"><aside id="sidebar"></aside><div class="main"></div></div></body></html>`,
    { url: 'http://127.0.0.1:7471/' });
  const assigns = [];
  const fakeWindow = {
    fetch: fetchImpl ?? (async () => ({ status: 200, url: 'http://127.0.0.1:7471/login' })),
    location: { href: 'http://127.0.0.1:7471/', origin: 'http://127.0.0.1:7471', assign: (u) => assigns.push(u) },
  };
  globalThis.window = fakeWindow;
  globalThis.document = dom.window.document;
  return { dom, assigns };
}

const { mountSidebar } = await import('../../js/components/sidebar.js');
const store = { subscribe: () => () => {}, getIdentity: () => null };

test('solo mode: no Sign out control renders', () => {
  const { dom } = boot({ serverMode: false });
  mountSidebar(dom.window.document.getElementById('sidebar'), { store, prefs: null });
  assert.equal(dom.window.document.querySelector('.sidebar-signout'), null);
  assert.equal(dom.window.document.querySelector('.sidebar-footer'), null);
});

test('server mode: a footer after the nav holds a Sign out button with an accessible name and a live region', () => {
  const { dom } = boot({ serverMode: true });
  const side = dom.window.document.getElementById('sidebar');
  mountSidebar(side, { store, prefs: null });
  const footer = side.querySelector(':scope > .sidebar-footer');
  assert.ok(footer && footer.previousElementSibling.matches('nav.sidebar-nav'));
  const btn = footer.querySelector('button.btn.btn--ghost.btn--sm.sidebar-signout');
  assert.ok(btn);
  assert.equal(btn.type, 'button');
  assert.equal(btn.textContent.trim(), 'Sign out');
  const live = footer.querySelector('[aria-live]');
  assert.ok(live);
  assert.equal(live.textContent, '');
});

test('clicking Sign out POSTs and lands on the sign-in location', async () => {
  const calls = [];
  const { dom, assigns } = boot({ serverMode: true, fetchImpl: async (url, init) => { calls.push([url, init.method]); return { status: 200, url: 'http://127.0.0.1:7471/login' }; } });
  mountSidebar(dom.window.document.getElementById('sidebar'), { store, prefs: null });
  dom.window.document.querySelector('.sidebar-signout').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(calls, [['/logout', 'POST']]);
  assert.deepEqual(assigns, ['/login']);
});

test('a failed sign-out shows the message in the live region and does not navigate', async () => {
  const { dom, assigns } = boot({ serverMode: true, fetchImpl: async () => ({ status: 403, url: 'http://127.0.0.1:7471/logout' }) });
  mountSidebar(dom.window.document.getElementById('sidebar'), { store, prefs: null });
  dom.window.document.querySelector('.sidebar-signout').click();
  await new Promise((r) => setTimeout(r, 10));
  assert.deepEqual(assigns, []);
  assert.equal(dom.window.document.querySelector('.sidebar-footer [aria-live]').textContent, 'Sign out failed. Reload and try again.');
});
