import { registerWidget } from '../widget-catalog.js';

export const meta = {
  id: 'agent-activity',
  label: 'Agent activity',
  sizes: ['small', 'medium'],
  defaultSize: 'small',
  defaultRail: 'bottom',
};

export async function mount(el, { api }) {
  let state = { running: [], hooks: {} };
  try { state = await api.getAutoState(); } catch (_) { /* keep defaults */ }
  el.replaceChildren();
  const total = (state.running || []).length;
  const summary = document.createElement('div');
  summary.style.cssText = 'font-size:12px;color:var(--ink-1);margin-bottom:6px;';
  summary.innerHTML = total
    ? `<span style="display:inline-block;width:8px;height:8px;border-radius:50%;background:#6ea8ff;margin-right:6px;"></span>${total} auto-mode session${total === 1 ? '' : 's'} running`
    : 'No agents running.';
  el.appendChild(summary);

  for (const r of (state.running || []).slice(0, 4)) {
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:8px;align-items:baseline;font-size:11px;padding:2px 0;';
    row.innerHTML = `<span class="mono" style="color:var(--ink-3);">${r.task_id || ''}</span><span style="flex:1;">${r.step_text || r.step || ''}</span><span style="font-family:var(--font-mono);color:var(--ink-3);">${r.elapsed || ''}</span>`;
    el.appendChild(row);
  }
  return () => {};
}

registerWidget({ meta, mount });
