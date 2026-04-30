import { registerWidget } from '../widget-catalog.js';

export const meta = {
  id: 'recent-commits',
  label: 'Recent commits',
  sizes: ['small', 'medium'],
  defaultSize: 'small',
  defaultRail: 'bottom',
};

export async function mount(el, { api }) {
  let commits = [];
  try { commits = await api.getRecentCommits({ limit: 8 }); } catch (_) { commits = []; }
  el.replaceChildren();
  if (!commits.length) {
    const empty = document.createElement('div');
    empty.className = 'widget__empty';
    empty.textContent = 'No commits yet.';
    el.appendChild(empty);
    return () => {};
  }
  for (const c of commits) {
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:8px;align-items:baseline;padding:3px 0;font-size:12px;';
    row.innerHTML = `<span class="mono" style="color:var(--ink-3);">${(c.sha || '').slice(0, 7)}</span><span style="flex:1;color:var(--ink-1);">${c.subject || ''}</span><span style="font-family:var(--font-mono);color:var(--ink-3);font-size:10px;">${c.relative_time || ''}</span>`;
    el.appendChild(row);
  }
  return () => {};
}

registerWidget({ meta, mount });
