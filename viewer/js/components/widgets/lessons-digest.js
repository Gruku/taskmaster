import { registerWidget } from '../widget-catalog.js';

export const meta = {
  id: 'lessons-digest',
  label: 'Lessons digest',
  sizes: ['small', 'medium'],
  defaultSize: 'small',
  defaultRail: 'bottom',
};

export async function mount(el, { api }) {
  let lessons = [];
  try { lessons = await api.listLessons({ shelf: 'core' }); } catch (_) { lessons = []; }
  el.replaceChildren();
  if (!lessons.length) {
    const empty = document.createElement('div');
    empty.className = 'widget__empty';
    empty.textContent = 'No core lessons yet.';
    el.appendChild(empty);
    return () => {};
  }
  for (const l of lessons.slice(0, 6)) {
    const row = document.createElement('a');
    row.href = `#/lessons?focus=${encodeURIComponent(l.id)}`;
    row.style.cssText = 'display:flex;gap:8px;align-items:baseline;padding:3px 0;text-decoration:none;color:inherit;font-size:12px;';
    row.innerHTML = `<span class="mono" style="color:#d4a72c;">${l.id}</span><span style="flex:1;">${l.title || l.summary || ''}</span><span style="font-family:var(--font-mono);color:var(--ink-3);font-size:10px;">×${l.reinforce_count || 0}</span>`;
    el.appendChild(row);
  }
  return () => {};
}

registerWidget({ meta, mount });
