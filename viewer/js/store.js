// In-memory store. Screens read via getters and subscribe to keys.
// Polling is initiated by main.js, not here.

import { getTask, getTaskRelated } from './api.js';

const state = {
  backlog: null,           // parsed backlog YAML object
  prefs: null,             // viewer prefs
  identity: null,          // {root, version}
  lessons: null,           // lesson list; populated by lessons.js
  issues: null,            // issue list; populated by issues.js
  ideas: null,             // idea list; populated by ideas.js
  etags: {},               // keyed by `task:<id>` or `backlog`
};

const subscribers = new Map(); // key → Set<callback>

function emit(key) {
  const subs = subscribers.get(key);
  if (!subs) return;
  for (const cb of subs) {
    try { cb(state[key]); } catch (e) { console.error('store sub error', key, e); }
  }
}

function emitValue(key, value) {
  const subs = subscribers.get(key);
  if (!subs) return;
  for (const cb of subs) {
    try { cb(value); } catch (e) { console.error('store sub error', key, e); }
  }
}

// Cheap structural equality for plain JSON shapes returned by the API.
// Used to skip emit when a poll returns the same data — otherwise every
// 3s poll triggers screen subscribers (e.g. kanban paint() →
// boardGrid.replaceChildren()) and destroys scroll position / focus.
function jsonEqual(a, b) {
  if (a === b) return true;
  if (a == null || b == null) return false;
  try { return JSON.stringify(a) === JSON.stringify(b); }
  catch { return false; }
}

export const store = {
  getBacklog:  () => state.backlog,
  getPrefs:    () => state.prefs,
  getIdentity: () => state.identity,
  getLessons:  () => state.lessons,
  getIssues:   () => state.issues,
  getIdeas:    () => state.ideas,

  setBacklog:  (b) => { if (jsonEqual(state.backlog, b)) return; state.backlog = b; emit('backlog'); },
  setPrefs:    (p) => { state.prefs = p;    emit('prefs'); },
  setIdentity: (i) => { state.identity = i; emit('identity'); },
  setLessons:  (v) => { state.lessons = v || []; emit('lessons'); },
  setIssues:   (v) => { state.issues = v || [];  emit('issues'); },
  setIdeas:    (v) => { state.ideas  = v || [];  emit('ideas');  },
  setEtag: (key, etag) => { state.etags[key] = etag; },
  getEtag: (key) => state.etags[key] || null,

  subscribe(key, cb) {
    if (!subscribers.has(key)) subscribers.set(key, new Set());
    subscribers.get(key).add(cb);
    return () => subscribers.get(key).delete(cb);
  },

  getTaskFull,
  getTaskRelatedFull,
  invalidateTask,
};

const _taskCache = new Map();
const _relatedCache = new Map();

export async function getTaskFull(id, { force = false } = {}) {
  if (!force && _taskCache.has(id)) return _taskCache.get(id);
  const data = await getTask(id);
  _taskCache.set(id, data);
  return data;
}

export async function getTaskRelatedFull(id, { force = false } = {}) {
  if (!force && _relatedCache.has(id)) return _relatedCache.get(id);
  const data = await getTaskRelated(id);
  _relatedCache.set(id, data);
  return data;
}

export function invalidateTask(id) {
  _taskCache.delete(id);
  _relatedCache.delete(id);
}

