import { registerWidget } from '../widget-catalog.js';

const SEV_COLOR = { Critical: '#e87a85', High: '#e8a34d', Medium: '#a8c958', Low: '#8a93a3' };

export const meta = {
  id: 'open-issues',
  label: 'Open issues',
  sizes: ['small', 'medium'],
  defaultSize: 'medium',
  defaultRail: 'right',
};

export async function mount(el, { api, store }) {
  let issues = [];
  try { issues = await api.listIssues({ status: 'open' }); } catch (_) { issues = []; }
  el.replaceChildren();
  if (!issues.length) {
    const empty = document.createElement('div');
    empty.className = 'widget__empty';
    empty.textContent = 'No open issues.';
    el.appendChild(empty);
    return () => {};
  }
  for (const i of issues.slice(0, 8)) {
    const row = document.createElement('a');
    row.href = `#/issues?focus=${encodeURIComponent(i.id)}`;
    row.style.cssText = 'display:flex;gap:8px;align-items:baseline;padding:4px 0;text-decoration:none;color:inherit;font-size:12px;';
    row.innerHTML = `<span style="color:${SEV_COLOR[i.severity] || 'var(--ink-3)'};font-family:var(--font-mono);">${i.id}</span><span>${i.title || ''}</span>`;
    el.appendChild(row);
  }
  return () => {};
}

registerWidget({ meta, mount });
