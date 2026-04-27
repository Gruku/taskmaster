import { api } from './api.js';
import { store } from './store.js';
import { init as routerInit, registerScreen } from './router.js';
import { mountSidebar } from './components/sidebar.js';

const BACKLOG_POLL_MS = 3000;
const PREFS_DEBOUNCE_MS = 400;

// Register screens (lazy-loaded modules).
registerScreen('/dashboard',  () => import('./screens/dashboard.js'));
registerScreen('/kanban',     () => import('./screens/kanban.js'));
registerScreen('/task',       () => import('./screens/task-detail.js'));
registerScreen('/sessions',   () => import('./screens/sessions.js'));
registerScreen('/lessons',    () => import('./screens/lessons.js'));
registerScreen('/issues',     () => import('./screens/issues.js'));
registerScreen('/auto',       () => import('./screens/auto-mode.js'));
registerScreen('/recap',      () => import('./screens/recap.js'));

// Prefs writer with debounce — screens call `prefs.patch({...})`.
let prefsDebounce = null;
const prefs = {
  patch(patchObj) {
    // Apply locally for instant UI feedback.
    const cur = store.getPrefs() || {};
    const merged = deepMerge(structuredClone(cur), patchObj);
    store.setPrefs(merged);
    // Persist with debounce.
    if (prefsDebounce) clearTimeout(prefsDebounce);
    prefsDebounce = setTimeout(() => {
      api.savePrefs(patchObj).catch(e => console.error('savePrefs failed', e));
    }, PREFS_DEBOUNCE_MS);
  },
};

function deepMerge(base, patch) {
  for (const [k, v] of Object.entries(patch)) {
    if (v && typeof v === 'object' && !Array.isArray(v) && base[k] && typeof base[k] === 'object') {
      deepMerge(base[k], v);
    } else {
      base[k] = v;
    }
  }
  return base;
}

async function boot() {
  // Initial fetches in parallel
  const [identity, prefsData] = await Promise.all([
    api.identity().catch(e => { console.error('identity fetch failed', e); return null; }),
    api.prefs().catch(e => { console.error('prefs fetch failed', e); return null; }),
  ]);
  store.setIdentity(identity);
  store.setPrefs(prefsData);

  // Mount sidebar
  mountSidebar(document.getElementById('sidebar'), { store });

  // Init router
  routerInit({
    mount: document.getElementById('screen-mount'),
    topbar: document.getElementById('topbar'),
    deps: { store, api, prefs },
  });

  // Backlog polling loop
  pollBacklogForever();
}

async function pollBacklogForever() {
  while (true) {
    try {
      const yaml = await api.backlogYaml();
      // Server already returns YAML text; parse client-side via a worker-free approach.
      // Use jsyaml from CDN (matches existing viewer).
      if (!window.jsyaml) await loadJsYaml();
      store.setBacklog(window.jsyaml.load(yaml));
    } catch (e) {
      console.error('backlog poll failed', e);
    }
    await sleep(BACKLOG_POLL_MS);
  }
}

function loadJsYaml() {
  return new Promise((resolve, reject) => {
    const s = document.createElement('script');
    s.src = 'https://cdn.jsdelivr.net/npm/js-yaml@4/dist/js-yaml.min.js';
    s.onload = resolve;
    s.onerror = reject;
    document.head.appendChild(s);
  });
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

boot();
