import { registerWidget } from '../widget-catalog.js';

export const meta = {
  id: 'phase-deliverables',
  label: 'Phase deliverables',
  sizes: ['small', 'medium'],
  defaultSize: 'medium',
  defaultRail: 'left',
};

export async function mount(el, { store }) {
  function render() {
    const backlog = (store.getBacklog && store.getBacklog()) || {};
    const active = (backlog.phases || []).find(p => p.status === 'active');
    el.replaceChildren();
    if (!active) {
      const empty = document.createElement('div');
      empty.className = 'widget__empty';
      empty.textContent = 'No active phase.';
      el.appendChild(empty);
      return;
    }
    const list = document.createElement('ul');
    list.style.cssText = 'list-style:none;padding:0;margin:0;display:flex;flex-direction:column;gap:6px;';
    const tasks = (backlog.tasks || []).filter(t => t.phase === active.id);
    const MAX = 8;
    const shown = tasks.slice(0, MAX);
    for (const t of shown) {
      const li = document.createElement('li');
      const done = t.status === 'done' || t.status === 'completed';
      li.style.cssText = 'display:flex;gap:8px;align-items:baseline;font-size:12px;';
      li.innerHTML = `<span style="color:${done ? '#5fcdb8' : 'var(--ink-3)'};width:14px;">${done ? '✓' : '○'}</span><span class="mono" style="color:var(--ink-3);">${t.id}</span><span style="color:${done ? 'var(--ink-3)' : 'var(--ink-1)'};${done ? 'text-decoration:line-through;' : ''}">${t.title || ''}</span>`;
      list.appendChild(li);
    }
    el.appendChild(list);
    if (tasks.length > MAX) {
      const more = document.createElement('a');
      more.href = '#/kanban';
      more.style.cssText = 'display:block;margin-top:8px;color:var(--ink-3);font-size:11px;text-decoration:none;';
      more.textContent = `+${tasks.length - MAX} more →`;
      el.appendChild(more);
    }
  }
  render();
  const unsub = store.subscribe ? store.subscribe('backlog', render) : () => {};
  return () => unsub();
}

registerWidget({ meta, mount });
