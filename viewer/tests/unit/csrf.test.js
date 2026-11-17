// User intent: the CSRF fetch wrapper adds its header only to same-origin writes, only when the page carries a token, and sends a lost session to /login.
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { installCsrf } from '../../js/lib/csrf.js';

const ORIGIN = 'http://127.0.0.1:7471';

function setup({ token = 'tok-1', status = 200 } = {}) {
  const calls = [];
  const assigns = [];
  const fetchFn = async (input, init) => { calls.push({ input, init }); return { status }; };
  const win = {
    fetch: fetchFn,
    Request,
    location: { href: `${ORIGIN}/`, origin: ORIGIN, assign: (u) => assigns.push(u) },
  };
  const doc = { querySelector: (sel) => (token !== null && sel === 'meta[name="taskmaster-csrf"]' ? { content: token } : null) };
  return { win, doc, calls, assigns, fetchFn };
}
const hdr = (call, name) => new Headers(call.init.headers).get(name);

test('no meta: installCsrf returns false and win.fetch is the same function', () => {
  const { win, doc, fetchFn } = setup({ token: null });
  assert.equal(installCsrf(doc, win), false);
  assert.equal(win.fetch, fetchFn);
});

test('adds the header to same-origin POST, PUT, PATCH and DELETE, keeping existing headers', async () => {
  const { win, doc, calls } = setup();
  assert.equal(installCsrf(doc, win), true);
  for (const method of ['POST', 'PUT', 'PATCH', 'DELETE']) {
    await win.fetch('/api/x', { method, headers: { 'If-Match': '"e1"' } });
    await win.fetch('/api/x', { method: method.toLowerCase(), headers: new Headers({ 'If-Match': '"e2"' }) });
    await win.fetch('/api/x', { method });
  }
  assert.equal(calls.length, 12);
  for (const c of calls) assert.equal(hdr(c, 'X-Taskmaster-CSRF'), 'tok-1');
  assert.equal(hdr(calls[0], 'If-Match'), '"e1"');
  assert.equal(hdr(calls[1], 'If-Match'), '"e2"');
});

test('does not add it to GET or HEAD, or to cross-origin URLs', async () => {
  const { win, doc, calls } = setup();
  installCsrf(doc, win);
  await win.fetch('/api/x');
  await win.fetch('/api/x', { method: 'GET' });
  await win.fetch('/api/x', { method: 'HEAD' });
  await win.fetch('https://other.example/api', { method: 'POST' });
  await win.fetch('http://127.0.0.1:9999/api', { method: 'POST' });
  for (const c of calls) assert.equal(c.init?.headers === undefined ? null : hdr(c, 'X-Taskmaster-CSRF'), null);
});

test('a Request input keeps its own headers and method', async () => {
  const { win, doc, calls } = setup();
  installCsrf(doc, win);
  const mk = () => new Request(`${ORIGIN}/api/x`, {
    method: 'PUT',
    headers: { 'If-Match': '"e1"', 'Content-Type': 'application/json' },
  });
  await win.fetch(mk());
  assert.equal(hdr(calls[0], 'If-Match'), '"e1"');
  assert.equal(hdr(calls[0], 'Content-Type'), 'application/json');
  assert.equal(hdr(calls[0], 'X-Taskmaster-CSRF'), 'tok-1');
  assert.equal(calls[0].input.method, 'PUT');

  // init.headers wins over the Request's own headers
  await win.fetch(mk(), { headers: { 'If-Match': '"e9"' } });
  assert.equal(hdr(calls[1], 'If-Match'), '"e9"');
  assert.equal(hdr(calls[1], 'Content-Type'), null);
  assert.equal(hdr(calls[1], 'X-Taskmaster-CSRF'), 'tok-1');
});

test('a 401 on a same-origin request calls location.assign("/login"); another origin does not', async () => {
  const { win, doc, assigns } = setup({ status: 401 });
  installCsrf(doc, win);
  await win.fetch('/api/x');
  assert.deepEqual(assigns, ['/login']);
  await win.fetch('https://other.example/api');
  assert.deepEqual(assigns, ['/login']);
});

// --- sign-in and sign-out locations, and signing out (028 section 3.4) ---------------------------

import { serverMode, signOut, signInUrl } from '../../js/lib/csrf.js';

function metas(map) {
  return { querySelector: (sel) => {
    const m = /meta\[name="([^"]+)"\]/.exec(sel);
    return m && map[m[1]] !== undefined ? { content: map[m[1]] } : null;
  } };
}
function outWin({ status = 200, url = `${ORIGIN}/login`, throws = false } = {}) {
  const calls = [];
  const assigns = [];
  const win = {
    fetch: async (input, init) => { calls.push({ input, init }); if (throws) throw new Error('down'); return { status, url }; },
    Request,
    location: { href: `${ORIGIN}/`, origin: ORIGIN, assign: (u) => assigns.push(u) },
  };
  return { win, calls, assigns };
}

test('serverMode is true only with the sign-out meta', () => {
  assert.equal(serverMode(metas({})), false);
  assert.equal(serverMode(metas({ 'taskmaster-csrf': 't' })), false);
  assert.equal(serverMode(metas({ 'taskmaster-sign-out': '/logout' })), true);
});

test('signOut POSTs the sign-out meta URL, then goes to the sign-in meta URL when it landed there', async () => {
  const { win, calls, assigns } = outWin({ url: `${ORIGIN}/x-in` });
  const doc = metas({ 'taskmaster-sign-out': '/x-out', 'taskmaster-sign-in': '/x-in' });
  assert.equal(await signOut(win, doc), true);
  assert.equal(calls[0].input, '/x-out');
  assert.equal(calls[0].init.method, 'POST');
  assert.deepEqual(assigns, ['/x-in']);
});

test('signOut defaults to /login when only the sign-out meta is present, and has no hard-coded /logout', async () => {
  const { win, calls, assigns } = outWin();
  assert.equal(await signOut(win, metas({ 'taskmaster-sign-out': '/o' })), true);
  assert.equal(calls[0].input, '/o');
  assert.deepEqual(assigns, ['/login']);
  assert.equal(signInUrl(metas({})), '/login');
});

test('a failed sign-out (403, a network error, or landing elsewhere) does not navigate and reports false', async () => {
  for (const opts of [{ status: 403, url: `${ORIGIN}/logout` }, { throws: true }, { url: `${ORIGIN}/somewhere` }]) {
    const { win, assigns } = outWin(opts);
    assert.equal(await signOut(win, metas({ 'taskmaster-sign-out': '/logout', 'taskmaster-sign-in': '/login' })), false, JSON.stringify(opts));
    assert.deepEqual(assigns, []);
  }
});

test('a 401 goes to the sign-in meta URL, not a hard-coded /login', async () => {
  const { win, assigns } = setup({ status: 401 });
  const doc = metas({ 'taskmaster-csrf': 'tok', 'taskmaster-sign-in': '/portal/in' });
  installCsrf(doc, win);
  await win.fetch('/api/x');
  assert.deepEqual(assigns, ['/portal/in']);
});

test('the wrapper installs with only a sign-out meta (no CSRF token) and then adds no header', async () => {
  const { win, calls } = setup();
  assert.equal(installCsrf(metas({ 'taskmaster-sign-out': '/logout' }), win), true);
  await win.fetch('/api/x', { method: 'POST' });
  assert.equal(calls[0].init === undefined || new Headers(calls[0].init.headers).get('X-Taskmaster-CSRF') === null, true);
});
