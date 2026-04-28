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
  // Plans 5/6 add: reinforceLesson, getRecap, putRecap, putAutoState, etc.
};
