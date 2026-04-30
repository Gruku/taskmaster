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
  let identity, prefsData;
  try {
    [identity, prefsData] = await Promise.all([
      api.identity().catch(e => { console.error('identity fetch failed', e); return null; }),
      api.prefs().catch(e => { console.error('prefs fetch failed', e); return null; }),
    ]);
  } catch (e) {
    // Render boot error into the sidebar placeholder so the page isn't silently blank.
    const sidebarEl = document.getElementById('sidebar');
    if (sidebarEl) sidebarEl.innerHTML = `<div style="padding:16px;color:#d66b5f;font-size:11px">Boot failed: ${e.message}</div>`;
    console.error('boot failed', e);
    return;
  }
  store.setIdentity(identity);
  store.setPrefs(prefsData);

  // Apply persisted sidebar-collapsed before sidebar mounts so layout doesn't flicker.
  if (prefsData?.ui?.sidebar_collapsed) {
    document.querySelector('.shell')?.classList.add('sidebar-collapsed');
  }

  // Mount sidebar
  mountSidebar(document.getElementById('sidebar'), { store, prefs });

  // Init router
  routerInit({
    mount: document.getElementById('screen-mount'),
    topbar: document.getElementById('topbar'),
    deps: { store, api, prefs },
  });

  // Backlog polling loop
  pollBacklogForever();
  pollAutoStateForever();
}

async function pollBacklogForever() {
  let consecutiveFailures = 0;
  const MAX_BACKOFF_MS = 60_000;

  while (true) {
    // Pause polling when the tab is hidden; resume on visibility.
    if (document.visibilityState === 'hidden') {
      await new Promise(resolve => {
        document.addEventListener('visibilitychange', function onVisible() {
          if (document.visibilityState === 'visible') {
            document.removeEventListener('visibilitychange', onVisible);
            resolve();
          }
        });
      });
    }

    try {
      const yaml = await api.backlogYaml();
      // Server already returns YAML text; parse client-side via a worker-free approach.
      // Use jsyaml from CDN (matches existing viewer).
      await loadJsYaml();
      store.setBacklog(window.jsyaml.load(yaml));
      consecutiveFailures = 0;
    } catch (e) {
      consecutiveFailures++;
      console.error('backlog poll failed', e);
    }

    // Exponential backoff on consecutive failures, capped at MAX_BACKOFF_MS.
    const delay = consecutiveFailures > 0
      ? Math.min(BACKLOG_POLL_MS * 2 ** (consecutiveFailures - 1), MAX_BACKOFF_MS)
      : BACKLOG_POLL_MS;
    await sleep(delay);
  }
}

// Deduplicated jsyaml loader — concurrent callers share the same promise.
let _jsYamlPromise = null;
function loadJsYaml() {
  if (window.jsyaml) return Promise.resolve();
  if (!_jsYamlPromise) {
    _jsYamlPromise = new Promise((resolve, reject) => {
      const s = document.createElement('script');
      s.src = 'https://cdn.jsdelivr.net/npm/js-yaml@4/dist/js-yaml.min.js';
      s.onload = resolve;
      s.onerror = (e) => { _jsYamlPromise = null; reject(e); };
      document.head.appendChild(s);
    });
  }
  return _jsYamlPromise;
}

const AUTO_STATE_POLL_MS = 3000;

async function pollAutoStateForever() {
  let consecutiveFailures = 0;
  const MAX_BACKOFF_MS = 60_000;

  while (true) {
    // Pause polling when the tab is hidden; resume on visibility.
    if (document.visibilityState === 'hidden') {
      await new Promise(resolve => {
        document.addEventListener('visibilitychange', function onVisible() {
          if (document.visibilityState === 'visible') {
            document.removeEventListener('visibilitychange', onVisible);
            resolve();
          }
        });
      });
    }

    try {
      const auto = await api.autoState();
      store.setAutoState(auto);
      consecutiveFailures = 0;
    } catch (e) {
      consecutiveFailures++;
      console.error('auto state poll failed', e);
      store.setAutoState(null);
    }

    // Exponential backoff on consecutive failures, capped at MAX_BACKOFF_MS.
    const delay = consecutiveFailures > 0
      ? Math.min(AUTO_STATE_POLL_MS * 2 ** (consecutiveFailures - 1), MAX_BACKOFF_MS)
      : AUTO_STATE_POLL_MS;
    await sleep(delay);
  }
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

boot();

// Plan 5a — sessions screen fires this when its view toggle changes.
// Plan 5b will reuse the same convention.
window.addEventListener('viewer:prefs-patch', (ev) => {
  prefs.patch(ev.detail);
});
