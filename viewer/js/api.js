// Thin HTTP client for /api/* endpoints. All viewer mutations go through here.

const BASE = ''; // same-origin

async function http(method, path, body) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  const resp = await fetch(BASE + path, init);
  if (!resp.ok) {
    const text = await resp.text().catch(() => '');
    throw new Error(`${method} ${path} → ${resp.status}: ${text}`);
  }
  const ctype = resp.headers.get('Content-Type') || '';
  if (ctype.includes('application/json')) {
    try {
      return await resp.json();
    } catch (e) {
      throw new Error(`${method} ${path} → JSON parse failed: ${e.message}`);
    }
  }
  if (ctype.includes('text/yaml') || path.endsWith('.yaml')) return resp.text();
  return resp.text();
}

export async function getTask(id) {
  const resp = await fetch(`/api/task/${encodeURIComponent(id)}`);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error || `task ${id} not found`);
  }
  return resp.json();
}

export async function getTaskRelated(id) {
  const resp = await fetch(`/api/task/${encodeURIComponent(id)}/related`);
  if (!resp.ok) {
    const body = await resp.json().catch(() => ({}));
    throw new Error(body.error || `related for ${id} not found`);
  }
  return resp.json();
}

export const api = {
  identity:        ()    => http('GET', '/api/identity'),
  backlog:         ()    => http('GET', '/api/backlog'),
  backlogYaml:     ()    => http('GET', '/backlog.yaml'),
  prefs:           ()    => http('GET', '/api/viewer/prefs'),
  savePrefs:       (p)   => http('PUT', '/api/viewer/prefs', p),
  autoState:       ()    => http('GET', '/api/auto/state').then(r => r && r.state),
  getTask,
  getTaskRelated,

  async getRecentEvents(since) {
    const u = new URL('/api/dashboard/recent-events', location.origin);
    u.searchParams.set('since', since);
    const r = await fetch(u);
    if (!r.ok) throw new Error(`recent-events: ${r.status}`);
    return r.json();
  },

  async getLastSession() {
    const r = await fetch('/api/sessions/last');
    if (!r.ok) return null;
    return r.json();
  },

  async listIssues(filter = {}) {
    const u = new URL('/api/issues', location.origin);
    for (const [k, v] of Object.entries(filter)) u.searchParams.set(k, v);
    const r = await fetch(u);
    if (!r.ok) return [];
    return r.json();
  },

  async listLessons(filter = {}) {
    const u = new URL('/api/lessons', location.origin);
    for (const [k, v] of Object.entries(filter)) u.searchParams.set(k, v);
    const r = await fetch(u);
    if (!r.ok) return [];
    return r.json();
  },

  async getRecentCommits({ limit = 8 } = {}) {
    const r = await fetch(`/api/git/commits?limit=${limit}`);
    if (!r.ok) return [];
    return r.json();
  },

  async getBuildTestPulse() {
    const r = await fetch('/api/build-test-pulse');
    if (!r.ok) return { build: 'unknown', tests: { passed: 0, failed: 0, total: 0 }, ts: null };
    return r.json();
  },

  async getAutoState() {
    const r = await fetch('/api/auto/state');
    if (!r.ok) return { running: [], hooks: {} };
    return r.json();
  },

  async quickCapture(text) {
    const r = await fetch('/api/quick-capture', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ text }),
    });
    if (!r.ok) throw new Error(`quick-capture: ${r.status}`);
    return r.json();
  },

  // Plans 5/6 add: reinforceLesson, getRecap, putRecap, putAutoState, etc.
};

// --- Sessions / Recap (Plan 5a) -------------------------------------------

export async function listSessions() {
  const r = await fetch('/api/sessions');
  if (!r.ok) throw new Error(`listSessions: ${r.status}`);
  return r.json();
}

export async function getSessionDetail(sid) {
  const r = await fetch(`/api/sessions/${encodeURIComponent(sid)}`);
  if (!r.ok) throw new Error(`getSessionDetail(${sid}): ${r.status}`);
  return r.json();
}

export async function getRecap(sid) {
  const r = await fetch(`/api/recap/${encodeURIComponent(sid)}`);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`getRecap(${sid}): ${r.status}`);
  return r.json();
}

export async function putRecap(sid, payload) {
  const r = await fetch(`/api/recap/${encodeURIComponent(sid)}`, {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(`putRecap(${sid}): ${r.status}`);
  return r.json();
}

export async function getSnapshotDiff(fromId, toId) {
  const u = `/api/snapshots/diff?from=${encodeURIComponent(fromId)}&to=${encodeURIComponent(toId)}`;
  const r = await fetch(u);
  if (!r.ok) throw new Error(`getSnapshotDiff(${fromId}→${toId}): ${r.status}`);
  return r.json();
}

export async function savePrefs(patch) {
  const r = await fetch('/api/viewer/prefs', {
    method: 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error(`savePrefs: ${r.status}`);
  return r.json();
}

// --- Lessons ---------------------------------------------------------------
export async function getLessons() {
  const r = await fetch('/api/lessons');
  if (!r.ok) throw new Error(`getLessons failed: ${r.status}`);
  return r.json();
}

export async function reinforceLesson(lessonId, { source = 'user', note = '' } = {}) {
  const r = await fetch(`/api/lessons/${encodeURIComponent(lessonId)}/reinforce`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ source, note }),
  });
  if (!r.ok) throw new Error(`reinforceLesson failed: ${r.status}`);
  return r.json();
}

// --- Issues ----------------------------------------------------------------
export async function getIssues({ includeResolved = true } = {}) {
  const qs = includeResolved ? '' : '?include_resolved=false';
  const r = await fetch(`/api/issues${qs}`);
  if (!r.ok) throw new Error(`getIssues failed: ${r.status}`);
  return r.json();
}
