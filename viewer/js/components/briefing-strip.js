// Italic-serif "Welcome back…" strip per spec §3.4.
// Pulls counts from /api/dashboard/recent-events; falls back to a calm placeholder while loading.

const escapeAttr = (s) => String(s == null ? '' : s).replace(/[&<>"']/g, c =>
  ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function renderPhasePips(phases) {
  if (!phases || !phases.length) return '';
  return phases.map(p => {
    const status = p.status || 'future';
    return `<span class="dash-briefing__pip dash-briefing__pip--${escapeAttr(status)}" title="${escapeAttr(p.name)} · ${escapeAttr(status)}"></span>`;
  }).join('');
}

export function createBriefingStrip({ store, api, prefs }) {
  const root = document.createElement('section');
  root.className = 'dash-briefing';

  const sentence = document.createElement('div');
  sentence.className = 'dash-briefing__sentence';
  sentence.innerHTML = '<em>Welcome back.</em> Loading recent activity…';

  const meta = document.createElement('div');
  meta.className = 'dash-briefing__meta';
  meta.innerHTML = '<span class="dash-briefing__project"></span> · <span class="dash-briefing__phase"></span><span class="dash-briefing__phases" aria-hidden="true"></span> · <span class="dash-briefing__kbd">⌘K</span>';

  root.append(sentence, meta);

  async function refresh() {
    try {
      const since = (prefs && prefs.dashboard && prefs.dashboard.last_seen_at) || new Date(Date.now() - 24 * 3600 * 1000).toISOString();
      const events = await api.getRecentEvents(since);
      const closed = events.filter(e => e.kind === 'task_closed').length;
      const issues = events.filter(e => e.kind === 'issue_opened').length;
      const lessons = events.filter(e => e.kind === 'lesson_promoted').length;
      sentence.innerHTML = `<em>Welcome back.</em> Since you left, <em>${closed}</em> tasks closed, <em>${issues}</em> new issues, <em>${lessons}</em> lessons promoted.`;
    } catch (err) {
      sentence.innerHTML = '<em>Welcome back.</em> No recent-events feed available yet.';
    }
    const backlog = (store.getBacklog && store.getBacklog()) || {};
    const project = (backlog.meta && backlog.meta.project) || 'untitled';
    const phases = backlog.phases || [];
    const activePhase = phases.find(p => p.status === 'active');
    root.querySelector('.dash-briefing__project').textContent = project;
    root.querySelector('.dash-briefing__phase').textContent = activePhase ? activePhase.name : 'no active phase';
    root.querySelector('.dash-briefing__phases').innerHTML = renderPhasePips(phases);
  }

  refresh();
  const unsub = store.subscribe ? store.subscribe('backlog', refresh) : () => {};

  return {
    root,
    refresh,
    destroy() { if (typeof unsub === 'function') unsub(); },
  };
}
