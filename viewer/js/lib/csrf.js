// User intent: in server mode every viewer write carries the session's CSRF token, a lost session returns to the sign-in page the server names, and a member can sign out; in solo mode this does nothing.
// The sign-in and sign-out locations come from <meta> tags the server writes (028 section 3.4), so swapping the server's
// authentication changes no JavaScript.
const HEADER = 'X-Taskmaster-CSRF';

function metaValue(doc, name) {
  return doc.querySelector(`meta[name="${name}"]`)?.content ?? null;
}

export function signInUrl(doc) {
  return metaValue(doc, 'taskmaster-sign-in') || '/login';
}

// True when the page came from a server-mode viewer: it names where signing out goes.
export function serverMode(doc) {
  return !!metaValue(doc, 'taskmaster-sign-out');
}

export function installCsrf(doc, win) {
  const token = metaValue(doc, 'taskmaster-csrf');
  if (token === null && !serverMode(doc)) return false;
  const original = win.fetch.bind(win);
  win.fetch = async (input, init = {}) => {
    const isReq = typeof win.Request === 'function' && input instanceof win.Request;
    const method = String(init.method || (isReq ? input.method : 'GET')).toUpperCase();
    const url = new URL(
      typeof input === 'string' || input instanceof URL ? String(input) : input.url,
      win.location.href,
    );
    const sameOrigin = url.origin === win.location.origin;
    if (token !== null && method !== 'GET' && method !== 'HEAD' && sameOrigin) {
      const headers = new Headers(init.headers !== undefined ? init.headers : (isReq ? input.headers : undefined));
      headers.set(HEADER, token);
      init = { ...init, headers };
    }
    const resp = await original(input, init);
    if (resp.status === 401 && sameOrigin) win.location.assign(signInUrl(doc));
    return resp;
  };
  return true;
}

// Resolves true once the server has signed this member out and the page is on its way to the sign-in location.
// Anything else (a refusal, a network error, landing somewhere unexpected) resolves false and navigates nowhere,
// so a failed sign-out is never shown as a success.
export async function signOut(win, doc = win.document) {
  const out = metaValue(doc, 'taskmaster-sign-out');
  if (!out) return false;
  const signIn = signInUrl(doc);
  let resp;
  try {
    resp = await win.fetch(out, { method: 'POST' });
  } catch {
    return false;
  }
  const landed = new URL(resp.url || out, win.location.href);
  const expected = new URL(signIn, win.location.href);
  if (!resp.status || resp.status >= 400 || landed.pathname !== expected.pathname) return false;
  win.location.assign(signIn);
  return true;
}
