// plugins/taskmaster/viewer/js/lib/open-detail.js
// Single entry point for opening task/epic detail, honoring ui.detail_view_mode.
import { store } from '../store.js';
import { detailViewMode, parseDetailHref, shouldInterceptDetailLink } from './view-mode.js';

function routeHash(kind, id) { return `#/${kind}/${encodeURIComponent(id)}`; }

export function openDetail(kind, id) {
  const mode = detailViewMode(store.getPrefs());
  if (mode === 'full') { location.hash = routeHash(kind, id); return; }
  history.pushState({ detailModal: { kind, id } }, '');
  import('../components/detail-modal.js').then(({ openDetailModal }) => openDetailModal({ kind, id }));
}

let installed = false;
export function installDetailInterceptor() {
  if (installed) return;
  installed = true;
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    // Never intercept links that live inside an open modal — those are handled by the modal itself.
    if (a.closest('.dm-overlay')) return;
    const href = a.getAttribute('href') || '';
    const mode = detailViewMode(store.getPrefs());
    if (!shouldInterceptDetailLink({
      href, mode, button: e.button,
      metaKey: e.metaKey, ctrlKey: e.ctrlKey, shiftKey: e.shiftKey, altKey: e.altKey,
    })) return;
    const parsed = parseDetailHref(href);
    if (!parsed) return;
    e.preventDefault();
    openDetail(parsed.kind, parsed.id);
  }, true); // capture: run before screens' own link handlers
}
