import { registerWidget } from '../widget-catalog.js';

const STALE_DAYS = 4;

export const meta = {
  id: 'stale-tasks',
  label: 'Stale tasks',
  sizes: ['small', 'medium'],
  defaultSize: 'small',
  defaultRail: 'bottom',
};

function daysSince(iso) {
  if (!iso) return 0;
  const t = Date.parse(iso);
  if (Number.isNaN(t)) return 0;
  return Math.floor((Date.now() - t) / 86400000);
}

export async function mount(el, { store }) {
  function render() {
    const backlog = (store.getBacklog && store.getBacklog()) || {};
    const stale = (backlog.tasks || [])
      .filter(t => t.status === 'in-progress' || t.status === 'in_progress')
      .map(t => ({ t, age: daysSince(t.started || t.touched || t.created) }))
      .filter(x => x.age >= STALE_DAYS)
      .sort((a, b) => b.age - a.age)
      .slice(0, 6);

    el.replaceChildren();
    if (!stale.length) {
      const empty = document.createElement('div');
      empty.className = 'widget__empty';
      empty.textContent = 'Nothing stale.';
      el.appendChild(empty);
      return;
    }
    for (const { t, age } of stale) {
      const row = document.createElement('a');
      row.href = `#/task/${t.id}`;
      row.style.cssText = 'display:flex;gap:8px;align-items:baseline;padding:3px 0;text-decoration:none;color:inherit;font-size:12px;';
      row.innerHTML = `<span class="mono" style="color:var(--ink-3);">${t.id}</span><span style="flex:1;">${t.title || ''}</span><span style="color:#e8a34d;font-family:var(--font-mono);font-size:10px;">${age}d</span>`;
      el.appendChild(row);
    }
  }
  render();
  const unsub = store.subscribe ? store.subscribe('backlog', render) : () => {};
  return () => unsub();
}

registerWidget({ meta, mount });
