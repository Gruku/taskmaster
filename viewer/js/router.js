// Hash-based router. Hashes look like:
//   #/dashboard
//   #/kanban?epic=auth&phase=2
//   #/task/T-148

import { claimTopbar } from './lib/topbar.js';
import { stateBlock } from './components/empty-state.js';

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

// What the mount shows when a screen cannot be loaded or opened: words, never the error (it goes to the console).
function failureBlock() {
  return stateBlock({ state: 'error', label: 'Could not open', headline: 'This screen could not be opened.',
    hint: 'Reload the page. If it keeps happening, restart the viewer.', action: { label: 'Go to the dashboard', href: '#/dashboard' } });
}

// The sidebar follows every outcome: the screen's own item, or none when its module never arrived.
function announce(path, params, mod) {
  document.dispatchEvent(new CustomEvent('route:changed', { detail: { path, params, sidebarKey: mod?.meta?.sidebarKey ?? null } }));
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

  // The top bar belongs to the screen that just left: cleared here, once, so every outcome
  // below (mounted, failed to load, threw while mounting) starts from an empty one.
  mountEl.replaceChildren();
  claimTopbar();

  let mod;
  try {
    mod = await match();
  } catch (e) {
    if (seq !== navSeq) return; // stale
    console.error('screen load failed', e);
    titleEl.textContent = 'Could not open';
    mountEl.replaceChildren(failureBlock());
    announce(path, params, null);
    return;
  }
  if (seq !== navSeq) return; // stale

  titleEl.textContent = mod.meta?.title || matchPrefix;
  // Pass remaining path segments after the prefix as `subpath` (e.g. /task/T-148 → ['T-148']).
  const subSegments = segments.slice(matchPrefix.split('/').filter(Boolean).length);
  let cleanup;
  try {
    cleanup = await mod.mount(mountEl, {
      params,
      subpath: subSegments,
      ...injectDeps,
    });
  } catch (e) {
    if (seq !== navSeq) return; // stale
    console.error('screen mount failed', e);
    // Drop whatever the screen built before it threw.
    claimTopbar();
    mountEl.replaceChildren(failureBlock());
    announce(path, params, mod);
    return;
  }
  if (seq !== navSeq) return; // stale
  currentCleanup = cleanup;

  announce(path, params, mod);
}

export function navigate(hash) {
  if (!mountEl) throw new Error('router.navigate() called before router.init()');
  if (!hash.startsWith('#')) hash = '#' + hash;
  if (location.hash === hash) go();
  else location.hash = hash;
}
