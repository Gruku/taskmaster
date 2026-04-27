// Hash-based router. Hashes look like:
//   #/dashboard
//   #/kanban?epic=auth&phase=2
//   #/task/T-148
//   #/recap/SES-0184

const screens = new Map();   // path-prefix → loader (() => Promise<module>)
let currentCleanup = null;
let mountEl = null;
let topbarEl = null;
let titleEl = null;          // overridable via init({ titleEl })
let injectDeps = null;       // { store, api, prefs }
let navSeq = 0;              // monotonic counter; stale navigations check seq === navSeq

export function registerScreen(prefix, loader) {
  screens.set(prefix, loader);
}

export function init({ mount, topbar, deps, titleEl: titleElOverride }) {
  mountEl = mount;
  topbarEl = topbar;
  injectDeps = deps;
  titleEl = titleElOverride || topbar.querySelector('#page-title');
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
  if (!mountEl) throw new Error('router.go() called before router.init()');
  const seq = ++navSeq;

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
  if (seq !== navSeq) return; // stale — a newer navigation started

  mountEl.replaceChildren();

  let mod;
  try {
    mod = await match();
  } catch (e) {
    mountEl.innerHTML = `<div class="stub">Failed to load screen: ${matchPrefix}<div class="stub-meta">${e.message}</div></div>`;
    return;
  }
  if (seq !== navSeq) return; // stale

  titleEl.textContent = mod.meta?.title || matchPrefix;
  // Pass remaining path segments after the prefix as `subpath` (e.g. /task/T-148 → ['T-148']).
  const subSegments = segments.slice(matchPrefix.split('/').filter(Boolean).length);
  const cleanup = await mod.mount(mountEl, {
    params,
    subpath: subSegments,
    ...injectDeps,
  });
  if (seq !== navSeq) return; // stale
  currentCleanup = cleanup;

  // Notify sidebar to update active state.
  document.dispatchEvent(new CustomEvent('route:changed', { detail: { path, params, sidebarKey: mod.meta?.sidebarKey } }));
}

export function navigate(hash) {
  if (!mountEl) throw new Error('router.navigate() called before router.init()');
  if (!hash.startsWith('#')) hash = '#' + hash;
  if (location.hash === hash) go();
  else location.hash = hash;
}
