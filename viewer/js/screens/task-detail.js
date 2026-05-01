import { getTaskFull, getTaskRelatedFull, invalidateTask } from '../store.js';
import { mountTaskDetailDocument } from '../components/task-detail-document.js';

export const meta = { title: 'Task Detail', icon: '◧', sidebarKey: 'task' };

export async function mount(root, { params, store, api, prefs, subpath }) {
  let id = subpath?.[0] || params?.id || null;
  root.innerHTML = '<div class="td-page td-loading">Loading…</div>';

  // No id in URL → fall back to the most-recently-viewed task from prefs.
  if (!id) {
    const lastId = store?.getPrefs?.()?.ui?.last_task_id;
    if (lastId) {
      location.hash = `#/task/${lastId}`;
      return () => {};
    }
    root.innerHTML = `<div class="td-page td-empty">
      <h2 style="font-size:var(--text-2xl);margin:0 0 8px">No task open</h2>
      <p style="color:var(--ink-3);margin:0">
        Pick a task from the
        <a href="#/kanban" style="color:var(--accent)">Kanban</a>
        or the
        <a href="#/table" style="color:var(--accent)">Table</a>.
      </p>
    </div>`;
    return () => {};
  }

  // Persist the most-recently-viewed task so the sidebar's Task entry
  // re-opens it when clicked without an id.
  if (prefs?.patch) prefs.patch({ ui: { last_task_id: id } });

  let task = null, related = null;
  try {
    [task, related] = await Promise.all([
      getTaskFull(id),
      getTaskRelatedFull(id),
    ]);
  } catch (e) {
    root.innerHTML = `<div class="td-page td-empty">Could not load ${id}: ${e.message}</div>`;
    return () => {};
  }

  const onNavigate = (toId) => { location.hash = `#/task/${toId}`; };
  const onToggleVariant = async (next) => {
    await api.savePrefs({ screens: { task_detail: { view: next } } });
    invalidateTask(id);
    location.reload();
  };

  const prefsData = store?.getPrefs?.() || null;
  const urlView = params?.view === 'A' || params?.view === 'B' ? params.view : null;
  const view = urlView || (prefsData?.screens?.task_detail?.view === 'B' ? 'B' : 'A');
  let cleanup;
  if (view === 'B') {
    const mod = await import('../components/task-detail-graph.js');
    cleanup = mod.mountTaskDetailGraph(root, { task, related, prefs: prefsData, onNavigate, onToggleVariant });
  } else {
    cleanup = mountTaskDetailDocument(root, { task, related, prefs: prefsData, onNavigate, onToggleVariant });
  }
  return cleanup;
}
