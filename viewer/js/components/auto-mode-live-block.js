// Per-card auto-mode live block.
// Shows: pulse · step text (e.g. "step 3/5 · IMPLEMENT") · step bar · elapsed.

import { formatElapsed, isoToMs } from '../lib/time.js';

const STAGE_ORDER = ['PICK', 'IMPLEMENT', 'REVIEW', 'HANDOVER_STUB', 'COMPLETE'];

export function renderAutoModeLiveBlock({ autoState, now = Date.now() } = {}) {
  if (!autoState || !autoState.cursor) return null;

  const stage    = autoState.cursor.stage || 'PICK';
  const stageIdx = Math.max(0, STAGE_ORDER.indexOf(stage));
  const total    = STAGE_ORDER.length;
  const pct      = Math.round(((stageIdx + 0.5) / total) * 100);

  const startedMs = isoToMs(autoState.started_at);
  const elapsedMs = startedMs ? Math.max(0, now - startedMs) : 0;

  const wrap = document.createElement('div');
  wrap.className = 'card-live';

  const row = document.createElement('div');
  row.className = 'card-live-row';
  row.innerHTML = `
    <span class="pulse"></span>
    <span class="step-text">step ${stageIdx + 1}/${total} · ${escapeHtml(stage)}</span>
    <span class="elapsed">${formatElapsed(elapsedMs)}</span>
  `;
  wrap.appendChild(row);

  const bar = document.createElement('div');
  bar.className = 'card-live-bar';
  bar.innerHTML = `<i style="width:${pct}%"></i>`;
  wrap.appendChild(bar);

  return wrap;
}

function escapeHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
}
