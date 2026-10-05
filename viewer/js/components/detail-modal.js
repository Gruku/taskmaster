// User intent: clicking a card opens the task as a proper dialog on the shared modal shell — its id and title in the
// header (once), Edit and Open full beside them, the task document below — that closes the same way however it is
// dismissed, costs exactly one Back, and hands focus back to the card it came from.
// One modal at a time; peeking a linked task or epic swaps the content in place.
import { store, getTaskDetailFull } from '../store.js';
import { api, getEpic, listBugs } from '../api.js';
import { h } from '../util/h.js';
import { openModal } from './modal.js';
import { icon } from './icon.js';
import { stateBlock } from './empty-state.js';
import { parseDetailHref } from '../lib/view-mode.js';

let active = null; // { load, close } — single instance

const route = (kind, id) => `#/${kind}/${encodeURIComponent(id)}`;

// The title the board already knows, shown while the detail loads.
function knownTitle(kind, id) {
  const board = store.getBacklog?.();
  const hit = kind === 'epic'
    ? board?.epics?.find?.((e) => e?.id === id)?.name
    : board?.tasks?.find?.((t) => t?.id === id)?.title;
  return typeof hit === 'string' && hit ? hit : id;
}

// The board re-renders on its poll, so the element that opened the modal may be gone when it closes.
// This remembers enough of it to find the one that took its place.
function twinFinder(opener) {
  const taskId = opener?.dataset?.taskId;
  const href = opener?.getAttribute?.('href');
  const tag = opener?.tagName;
  return () => {
    const scope = document.getElementById('screen-mount') ?? document;
    if (taskId) return [...scope.querySelectorAll('[data-task-id]')].find((el) => el.dataset.taskId === taskId && el.tagName === tag) ?? null;
    if (href) return [...scope.querySelectorAll('a[href]')].find((a) => a.getAttribute('href') === href) ?? null;
    return null;
  };
}

export function openDetailModal({ kind, id, opener }) {
  // If a modal is already open, just swap its content (no new history entry).
  if (active) { active.load(kind, id); return active.close; }

  const from = opener ?? (document.activeElement !== document.body ? document.activeElement : null);
  const findTwin = twinFinder(from);

  let leaving = false;        // a history.back() is on its way to close the modal
  const modal = openModal({
    title: knownTitle(kind, id), eyebrow: id, size: 'lg', className: 'modal--plain-title modal--detail', opener: from,
    initialFocus: (dialog) => dialog.querySelector('.modal-title'),
    // Escape, the overlay and the close button leave through the history entry the modal was opened with,
    // so they and the browser's Back are one path and the entry is always consumed.
    onRequestClose: () => {
      if (!history.state?.detailModal) return true;
      if (!leaving) { leaving = true; history.back(); }
      return false;
    },
  });
  const titleEl = modal.dialog.querySelector('.modal-title');
  titleEl.tabIndex = -1;      // focus lands here on open and after a peek

  const editBtn = h('button', { type: 'button', class: 'btn btn--secondary btn--sm', 'data-action': 'edit', hidden: '' }, 'Edit');
  const openFull = h('a', { class: 'btn btn--ghost btn--sm', 'data-action': 'open-full', href: route(kind, id) },
    [icon('external', { size: 14 }), 'Open full']);
  modal.actions.append(editBtn, openFull);
  const mountEl = h('div', { class: 'detail-doc' });
  modal.body.appendChild(mountEl);

  let disposeComponent = null;
  let unsubscribe = null;
  let generation = 0, closed = false;
  let cur = { kind, id };
  let task = null;            // the task on screen, for Edit

  function dispose() {
    // The disposer locates inline editors in this DOM. Run it before clearing
    // anything so abandoned drafts lose their timers and edit-state leases.
    if (disposeComponent) { try { disposeComponent(); } catch (e) { console.error('detail dispose failed', e); } disposeComponent = null; }
    mountEl.removeAttribute('style');
  }

  // Focus that fell out of the dialog with removed content comes back to the title.
  function holdFocus() {
    if (!modal.isTop()) return;
    const at = document.activeElement;
    if (!at || at === document.body || !at.isConnected || !modal.dialog.contains(at)) titleEl.focus();
  }

  // `soft` re-reads the entity on screen after it changed: no loading state, the scroll position and focus kept.
  async function load(k, i, { soft = false } = {}) {
    const request = ++generation;
    if (!soft) {
      unsubscribe?.(); unsubscribe = null;
      dispose();
      cur = { kind: k, id: i };
      task = null;
      editBtn.hidden = true;
      openFull.setAttribute('href', route(k, i));
      modal.setEyebrow(i);
      modal.setTitle(knownTitle(k, i));
      mountEl.replaceChildren(stateBlock({ state: 'loading', headline: 'Loading…', busy: true }));
      modal.body.scrollTop = 0;
      // A peek removes the link that was clicked; the first load leaves focus to the shell.
      if (request > 1) holdFocus();
    }
    try {
      if (k === 'epic') {
        const { mountEpicDetail } = await import('./epic-detail-document.js');
        const epic = await getEpic(i);
        if (closed || request !== generation) return;
        dispose();
        modal.setTitle(typeof epic?.name === 'string' && epic.name ? epic.name : i);
        mountEl.replaceChildren();
        disposeComponent = mountEpicDetail(mountEl, {
          epic, store, chrome: 'embedded',
          onNavigate: (tid) => load('task', tid),
        });
      } else {
        const { mountTaskDetailDocument, rememberFocus } = await import('./task-detail-document.js');
        const detail = await getTaskDetailFull(i, {force: true});
        if (closed || request !== generation) return;
        const refocus = soft ? rememberFocus(modal.dialog) : null;
        const scrollTop = modal.body.scrollTop;
        dispose();
        // The title is edited where it is shown: its inline field lives in the dialog's heading.
        const titleHost = h('span', { class: 'td-title-host', 'data-test': 'title' });
        modal.setTitle(titleHost);
        mountEl.replaceChildren();
        disposeComponent = mountTaskDetailDocument(mountEl, {
          ...detail, prefs: store.getPrefs(), store, api, listBugs,
          chrome: 'embedded', titleHost,
        });
        task = detail.task;
        editBtn.hidden = !task;
        if (soft) { modal.body.scrollTop = scrollTop; refocus(modal.dialog); }
        unsubscribe ??= store.subscribe(`task:${i}`, () => { if (!store.isEditing(i)) load(k, i, { soft: true }); });
      }
      // After a peek, focus is on the title of what is now shown — unless the user is already at the header's controls.
      if (soft) holdFocus();
      else if (modal.isTop() && !modal.header.contains(document.activeElement)) titleEl.focus();
    } catch (e) {
      if (closed || request !== generation) return;
      console.error('detail load failed', e);
      dispose();
      task = null;
      editBtn.hidden = true;
      modal.setTitle(knownTitle(k, i));
      // http() throws `GET <path> → <status>: <body>`; the message is for the console, never for the page.
      const missing = /→ 404\b/.test(String(e?.message));
      const noun = k === 'epic' ? 'epic' : 'task';
      mountEl.replaceChildren(stateBlock({
        state: 'error', label: i,
        headline: missing ? `This ${noun} was not found` : `Could not load this ${noun}`,
        hint: missing ? 'It may have been archived, renamed or removed.' : 'Something went wrong while loading it. The full page may still open.',
        action: h('a', { class: 'btn btn--secondary btn--sm', 'data-action': 'open-full', href: route(k, i) }, 'Open full'),
      }));
      holdFocus();
    }
  }

  function onPop() { modal.close(); }
  function onHash() { modal.close(); }   // sidebar nav while open → close, don't linger

  // Leaving for another screen takes over the modal's own history entry: Back from there returns to
  // where the modal was opened, not to a board that reopens nothing.
  function leaveTo(href) {
    if (href === location.hash) { modal.requestClose(); return; }
    modal.close();
    if (history.state?.detailModal) location.replace(href);
    else location.hash = href;
  }

  // Links inside the dialog. A task or epic is peeked in place; any other screen is left for; a modified
  // click (new tab, new window) is the browser's and leaves the modal open.
  modal.dialog.addEventListener('click', (e) => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const a = e.target.closest?.('a[href]');
    const href = a?.getAttribute('href') || '';
    if (!a || !href.startsWith('#/') || a.target === '_blank') return;
    e.preventDefault();
    const peek = a.dataset.action === 'open-full' ? null : parseDetailHref(href);
    if (peek) load(peek.kind, peek.id);
    else leaveTo(href);
  });

  editBtn.addEventListener('click', async () => {
    const editing = task;
    if (!editing) return;
    const { openTaskEditModal } = await import('./edit/task-actions.js');
    // Stacked on top; when it closes the task's edit lease ends and the subscription above re-reads the task.
    if (!closed && task === editing) openTaskEditModal({ store, api, task: editing });
  });

  modal.onClosed(() => {
    closed = true; generation++; unsubscribe?.(); unsubscribe = null;
    dispose();
    window.removeEventListener('popstate', onPop);
    window.removeEventListener('hashchange', onHash);
    active = null;
    // The shell returned focus to the opener if it is still on the page; a card redrawn meanwhile is found again.
    if (from && !from.isConnected && !document.querySelector('.modal-overlay')) {
      const twin = findTwin();
      if (twin && twin !== document.activeElement) twin.focus();
    }
  });
  window.addEventListener('popstate', onPop);
  window.addEventListener('hashchange', onHash);

  active = { load, close: () => modal.close() };
  load(kind, id);
  return active.close;
}
