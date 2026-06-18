// Flight Log — chronological waterfall of auto-mode events.
// Newest at top. Active row gets blue tint + pulsing badge.

const STAGE_PILL_CLASS = {
  PICK: 'flog-pill--pick',
  IMPLEMENT: 'flog-pill--implement',
  REVIEW: 'flog-pill--review',
  HANDOVER_STUB: 'flog-pill--handover',
  COMPLETE: 'flog-pill--complete',
};

export function renderFlightLog(root, { events = [], cursorStage = null } = {}) {
  root.innerHTML = '';
  const wrap = document.createElement('div');
  wrap.className = 'flog-wrap';
  root.appendChild(wrap);

  if (!events.length) {
    const empty = document.createElement('div');
    empty.className = 'flog-empty';
    empty.textContent = 'No events yet.';
    wrap.appendChild(empty);
    return () => { root.innerHTML = ''; };
  }

  // Newest first
  const sorted = [...events].sort((a, b) => (b.ts || '').localeCompare(a.ts || ''));

  for (const ev of sorted) {
    const row = document.createElement('div');
    const isActive = ev.stage === cursorStage && ev.kind === 'stage_enter';
    row.className = 'flog-row' + (isActive ? ' flog-row--active' : '');

    row.innerHTML = `
      <span class="flog-ts">${shortTs(ev.ts)}</span>
      <span class="flog-pill ${STAGE_PILL_CLASS[ev.stage] || ''}">${ev.stage || '—'}</span>
      <span class="flog-msg">${escape(ev.msg ?? '')}</span>
      ${isActive ? '<span class="flog-active-badge">active</span>' : ''}
    `;

    if (Array.isArray(ev.subagent_runs) && ev.subagent_runs.length) {
      const sub = document.createElement('div');
      sub.className = 'flog-subruns';
      for (const sr of ev.subagent_runs) {
        const srRow = document.createElement('div');
        srRow.className = 'flog-subrun';
        srRow.innerHTML = `
          <span class="flog-subrun-type">${escape(sr.type ?? '')}</span>
          <span class="flog-subrun-msg">${escape(sr.msg ?? '')}</span>
          <span class="flog-subrun-ts">${shortTs(sr.ts)}</span>
        `;
        sub.appendChild(srRow);
      }
      row.appendChild(sub);
    }
    wrap.appendChild(row);
  }
  return () => { root.innerHTML = ''; };
}

function shortTs(iso) {
  if (!iso) return '';
  // Return HH:MM:SS from ISO.
  const m = /T(\d{2}:\d{2}:\d{2})/.exec(iso);
  return m ? m[1] : iso;
}

function escape(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
