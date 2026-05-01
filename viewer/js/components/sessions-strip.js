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
    const tab = document.createElement('button');
    tab.className = 'sstrip-tab' + (s.session_id === activeSid ? ' on' : '');
    tab.dataset.sid = s.session_id;
    tab.innerHTML = `
      <span class="sstrip-dot" aria-hidden="true"></span>
      <span class="sstrip-id">${escape(s.session_id)}</span>
      <span class="sstrip-title">${escape(s.title ?? '')}</span>
      <span class="sstrip-elapsed">${elapsed(s.started_at)}</span>
    `;
    tab.addEventListener('click', () => onSelect?.(s.session_id));
    wrap.appendChild(tab);
  }
  return () => { root.innerHTML = ''; };
}

function elapsed(iso) {
  if (!iso) return '';
  const ms = Date.now() - new Date(iso).getTime();
  if (Number.isNaN(ms) || ms < 0) return '';
  const m = Math.floor(ms / 60000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60);
  return `${h}h${m % 60}m`;
}

function escape(s) {
  return String(s).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}
