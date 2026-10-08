// plugins/taskmaster/viewer/js/lib/open-detail.js
// Single entry point for opening task/epic detail, honoring ui.detail_view_mode.
// `opener` is the element the user acted on: the modal hands focus back to it when it closes.
import { store } from '../store.js';
import { detailViewMode, parseDetailHref, shouldInterceptDetailLink } from './view-mode.js';

function routeHash(kind, id) { return `#/${kind}/${encodeURIComponent(id)}`; }

let opening = false;   // a modal is on its way: its history entry is already pushed

export function openDetail(kind, id, { opener } = {}) {
  const mode = detailViewMode(store.getPrefs());
  if (mode === 'full') { location.hash = routeHash(kind, id); return; }
  // One history entry per modal: an open (or opening) one swaps its content in place.
  if (!opening && !document.querySelector('.modal--detail')) {
    history.pushState({ detailModal: { kind, id } }, '');
    opening = true;
  }
  import('../components/detail-modal.js').then(({ openDetailModal }) => {
    opening = false;
    openDetailModal({ kind, id, opener });
  }, (e) => {
    opening = false;
    console.error('detail modal failed to load', e);
  });
}

let installed = false;
export function installDetailInterceptor() {
  if (installed) return;
  installed = true;
  document.addEventListener('click', (e) => {
    const a = e.target.closest && e.target.closest('a[href]');
    if (!a) return;
    // Never intercept links that live inside an open modal — those are handled by the modal itself —
    // nor inside a task page of its own, where a link to another task navigates.
    if (a.closest('.modal-overlay, [data-detail-links="follow"]')) return;
    const href = a.getAttribute('href') || '';
    const mode = detailViewMode(store.getPrefs());
    if (!shouldInterceptDetailLink({
      href, mode, button: e.button,
      metaKey: e.metaKey, ctrlKey: e.ctrlKey, shiftKey: e.shiftKey, altKey: e.altKey,
    })) return;
    const parsed = parseDetailHref(href);
    if (!parsed) return;
    e.preventDefault();
    openDetail(parsed.kind, parsed.id, { opener: a });
  }, true); // capture: run before screens' own link handlers
}
