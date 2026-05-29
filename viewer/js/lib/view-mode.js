// plugins/taskmaster/viewer/js/lib/view-mode.js
// Pure helpers for the detail modal-vs-full presentation. No DOM/imports —
// node:test-covered, consumed by open-detail.js and the settings screen.

const MODES = new Set(['modal', 'full']);

export function detailViewMode(prefs) {
  const m = prefs?.ui?.detail_view_mode;
  return MODES.has(m) ? m : 'modal';
}

// '#/task/<id>' | '#/epic/<id>'  ->  {kind,id} ; anything else (incl. sub-paths) -> null
export function parseDetailHref(href) {
  const m = /^#\/(task|epic)\/([^/]+)$/.exec(String(href || ''));
  if (!m) return null;
  let id;
  try { id = decodeURIComponent(m[2]); } catch { id = m[2]; }
  if (!id) return null;
  return { kind: m[1], id };
}

export function shouldInterceptDetailLink({ href, mode, button, metaKey, ctrlKey, shiftKey, altKey }) {
  if (mode !== 'modal') return false;
  if (button !== 0) return false;
  if (metaKey || ctrlKey || shiftKey || altKey) return false;
  return parseDetailHref(href) !== null;
}
