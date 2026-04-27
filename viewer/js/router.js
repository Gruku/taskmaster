// Hash-based router. Hashes look like:
//   #/dashboard
//   #/kanban?epic=auth&phase=2
//   #/task/T-148
//   #/recap/SES-0184

const screens = new Map();   // path-prefix → loader (() => Promise<module>)
let currentCleanup = null;
let mountEl = null;
let topbarEl = null;
let injectDeps = null;       // { store, api, prefs }

export function registerScreen(prefix, loader) {
  screens.set(prefix, loader);
}

export function init({ mount, topbar, deps }) {
  mountEl = mount;
  topbarEl = topbar;
  injectDeps = deps;
  window.addEventListener('hashchange', go);
  if (!location.hash || location.hash === '#') location.hash = '#/dashboard';
  else go();
}

function parseHash() {
  const raw = (location.hash || '').replace(/^#\/?/, '');
  if (!raw) return { path: '', params: {}, segments: [] };
  const [pathPart, query] = raw.split('?', 2);
  const segments = pathPart.split('/').filter(Boolean);
  const path = '/' + segments.join('/');
  const params = {};
  if (query) {
    for (const pair of query.split('&')) {
      const [k, v=''] = pair.split('=');
      params[decodeURIComponent(k)] = decodeURIComponent(v);
    }
  }
  return { path, params, segments };
}

async function go() {
  const { path, params, segments } = parseHash();
  // Find the longest matching prefix.
  let match = null, matchPrefix = '';
  for (const prefix of screens.keys()) {
    if (path === prefix || path.startsWith(prefix + '/')) {
      if (prefix.length > matchPrefix.length) { matchPrefix = prefix; match = screens.get(prefix); }
    }
  }
  if (!match) {
    location.hash = '#/dashboard';
    return;
  }

  if (typeof currentCleanup === 'function') {
    try { await currentCleanup(); } catch (e) { console.error('cleanup error', e); }
    currentCleanup = null;
  }
  mountEl.replaceChildren();

  const mod = await match();
  topbarEl.querySelector('#page-title').textContent = mod.meta?.title || matchPrefix;
  // Pass remaining path segments after the prefix as `subpath` (e.g. /task/T-148 → ['T-148']).
  const subSegments = segments.slice(matchPrefix.split('/').filter(Boolean).length);
  const cleanup = await mod.mount(mountEl, {
    params,
    subpath: subSegments,
    ...injectDeps,
  });
  currentCleanup = cleanup;

  // Notify sidebar to update active state.
  document.dispatchEvent(new CustomEvent('route:changed', { detail: { path, params, sidebarKey: mod.meta?.sidebarKey } }));
}

export function navigate(hash) {
  if (!hash.startsWith('#')) hash = '#' + hash;
  if (location.hash === hash) go();
  else location.hash = hash;
}
