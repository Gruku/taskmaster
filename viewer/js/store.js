// In-memory store. Screens read via getters and subscribe to keys.
// Polling is initiated by main.js, not here.

import { getTask, getTaskRelated } from './api.js';

const state = {
  backlog: null,    // parsed backlog YAML object
  prefs: null,      // viewer prefs
  identity: null,   // {root, version}
  autoState: null,  // populated by Plan 6
  lessons: null,    // lesson list; populated by lessons.js
  issues: null,     // issue list; populated by issues.js
};

const subscribers = new Map(); // key → Set<callback>

function emit(key) {
  const subs = subscribers.get(key);
  if (!subs) return;
  for (const cb of subs) {
    try { cb(state[key]); } catch (e) { console.error('store sub error', key, e); }
  }
}

export const store = {
  getBacklog:  () => state.backlog,
  getPrefs:    () => state.prefs,
  getIdentity: () => state.identity,
  getAutoState:() => state.autoState,
  getLessons:  () => state.lessons,
  getIssues:   () => state.issues,

  setBacklog:  (b) => { state.backlog = b;  emit('backlog'); },
  setPrefs:    (p) => { state.prefs = p;    emit('prefs'); },
  setIdentity: (i) => { state.identity = i; emit('identity'); },
  setAutoState:(a) => { state.autoState = a; emit('autoState'); },
  setLessons:  (v) => { state.lessons = v || []; emit('lessons'); },
  setIssues:   (v) => { state.issues = v || [];  emit('issues'); },

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
