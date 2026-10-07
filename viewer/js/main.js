import { api } from './api.js';
import { store } from './store.js';
import { init as routerInit, registerScreen } from './router.js';
import { mountSidebar } from './components/sidebar.js';
import { initTheme, setThemePref } from './lib/theme.js';
import { icon } from './components/icon.js';
import { createPrefsWriter, deepMerge } from './lib/prefs-writer.js';

const BACKLOG_POLL_MS = 3000;
const PREFS_DEBOUNCE_MS = 400;

// Register screens (lazy-loaded modules).
registerScreen('/dashboard', () => import('./screens/desk.js'));
registerScreen('/kanban',     () => import('./screens/kanban.js'));
registerScreen('/table',      () => import('./screens/table.js'));
registerScreen('/task',       () => import('./screens/task-detail.js'));
registerScreen('/epics',      () => import('./screens/epics.js'));
registerScreen('/epic',       () => import('./screens/epic-detail.js'));
registerScreen('/sessions',   () => import('./screens/sessions.js'));
registerScreen('/issues',     () => import('./screens/issues.js'));
registerScreen('/issue',      () => import('./screens/issue-detail.js'));
registerScreen('/bugs',       () => import('./screens/bugs.js'));
registerScreen('/bug',        () => import('./screens/bug-detail.js'));
registerScreen('/ideas',      () => import('./screens/ideas.js'));
registerScreen('/archived',   () => import('./screens/archived.js'));
registerScreen('/settings',   () => import('./screens/settings.js'));

// Prefs writer with debounce — screens call `prefs.patch({...})`.
// Patches inside one debounce window are merged, so none of them is dropped.
const prefsWriter = createPrefsWriter({
  save: (p, o) => api.savePrefs(p, o),
  delayMs: PREFS_DEBOUNCE_MS,
  onError: (e, { dropped }) => console.error('preferences not saved', dropped, e),
});
// A choice made just before the tab closes or navigates away is still inside its debounce window.
window.addEventListener('pagehide', () => prefsWriter.flush());
const prefs = {
  patch(patchObj) {
    // Apply locally for instant UI feedback.
    const cur = store.getPrefs() || {};
    const merged = deepMerge(structuredClone(cur), patchObj);
    store.setPrefs(merged);
    // Persist with debounce.
    prefsWriter.queue(patchObj);
  },
};

async function boot() {
  // Before the fetches and before initTheme: the toggle has its icon and label while boot
  // waits (it stays disabled until initTheme), and initTheme's first theme:changed event
  // finds its listener.
  wireThemeToggle();

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
    if (sidebarEl) sidebarEl.replaceChildren(Object.assign(document.createElement('div'), { className: 'boot-error', textContent: `Boot failed: ${e.message}` }));
    console.error('boot failed', e);
    return;
  }
  store.setIdentity(identity);
  store.setPrefs(prefsData);
  initTheme({ store, prefs });
  // Only now can a click be saved; before this the loaded preference would overwrite it.
  document.getElementById('theme-toggle')?.removeAttribute('disabled');

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

  // Ctrl+K / ⌘K focuses the current screen's search field (the hint beside it names this shortcut).
  // Not while a modal is open: the search sits behind the overlay, and moving focus there
  // would commit the field being edited and send later keystrokes to a filter nobody can see.
  window.addEventListener('keydown', (e) => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey || e.key?.toLowerCase() !== 'k') return;
    if (document.querySelector('[aria-modal="true"]')) return;
    const input = document.querySelector('[data-global-search]');
    if (input) { e.preventDefault(); input.focus(); input.select(); }
  });

  // Detail-modal interception (delegated <a> clicks → openDetail when mode=modal).
  import('./lib/open-detail.js').then(({ installDetailInterceptor }) => installDetailInterceptor());

  // Backlog polling loop
  pollBacklogForever();
}

// The toggle is an action, not a state: its label names the theme a click switches to and
// follows theme:changed. Until the first event it reads the theme the pre-paint script applied.
function wireThemeToggle() {
  const toggle = document.getElementById('theme-toggle');
  if (!toggle) return;
  toggle.replaceChildren(icon('polarity', { size: 20 }));
  const sync = (theme) => {
    const next = theme === 'dark' ? 'light' : 'dark';
    toggle.setAttribute('aria-label', `Switch to ${next} theme`);
    toggle.title = `Switch to ${next} theme`;
    toggle.dataset.next = next;
  };
  sync(document.documentElement.dataset.theme);
  document.addEventListener('theme:changed', (e) => sync(e.detail.theme));
  toggle.addEventListener('click', () => setThemePref(toggle.dataset.next));
}

async function pollBacklogForever() {
  let consecutiveFailures = 0;
  const MAX_BACKOFF_MS = 60_000;
  let isFirst = true;

  while (true) {
    try {
      await store.refreshBoard(api);
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

    // After the first poll, pause subsequent polls when the tab is hidden.
    if (!isFirst && document.visibilityState === 'hidden') {
      await new Promise(resolve => {
        document.addEventListener('visibilitychange', function onVisible() {
          if (document.visibilityState === 'visible') {
            document.removeEventListener('visibilitychange', onVisible);
            resolve();
          }
        });
      });
    }
    isFirst = false;
  }
}

const sleep = ms => new Promise(r => setTimeout(r, ms));

boot();

// Plan 5a — sessions screen fires this when its view toggle changes.
// Plan 5b will reuse the same convention.
window.addEventListener('viewer:prefs-patch', (ev) => {
  prefs.patch(ev.detail);
});
