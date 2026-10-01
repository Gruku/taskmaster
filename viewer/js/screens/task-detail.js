import { getTaskDetailFull, invalidateTask } from '../store.js';
import { mountTaskDetailDocument } from '../components/task-detail-document.js';
import { claimTopbar } from '../lib/topbar.js';

export const meta = { title: 'Task Detail', icon: '◧', sidebarKey: null };

function stateBlock(headline, hint) {
  const wrap = document.createElement('div');
  wrap.className = 'tm-empty';
  const h = document.createElement('div');
  h.className = 'tm-empty__headline';
  h.textContent = headline;
  const p = document.createElement('div');
  p.className = 'tm-empty__hint';
  p.append(hint, ' ');
  const a = document.createElement('a');
  a.href = '#/kanban';
  a.textContent = 'Open the Kanban';
  p.appendChild(a);
  wrap.append(h, p);
  return wrap;
}

export function mount(root, { params, store, api, prefs, subpath }) {
  let id = subpath?.[0] || params?.id || null;
  root.innerHTML = '<div class="td-page td-loading">Loading…</div>';

  // No id in URL → fall back to the most-recently-viewed task from prefs.
  if (!id) {
    const lastId = store?.getPrefs?.()?.ui?.last_task_id;
    if (lastId) {
      location.hash = `#/task/${lastId}`;
      return () => {};
    }
    claimTopbar();
    root.replaceChildren(stateBlock('No task open', 'Pick a task from a board.'));
    return () => {};
  }

  const onNavigate = (toId) => { location.hash = `#/task/${toId}`; };
  const onToggleVariant = async (next) => {
    await api.savePrefs({ screens: { task_detail: { view: next } } });
    if (disposed) return;
    invalidateTask(id);
    location.reload();
  };

  const prefsData = store?.getPrefs?.() || null;
  const urlView = params?.view === 'A' || params?.view === 'B' ? params.view : null;
  const view = urlView || (prefsData?.screens?.task_detail?.view === 'B' ? 'B' : 'A');
  let cleanup;
  let disposed = false, generation = 0;
  // Persist the most-recently-viewed task so a bare #/task re-opens it. Only once it has
  // painted: remembering an id that does not load would send bare #/task to a dead end.
  let remembered = false;
  function rememberAsLast() {
    if (remembered || !prefs?.patch) return;
    remembered = true;
    prefs.patch({ ui: { last_task_id: id } });
  }
  async function paint(value, request) {
    cleanup?.();
    const ctx = {...value, prefs: prefsData, store, api, onNavigate, onToggleVariant};
    if (view === 'B') {
      const mod = await import('../components/task-detail-graph.js');
      if (!disposed && request === generation) cleanup = mod.mountTaskDetailGraph(root, ctx);
    } else cleanup = mountTaskDetailDocument(root, ctx);
  }
  async function refresh() {
    if (disposed || store.isEditing(id)) return;
    const request = ++generation;
    try {
      const value = await getTaskDetailFull(id, {force: true});
      if (!disposed && request === generation && !store.isEditing(id)) {
        await paint(value, request);
        if (!disposed && request === generation) rememberAsLast();
      }
    } catch (e) {
      if (!disposed && request === generation && !store.isEditing(id)) {
        cleanup?.();
        claimTopbar();
        // http() throws `GET <path> → <status>: <body>`; anchor on the arrow so an
        // id like T-404 in the path can't read as a status.
        const missing = /→ 404\b/.test(String(e?.message));
        root.replaceChildren(stateBlock(missing ? 'Task not found' : 'Could not load task', missing ? `${id} does not exist.` : 'Something went wrong while loading this task.'));
      }
    }
  }
  const unsubscribe = store.subscribe(`task:${id}`, refresh);
  // Return cleanup before the first read resolves. The router cannot cancel an
  // async mount whose disposer has not arrived, and every route shares its root.
  void refresh();
  return () => { disposed = true; generation++; unsubscribe(); cleanup?.(); };
}
