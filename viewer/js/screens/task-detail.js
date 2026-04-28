import { getTaskFull, getTaskRelatedFull, invalidateTask } from '../store.js';
import { mountTaskDetailDocument } from '../components/task-detail-document.js';

export const meta = { title: 'Task Detail', icon: '◧', sidebarKey: null };

export async function mount(root, { params, store, api, prefs, subpath }) {
  const id = subpath?.[0] || params?.id || null;
  root.innerHTML = '<div class="td-page td-loading">Loading…</div>';

  if (!id) {
    root.innerHTML = '<div class="td-page td-empty">No task id in URL</div>';
    return () => {};
  }

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
