// User intent: dark and light are equal themes; the user's choice persists, and with no choice the viewer follows the OS.
// The pre-paint inline script in index.html duplicates resolveTheme() and the 'tm.theme' key — keep the two in agreement.
const KEY = 'tm.theme';
const PREFS = new Set(['dark', 'light', 'system']);
let pref = 'system';
let savePref = null;

export function normalizePref(v) { return PREFS.has(v) ? v : 'system'; }
export function resolveTheme(p, systemDark) {
  const n = normalizePref(p);
  return n === 'system' ? (systemDark ? 'dark' : 'light') : n;
}
export function currentPref() { return pref; }

function systemDark() {
  return typeof matchMedia === 'function' ? matchMedia('(prefers-color-scheme: dark)').matches : true;
}
function apply() {
  const theme = resolveTheme(pref, systemDark());
  document.documentElement.dataset.theme = theme;
  document.dispatchEvent(new CustomEvent('theme:changed', { detail: { pref, theme } }));
}
export function setThemePref(next) {
  pref = normalizePref(next);
  try { localStorage.setItem(KEY, pref); } catch { /* storage unavailable: the choice still applies for this page */ }
  savePref?.(pref);
  apply();
}
export function initTheme({ store, prefs }) {
  savePref = (p) => prefs.patch({ theme: p });
  const loaded = store.getPrefs();
  if (loaded) {
    pref = normalizePref(loaded.theme);
    try { localStorage.setItem(KEY, pref); } catch { /* ignore */ }
  } else {
    // Prefs fetch failed: keep the cached choice and leave the cache alone, so one bad boot can't erase it.
    try { pref = normalizePref(localStorage.getItem(KEY)); } catch { pref = 'system'; }
  }
  if (typeof matchMedia === 'function') {
    matchMedia('(prefers-color-scheme: dark)').addEventListener('change', () => { if (pref === 'system') apply(); });
  }
  apply();
}
