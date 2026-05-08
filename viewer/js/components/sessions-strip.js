import { isoToMs, formatDurationCompact } from '../lib/time.js';
import { isAutoRunning } from '../lib/auto-state.js';

/**
 * Render a tab strip of parallel auto-mode sessions.
 * @param {HTMLElement} root
 * @param {object} opts
 * @param {Array} opts.sessions      List of session objects.
 * @param {string} opts.activeSid    Currently inspected session id.
 * @param {(sid:string)=>void} opts.onSelect
 */
export function renderSessionsStrip(root, { sessions = [], activeSid, onSelect } = {}) {
  root.innerHTML = '';
  if (!sessions.length) return () => {};
  const wrap = document.createElement('div');
  wrap.className = 'sstrip';
  root.appendChild(wrap);

  for (const s of sessions) {
    const status = sessionStatus(s);
    const tab = document.createElement('button');
    tab.className = `sstrip-tab sstrip-tab--${status}` + (s.session_id === activeSid ? ' on' : '');
    tab.dataset.sid = s.session_id;
    tab.dataset.status = status;
    tab.innerHTML = `
      <span class="sstrip-dot sstrip-dot--${status}" aria-hidden="true"></span>
      <span class="sstrip-id">${escape(s.session_id)}</span>
      <span class="sstrip-title">${escape(s.title ?? '')}</span>
      <span class="sstrip-elapsed">${elapsed(s)}</span>
    `;
    tab.addEventListener('click', () => onSelect?.(s.session_id));
    wrap.appendChild(tab);
  }
  return () => { root.innerHTML = ''; };
}

// Derive a status keyword from raw session state. Mirrors the running/done
// distinction the rest of the auto-mode UI uses (isAutoRunning).
function sessionStatus(s) {
  const failed = Array.isArray(s?.failed) ? s.failed.length : 0;
  if (!isAutoRunning(s)) return failed > 0 ? 'failed' : 'done';
  return failed > 0 ? 'partial' : 'running';
}

function elapsed(s) {
  const startMs = isoToMs(s?.started_at);
  if (startMs == null) return '';
  // Freeze the counter once the session has stopped — otherwise a finished run
  // keeps "ticking" forever in the strip and reads as still in flight.
  const endMs = isAutoRunning(s) ? Date.now() : (isoToMs(s?.ended_at) ?? startMs);
  return formatDurationCompact(endMs - startMs);
}

function escape(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
