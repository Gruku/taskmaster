import { getTaskDetailFull } from '../store.js';
import { mountTaskDetailDocument, rememberView } from '../components/task-detail-document.js';
import { stateBlock } from '../components/empty-state.js';
import { openModalCount, topModal } from '../components/modal.js';
import { claimTopbar } from '../lib/topbar.js';

export const meta = { title: 'Task Detail', icon: '◧', sidebarKey: null };

const TO_KANBAN = { label: 'Open the Kanban', href: '#/kanban' };

export function mount(root, { params, store, api, prefs, subpath }) {
  let id = subpath?.[0] || params?.id || null;
  root.replaceChildren(stateBlock({ headline: 'Loading…', busy: true }));

  // No id in URL → fall back to the most-recently-viewed task from prefs.
  if (!id) {
    const lastId = store?.getPrefs?.()?.ui?.last_task_id;
    if (lastId) {
      location.hash = `#/task/${lastId}`;
      return () => {};
    }
    claimTopbar();
    root.replaceChildren(stateBlock({ state: 'empty', label: 'Task', headline: 'No task open', hint: 'Pick a task from a board.', action: TO_KANBAN }));
    return () => {};
  }

  const onNavigate = (toId) => { location.hash = `#/task/${toId}`; };

  const prefsData = store?.getPrefs?.() || null;
  const urlView = params?.view === 'A' || params?.view === 'B' ? params.view : null;
  // The view on screen. The top bar's switch is built from this, never from the saved preference alone:
  // an address that names a view wins over it.
  let view = urlView || (prefsData?.screens?.task_detail?.view === 'B' ? 'B' : 'A');
  let cleanup;
  let disposed = false, generation = 0;
  let shown = null;      // the detail last painted, for switching views without a fetch

  const onToggleVariant = async (next) => {
    if ((next !== 'A' && next !== 'B') || next === view) return;
    view = next;
    // From here the choice is the saved one; an address still naming the other view would undo it on reload.
    if (urlView) history.replaceState(history.state, '', `#/task/${encodeURIComponent(id)}`);
    if (shown) await paint(shown, generation);
    prefs?.patch({ screens: { task_detail: { view: next } } });
  };

  // Persist the most-recently-viewed task so a bare #/task re-opens it. Only once it has
  // painted: remembering an id that does not load would send bare #/task to a dead end.
  // Set by Try again: whatever the retried read paints next takes keyboard focus, so it is never left on <body>.
  let refocus = false;
  function takeFocus(el) {
    if (!refocus) return;
    refocus = false;
    if (!el) return;
    if (!el.matches('a[href], button') && !el.hasAttribute('tabindex')) el.setAttribute('tabindex', '-1');
    el.focus();
  }
  function retry() {
    const loading = stateBlock({ headline: 'Loading…', busy: true });
    loading.setAttribute('tabindex', '-1');
    root.replaceChildren(loading);
    loading.focus();
    refocus = true;
    void refresh();
  }

  let remembered = false;
  function rememberAsLast() {
    if (remembered || !prefs?.patch) return;
    remembered = true;
    prefs.patch({ ui: { last_task_id: id } });
  }
  async function paint(value, request) {
    // A repaint replaces every node; what was open is opened again and focus put back on the same control.
    const restore = rememberView(root);
    // The graph view's own state (open tab, hidden context, canvas scroll) carries over a repaint of the graph view.
    const viewState = view === 'B' ? cleanup?.viewState?.() : undefined;
    // A fullscreen graph frame stays on the page for the graph mount below to repaint inside it.
    const keepFrame = !!viewState?.frame;
    cleanup?.({ keepFrame });
    cleanup = null;
    const ctx = {...value, prefs: prefsData, store, api, onNavigate, onToggleVariant, view, viewState};
    if (view === 'B') {
      const mod = await import('../components/task-detail-graph.js');
      if (!disposed && request === generation) cleanup = mod.mountTaskDetailGraph(root, ctx);
      else if (keepFrame) {
        // Nothing replaces the kept frame after all: leave no screen filled by an abandoned page.
        if (document.fullscreenElement && root.contains(document.fullscreenElement)) document.exitFullscreen?.();
        root.replaceChildren();
      }
    } else cleanup = mountTaskDetailDocument(root, ctx);
    shown = value;
    restore(root);
  }
  async function refresh() {
    // Nothing is painted on an early return, so a later store refresh must not pull focus to the h1.
    if (disposed || store.isEditing(id)) { refocus = false; return; }
    const request = ++generation;
    try {
      const value = await getTaskDetailFull(id, {force: true});
      // An edit that began while the read was out paints nothing either: no focus pulled to the h1 later.
      if (!disposed && request === generation && store.isEditing(id)) refocus = false;
      else if (!disposed && request === generation) {
        await paint(value, request);
        if (!disposed && request === generation) {
          rememberAsLast();
          // The page's h1, in either view.
          takeFocus(root.querySelector('h1'));
        }
      }
    } catch (e) {
      if (!disposed && request === generation && !store.isEditing(id)) {
        cleanup?.();
        cleanup = null;
        shown = null;
        claimTopbar();
        // The remembered task is gone: forgotten, so the next bare #/task says "No task open" instead of coming back here.
        if (e?.code === 404 && store?.getPrefs?.()?.ui?.last_task_id === id) prefs?.patch?.({ ui: { last_task_id: null } });
        // Said in words: the request, its status and the server's text are never shown on the page.
        root.replaceChildren(stateBlock(e?.code === 404
          ? { state: 'missing', label: id, headline: 'Task not found', hint: 'It may have been archived, renamed or removed.', action: TO_KANBAN }
          : { state: 'error', label: id, headline: 'Could not load this task', hint: 'Something went wrong while loading it. Try again in a moment.', action: { label: 'Try again', onClick: retry } }));
        takeFocus(root.querySelector('.tm-empty :is(a[href], button)'));
      }
    }
  }
  const unsubscribe = store.subscribe(`task:${id}`, refresh);
  // Return cleanup before the first read resolves. The router cannot cancel an
  // async mount whose disposer has not arrived, and every route shares its root.
  void refresh();
  return () => {
    // First, so a read that resolves from here on paints nothing.
    generation++;
    disposed = true;
    unsubscribe();
    cleanup?.();
    // The Edit form opened from this page goes with it: closed when clean, asked first when it holds typing.
    if (openModalCount() > 0) topModal().requestClose();
  };
}
