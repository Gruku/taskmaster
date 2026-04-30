import { registerWidget } from '../widget-catalog.js';

export const meta = {
  id: 'newly-unblocked',
  label: 'Newly unblocked',
  sizes: ['small', 'medium'],
  defaultSize: 'medium',
  defaultRail: 'left',
};

export async function mount(el, { store }) {
  function render() {
    const backlog = (store.getBacklog && store.getBacklog()) || {};
    const tasks = (backlog.tasks || []).filter(t => {
      const deps = t.depends_on || [];
      const allDone = deps.every(id => {
        const dep = (backlog.tasks || []).find(x => x.id === id);
        return dep && (dep.status === 'done' || dep.status === 'completed');
      });
      return deps.length > 0 && allDone && (t.status === 'todo' || t.status === 'ready');
    }).slice(0, 5);

    el.replaceChildren();
    if (!tasks.length) {
      const empty = document.createElement('div');
      empty.className = 'widget__empty';
      empty.textContent = 'Nothing newly unblocked.';
      el.appendChild(empty);
      return;
    }
    for (const t of tasks) {
      const row = document.createElement('a');
      row.href = `#/task/${t.id}`;
      row.style.cssText = 'display:flex;gap:8px;align-items:baseline;padding:4px 0;text-decoration:none;color:inherit;font-size:12px;';
      row.innerHTML = `<span class="mono" style="color:var(--ink-3);">${t.id}</span><span>${t.title || ''}</span>`;
      el.appendChild(row);
    }
  }
  render();
  const unsub = store.subscribe ? store.subscribe('backlog', render) : () => {};
  return () => unsub();
}

registerWidget({ meta, mount });
