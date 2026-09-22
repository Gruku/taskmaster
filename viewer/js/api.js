// Thin HTTP client for /api/* endpoints. All viewer mutations go through here.

const BASE = ''; // same-origin
import { beginMeasure, endMeasure } from './lib/measure.js';

async function http(method, path, body, options = {}) {
  const init = { method, headers: {...options.headers}, cache: 'no-store' };
  if (body !== undefined) {
    init.headers['Content-Type'] = 'application/json';
    init.body = JSON.stringify(body);
  }
  // Attach If-Match for write methods if we have an etag for this resource.
  if (method === 'PATCH' || method === 'PUT' || (method === 'POST' && path.endsWith('/archive'))) {
    const m = path.match(/^\/api\/tasks\/([^/]+)/);
    if (m) {
      const { store } = await import('./store.js');
      const et = store.getEtag(`task:${decodeURIComponent(m[1])}`);
      if (et) init.headers['If-Match'] = et;
    }
  }
  const fetchStart = beginMeasure();
  const resp = await fetch(BASE + path, init);
  endMeasure('fetch', fetchStart);
  if (resp.status === 304) return {status: 304};
  if (!resp.ok && 'fallback' in options) return options.fallback;
  // Capture returned ETag for next time.
  const et = resp.headers.get('ETag');
  if (et && options.capture !== false) {
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
      const raw = await resp.text();
      const parseStart = beginMeasure();
      const data = JSON.parse(raw);
      endMeasure('parse', parseStart);
      return data;
    } catch (e) {
      throw new Error(`${method} ${path} → JSON parse failed: ${e.message}`);
    }
  }
  if (ctype.includes('text/yaml') || path.endsWith('.yaml')) return resp.text();
  return resp.text();
}

export async function getTask(id) {
  return http('GET', `/api/task/${encodeURIComponent(id)}`);
}

export async function getTaskRelated(id) {
  return http('GET', `/api/task/${encodeURIComponent(id)}/related`);
}

export const getTaskDetail = id => http('GET', `/api/task/${encodeURIComponent(id)}/detail`, undefined, {capture: false});

export async function getEpic(id) {
  return http('GET', `/api/epic/${encodeURIComponent(id)}`);
}

export const api = {
  // Generic HTTP helpers — screens needing arbitrary endpoints (e.g. continuity
  // dashboard hitting /api/continuity, /api/decisions/*) route through these
  // instead of growing the named-method surface.
  get:             (path)        => http('GET', path),
  post:            (path, body)  => http('POST', path, body ?? {}),
  identity:        ()    => http('GET', '/api/identity'),
  backlog:         ()    => http('GET', '/api/backlog'),
  board: (since) => http('GET', '/api/board' + (since ? `?since=${encodeURIComponent(since)}` : ''), undefined,
    {headers: since ? {'If-None-Match': `"${since}"`} : {}}),
  prefs:           ()    => http('GET', '/api/viewer/prefs'),
  savePrefs:       (p)   => http('PUT', '/api/viewer/prefs', p),
  getTask,
  getEpic,
  getTaskRelated,
  getTaskDetail,
  patchTask:    (id, patch) => http('PATCH', `/api/tasks/${encodeURIComponent(id)}`, patch),
  putTask:      (id, full)  => http('PUT',   `/api/tasks/${encodeURIComponent(id)}`, full),
  createTask:   (payload)   => http('POST',  '/api/tasks', payload),
  archiveTask:  (id)        => http('POST',  `/api/tasks/${encodeURIComponent(id)}/archive`, {}),
  validateTask: (taskId, patch) => http('POST', '/api/tasks/validate', { task_id: taskId, patch }),

  async getRecentEvents(since) {
    const u = new URL('/api/dashboard/recent-events', location.origin);
    u.searchParams.set('since', since);
    return http('GET', u.pathname + u.search);
  },

  async getLastSession() {
    return http('GET', '/api/sessions/last', undefined, {fallback: null});
  },

  async listIssues(filter = {}) {
    const u = new URL('/api/issues', location.origin);
    for (const [k, v] of Object.entries(filter)) u.searchParams.set(k, v);
    return http('GET', u.pathname + u.search, undefined, {fallback: []});
  },

  async getRecentCommits({ limit = 8 } = {}) {
    return http('GET', `/api/git/commits?limit=${limit}`, undefined, {fallback: []});
  },

  async getBuildTestPulse() {
    return http('GET', '/api/build-test-pulse', undefined,
      {fallback: { build: 'unknown', tests: { passed: 0, failed: 0, total: 0 }, ts: null }});
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

  // Plans 5/6 add: putAutoState, etc.

  // ── Notes (Desk) ──────────────────────────────────────────────
  notes: (includeArchived = false) =>
    http('GET', `/api/notes${includeArchived ? '?include_archived=1' : ''}`),
  createNote: (text, pinned = false) => http('POST', '/api/notes', { text, pinned }),
  updateNote: (id, patch) => http('POST', `/api/notes/${encodeURIComponent(id)}/update`, patch),
  archiveNote: (id) => http('POST', `/api/notes/${encodeURIComponent(id)}/archive`, {}),
};

// --- Sessions (Plan 5a) -------------------------------------------

export async function listSessions() {
  return http('GET', '/api/sessions');
}

export async function getSessionDetail(sid) {
  return http('GET', `/api/sessions/${encodeURIComponent(sid)}`);
}

export async function listThreads() {
  return http('GET', '/api/threads');
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

// --- Issues ----------------------------------------------------------------
export async function getIssues({ includeResolved = true } = {}) {
  const qs = includeResolved ? '' : '?include_resolved=false';
  return http('GET', `/api/issues${qs}`);
}

// ── Bugs ─────────────────────────────────────────────────────────────────

export async function listBugs({ status, found_in, include_archive } = {}) {
  const params = new URLSearchParams();
  if (status) params.set('status', status);
  if (found_in) params.set('found_in', found_in);
  if (include_archive) params.set('include_archive', '1');
  const qs = params.toString();
  return http('GET', `/api/bugs${qs ? '?' + qs : ''}`);
}

export async function getBug(bugId) {
  return http('GET', `/api/bugs/${encodeURIComponent(bugId)}`);
}

export async function createBug(payload) {
  const r = await fetch('/api/bugs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  if (!r.ok) throw new Error(`createBug failed: ${r.status}`);
  return r.json();
}

export async function updateBug(bugId, patch) {
  const r = await fetch(`/api/bugs/${encodeURIComponent(bugId)}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(patch),
  });
  if (!r.ok) throw new Error(`updateBug ${bugId} failed: ${r.status}`);
  return r.json();
}

export async function archiveBug(bugId) {
  const r = await fetch(`/api/bugs/${encodeURIComponent(bugId)}/archive`, { method: 'POST' });
  if (!r.ok) throw new Error(`archiveBug ${bugId} failed: ${r.status}`);
  return r.json();
}

export async function bugPatternScan({ mode = 'all' } = {}) {
  const r = await fetch('/api/bugs/pattern-scan', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ mode }),
  });
  if (!r.ok) throw new Error(`bugPatternScan failed: ${r.status}`);
  return r.json();
}

export async function promoteBugs({ bug_ids, title, severity, evidence_text, components, body }) {
  const r = await fetch('/api/bugs/promote', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ bug_ids, title, severity, evidence_text, components, body }),
  });
  if (!r.ok) throw new Error(`promoteBugs failed: ${r.status}`);
  return r.json();
}
