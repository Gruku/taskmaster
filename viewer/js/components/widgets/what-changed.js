import { registerWidget } from '../widget-catalog.js';

const ICONS = {
  task_moved:       '↳',
  issue_opened:     '!',
  lesson_promoted:  '✦',
  task_closed:      '▮',
  phase_advanced:   '⎇',
};

export const meta = {
  id: 'what-changed',
  label: 'What changed',
  sizes: ['medium', 'wide'],
  defaultSize: 'medium',
  defaultRail: 'right',
};

export async function mount(el, { api, prefs }) {
  const since = (prefs && prefs.dashboard && prefs.dashboard.last_seen_at) || new Date(Date.now() - 24 * 3600 * 1000).toISOString();
  el.textContent = 'Loading…';
  let events = [];
  try { events = await api.getRecentEvents(since); } catch (_) { events = []; }
  el.replaceChildren();
  if (!events.length) {
    const empty = document.createElement('div');
    empty.className = 'widget__empty';
    empty.textContent = 'Nothing since you last looked.';
    el.appendChild(empty);
    return () => {};
  }
  for (const ev of events.slice(0, 12)) {
    const row = document.createElement('div');
    row.style.cssText = 'display:flex;gap:8px;align-items:baseline;font-size:12px;padding:3px 0;';
    row.innerHTML = `<span style="width:14px;color:var(--ink-2);">${ICONS[ev.kind] || '·'}</span><span style="color:var(--ink-1);">${ev.summary || ev.kind}</span><span style="margin-left:auto;color:var(--ink-3);font-family:var(--font-mono);font-size:10px;">${ev.at || ''}</span>`;
    el.appendChild(row);
  }
  return () => {};
}

registerWidget({ meta, mount });
