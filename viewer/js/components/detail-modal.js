// plugins/taskmaster/viewer/js/components/detail-modal.js
// Read-only detail overlay. Mirrors entity-modal.js mechanics (scrim/Esc close,
// dedicated host, body class) but mounts a detail COMPONENT and adds Open-full.
// One modal at a time; peeking a linked entity swaps content in place.
import { store } from '../store.js';
import { api } from '../api.js';

const HOST_ID = 'detail-modal-host';
let active = null; // { overlay, close } — single instance

function esc(s) {
  return String(s == null ? '' : s)
    .replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
}

export function openDetailModal({ kind, id }) {
  // If a modal is already open, just swap its content (no new history entry).
  if (active) { active.load(kind, id); return active.close; }

  const host = document.getElementById(HOST_ID);
  if (!host) throw new Error(`#${HOST_ID} not found in DOM`);

  const overlay = document.createElement('div');
  overlay.className = 'dm-overlay';
  overlay.tabIndex = -1;
  const modal = document.createElement('div');
  modal.className = 'dm-modal';
  modal.setAttribute('role', 'dialog');
  modal.setAttribute('aria-modal', 'true');

  const header = document.createElement('div');
  header.className = 'dm-header';
  const titleEl = document.createElement('span');
  titleEl.className = 'dm-title';
  const actions = document.createElement('div');
  actions.className = 'dm-actions';            // detail component renders its action row here
  const openFull = document.createElement('a');
  openFull.className = 'dm-openfull';
  openFull.textContent = 'Open full ↗';
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'dm-close';
  closeBtn.setAttribute('aria-label', 'close');
  closeBtn.textContent = '✕';
  header.append(titleEl, actions, openFull, closeBtn);

  const bodyEl = document.createElement('div');
  bodyEl.className = 'dm-body';

  modal.append(header, bodyEl);
  overlay.appendChild(modal);
  host.appendChild(overlay);
  document.body.classList.add('dm-open');

  let disposeComponent = null;
  let cur = { kind, id };

  function route(k, i) { return `#/${k}/${encodeURIComponent(i)}`; }

  async function load(k, i) {
    cur = { kind: k, id: i };
    openFull.setAttribute('href', route(k, i));
    titleEl.textContent = i;
    actions.replaceChildren();
    bodyEl.replaceChildren();
    bodyEl.classList.add('dm-loading');
    if (disposeComponent) { disposeComponent(); disposeComponent = null; }
    try {
      if (k === 'epic') {
        const { getEpic } = await import('../api.js');
        const { mountEpicDetail } = await import('./epic-detail-document.js');
        const epic = await getEpic(i);
        titleEl.textContent = epic.name || i;
        disposeComponent = mountEpicDetail(bodyEl, {
          epic, store, chrome: 'embedded',
          onNavigate: (tid) => load('task', tid),
        });
      } else {
        const { getTaskFull, getTaskRelatedFull } = await import('../store.js');
        const { mountTaskDetailDocument } = await import('./task-detail-document.js');
        const [task, related] = await Promise.all([getTaskFull(i), getTaskRelatedFull(i)]);
        titleEl.textContent = task?.title || i;
        disposeComponent = mountTaskDetailDocument(bodyEl, {
          task, related, prefs: store.getPrefs(), store, api,
          chrome: 'embedded', actionsHost: actions,
          onNavigate: (tid) => load('task', tid),
          // swap modal body in place instead of reloading the page
          onToggleVariant: (v) => {
            api.savePrefs({ screens: { task_detail: { view: v } } }).catch(() => {});
            load('task', i);
          },
        });
      }
    } catch (e) {
      bodyEl.innerHTML = `<div class="dm-error">Could not load ${esc(i)}: ${esc(e.message)}. `
        + `<a href="${route(k, i)}">Open full</a>.</div>`;
    } finally {
      bodyEl.classList.remove('dm-loading');
    }
  }

  function destroy() {
    if (disposeComponent) { try { disposeComponent(); } catch {} disposeComponent = null; }
    overlay.remove();
    document.body.classList.remove('dm-open');
    document.removeEventListener('keydown', onKey);
    window.removeEventListener('popstate', onPop);
    window.removeEventListener('hashchange', onHash);
    active = null;
  }

  // Esc/scrim/✕ go through history.back() so the pushed entry is consumed
  // consistently; popstate is the single real close path.
  function requestClose() { history.back(); }
  function onKey(e) { if (e.key === 'Escape') { e.preventDefault(); requestClose(); } }
  function onPop() { destroy(); }
  function onHash() { destroy(); }   // sidebar nav while open → close, don't linger

  overlay.addEventListener('click', (e) => { if (e.target === overlay) requestClose(); });
  closeBtn.addEventListener('click', requestClose);
  openFull.addEventListener('click', (e) => {
    e.preventDefault();
    history.replaceState(null, '');          // drop the modal entry
    window.removeEventListener('hashchange', onHash); // this hash change is intentional
    destroy();
    location.hash = route(cur.kind, cur.id);
  });
  document.addEventListener('keydown', onKey);
  window.addEventListener('popstate', onPop);
  window.addEventListener('hashchange', onHash);

  active = { overlay, close: destroy, load };
  queueMicrotask(() => overlay.focus());
  load(kind, id);
  return destroy;
}
