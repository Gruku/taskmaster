// Header status pill — visible on every screen when auto-mode is running.
// Mounts into #topbar-actions, subscribes to store.autoState.

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
    if (!state || !state.cursor) { pill.hidden = true; return; }
    pill.hidden = false;
    const stage = STAGE_LABELS[state.cursor.stage] || state.cursor.stage || '';
    const elapsed = formatElapsed(state.started_at);
    pill.innerHTML = `
      <span class="auto-status-dot"></span>
      <span class="auto-status-text">Auto Mode${stage ? ` · ${stage}` : ''}${elapsed ? ` · ${elapsed}` : ''}</span>
    `;
  }

  paint(ctx.store.getAutoState?.() ?? null);
  const unsub = ctx.store.subscribe?.('autoState', paint);
  return () => { unsub?.(); pill.remove(); };
}

function formatElapsed(iso) {
  if (!iso) return '';
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return '';
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h}h${m}m`;
  if (m) return `${m}m`;
  return `${s}s`;
}
