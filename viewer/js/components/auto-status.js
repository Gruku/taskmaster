// Header status pill — visible on every screen when auto-mode is running.
// Mounts into #topbar-actions, subscribes to store.autoState.

import { isoToMs, formatDurationCompact } from '../lib/time.js';
import { isAutoRunning } from '../lib/auto-state.js';

const STAGE_LABELS = {
  PICK: 'Pick', PLAN: 'Plan', SPEC_REVIEW: 'Review',
  TESTS: 'Tests', IMPLEMENT: 'Implement',
  TEST: 'Test', REVIEW: 'Review', COMPLETE: 'Done',
};

export function mountAutoStatus(root, ctx) {
  if (!root) return () => {};
  const pill = document.createElement('button');
  pill.className = 'auto-status-pill';
  pill.type = 'button';
  pill.setAttribute('aria-label', 'Open Auto Mode');
  pill.hidden = true;
  pill.addEventListener('click', () => { window.location.hash = '#/auto'; });
  root.appendChild(pill);

  function paint(state) {
    if (!isAutoRunning(state) || !state.cursor) { pill.hidden = true; return; }
    pill.hidden = false;
    const stage = STAGE_LABELS[state.cursor.stage] || state.cursor.stage || '';
    const startMs = isoToMs(state.started_at);
    const elapsed = startMs != null ? formatDurationCompact(Date.now() - startMs) : '';
    pill.innerHTML = `
      <span class="auto-status-dot"></span>
      <span class="auto-status-text">Auto Mode${stage ? ` · ${stage}` : ''}${elapsed ? ` · ${elapsed}` : ''}</span>
    `;
  }

  paint(ctx.store.getAutoState?.() ?? null);
  const unsub = ctx.store.subscribe?.('autoState', paint);
  return () => { unsub?.(); pill.remove(); };
}
