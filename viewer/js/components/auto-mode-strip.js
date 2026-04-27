// Light auto-mode strip across the top of the kanban (spec §3.3).
// Shows: conic spinner · "Auto-mode · N running" · session timer · per-run pills · "view all →"
//
// Render-only: takes a normalized list of runs. Updates in place via update().

import { formatElapsed, isoToMs } from '../lib/time.js';

const STAGE_ORDER = ['PICK', 'IMPLEMENT', 'REVIEW', 'HANDOVER_STUB', 'COMPLETE'];

/** Convert raw auto state → run rows the strip can render.
 *  Plan 6 will return multiple parallel runs; Plan 2 derives a single-run list from cursor. */
export function runsFromAutoState(autoState, backlog) {
  if (!autoState || !autoState.cursor) return { runs: [], sessionStartedMs: null };
  const tid = autoState.cursor.task_id;
  const task = (backlog?.tasks || []).find(t => t.id === tid);
  const stageIdx = Math.max(0, STAGE_ORDER.indexOf(autoState.cursor.stage || 'PICK'));
  const pct = Math.round(((stageIdx + 0.5) / STAGE_ORDER.length) * 100);
  const startedMs = isoToMs(autoState.started_at);
  return {
    sessionStartedMs: startedMs,
    runs: [{
      id: tid,
      name: task?.title || tid,
      pct,
      startedMs,
    }],
  };
}

export function renderAutoModeStrip({ autoState, backlog, onViewAll, now = Date.now() }) {
  const wrap = document.createElement('div');
  wrap.className = 'kanban-strip';
  wrap.dataset.cmp = 'auto-mode-strip';
  paintStrip(wrap, { autoState, backlog, onViewAll, now });
  return wrap;
}

export function updateAutoModeStrip(el, { autoState, backlog, onViewAll, now = Date.now() }) {
  if (!el) return;
  paintStrip(el, { autoState, backlog, onViewAll, now });
}

function paintStrip(el, { autoState, backlog, onViewAll, now }) {
  const { runs, sessionStartedMs } = runsFromAutoState(autoState, backlog);
  el.replaceChildren();

  if (!autoState || !autoState.mode || !runs.length) {
    el.hidden = true;
    return;
  }
  el.hidden = false;

  const title = document.createElement('div');
  title.className = 'kanban-strip-title';
  title.innerHTML = `
    <span class="kanban-strip-spinner" aria-hidden="true"></span>
    <span class="live-dot"></span>
    Auto-mode · ${runs.length} running
  `;
  el.appendChild(title);

  if (sessionStartedMs) {
    const t = document.createElement('div');
    t.className = 'kanban-strip-session-time';
    t.textContent = `running ${formatElapsed(now - sessionStartedMs)}`;
    el.appendChild(t);
  }

  const runsEl = document.createElement('div');
  runsEl.className = 'kanban-strip-runs';
  for (const r of runs) {
    const row = document.createElement('div');
    row.className = 'kanban-strip-run';
    row.innerHTML = `
      <span class="id">${escapeHtml(r.id || '?')}</span>
      <span class="name">${escapeHtml(r.name || '')}</span>
      <span class="mini-bar"><i style="width:${r.pct}%"></i></span>
      <span class="pct">${r.pct}%</span>
      <span class="elapsed">${r.startedMs ? formatElapsed(now - r.startedMs) : ''}</span>
    `;
    row.addEventListener('click', () => {
      if (r.id) location.hash = '#/task/' + encodeURIComponent(r.id);
    });
    runsEl.appendChild(row);
  }
  el.appendChild(runsEl);

  const action = document.createElement('div');
  action.className = 'kanban-strip-action';
  action.textContent = 'view all →';
  action.addEventListener('click', () => {
    if (onViewAll) onViewAll();
    else location.hash = '#/auto';
  });
  el.appendChild(action);
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
