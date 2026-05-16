// Thin HTTP client for /api/* endpoints. All viewer mutations go through here.

const BASE = ''; // same-origin

async function http(method, path, body) {
  const init = { method, headers: {} };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  // Attach If-Match for write methods if we have an etag for this resource.
  if (method === 'PATCH' || method === 'PUT') {
    const m = path.match(/^\/api\/tasks\/([^/]+)/);
    if (m) {
      const { store } = await import('./store.js');
      const et = store.getEtag(`task:${decodeURIComponent(m[1])}`);
      if (et) init.headers['If-Match'] = et;
    }
  }
  const resp = await fetch(BASE + path, init);
  // Capture returned ETag for next time.
  const et = resp.headers.get('ETag');
  if (et) {
    const { store } = await import('./store.js');
    const m1 = path.match(/^\/api\/task\/([^/]+)$/);  // GET single task
    const m2 = path.match(/^\/api\/tasks\/([^/]+)/);   // PATCH/PUT
    const id = (m1 || m2)?.[1];
    if (id) store.setEtag(`task:${decodeURIComponent(id)}`, et.replace(/^"|"$/g, ''));
    if (path === '/api/backlog') store.setEtag('backlog', et.replace(/^"|"$/g, ''));
  }
  if (resp.status === 409) {
    const j = await resp.json();
    const err = new Error('stale');
    err.code = 409;
    err.current = j.current;
    err.current_etag = j.current_etag;
    throw err;
  }
  if (resp.status === 422) {
    const j = await resp.json();
    const err = new Error('validation failed');
    err.code = 422;
    err.errors = j.errors || {};
    throw err;
  }
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
  // Generic HTTP helpers — screens needing arbitrary endpoints (e.g. continuity
  // dashboard hitting /api/continuity, /api/decisions/*) route through these
  // instead of growing the named-method surface.
  get:             (path)        => http('GET', path),
  post:            (path, body)  => http('POST', path, body ?? {}),
  identity:        ()    => http('GET', '/api/identity'),
  backlog:         ()    => http('GET', '/api/backlog'),
  prefs:           ()    => http('GET', '/api/viewer/prefs'),
  savePrefs:       (p)   => http('PUT', '/api/viewer/prefs', p),
  autoState:       ()    => http('GET', '/api/auto/state'),
  getTask,
  getTaskRelated,
  patchTask:    (id, patch) => http('PATCH', `/api/tasks/${encodeURIComponent(id)}`, patch),
  putTask:      (id, full)  => http('PUT',   `/api/tasks/${encodeURIComponent(id)}`, full),
  createTask:   (payload)   => http('POST',  '/api/tasks', payload),
  archiveTask:  (id)        => http('POST',  `/api/tasks/${encodeURIComponent(id)}/archive`, {}),
  validateTask: (taskId, patch) => http('POST', '/api/tasks/validate', { task_id: taskId, patch }),

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

// ---- Auto Mode (Plan 6) -------------------------------------------------

export async function autoListSessions() {
  const r = await fetch('/api/auto/sessions');
  if (!r.ok) throw new Error(`autoListSessions ${r.status}`);
  return (await r.json()).sessions;
}

export async function autoSession(sid) {
  const r = await fetch(`/api/auto/sessions/${encodeURIComponent(sid)}`);
  if (r.status === 404) return null;
  if (!r.ok) throw new Error(`autoSession ${r.status}`);
  return await r.json();
}

export async function autoPause(sid) {
  const r = await fetch('/api/auto/pause', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sid }),
  });
  if (!r.ok) throw new Error(`autoPause ${r.status}`);
  return await r.json();
}

export async function autoStop(sid) {
  const r = await fetch('/api/auto/stop', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ session_id: sid }),
  });
  if (!r.ok) throw new Error(`autoStop ${r.status}`);
  return await r.json();
}

export async function autoEvents(sid, since) {
  const qs = new URLSearchParams({ sid });
  if (since) qs.set('since', since);
  const r = await fetch(`/api/auto/events?${qs}`);
  if (!r.ok) throw new Error(`autoEvents ${r.status}`);
  return (await r.json()).events;
}

export async function autoBudget(sid) {
  const r = await fetch(`/api/auto/budget/${encodeURIComponent(sid)}`);
  if (!r.ok) throw new Error(`autoBudget ${r.status}`);
  return await r.json();
}
