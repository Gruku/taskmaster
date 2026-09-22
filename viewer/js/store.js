// In-memory store. Screens read via getters and subscribe to keys.
// Polling is initiated by main.js, not here.

import { getTaskDetail, api } from './api.js';
import { applyBoardDelta } from './lib/board-delta.js';
import { activeEpic } from './lib/epics.js';
import { measureSync } from './lib/measure.js';

const state = {
  backlog: null,           // parsed backlog YAML object
  prefs: null,             // viewer prefs
  identity: null,          // {root, version}
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

function publishBoard(board, changed) {
  if (state.backlog?.revision && state.backlog.revision === board.revision) return;
  state.backlog = {...board};
  Object.defineProperty(state.backlog, 'context', {get: () => ({active_epic: activeEpic(state.backlog)})});
  for (const id of changed) invalidateTask(id);
  // Subscribers synchronously rebuild the active board screen. This measures
  // DOM construction, not presentation; the harness also awaits browser frames.
  measureSync('paint', () => emit('backlog'));
  for (const id of changed) emitValue(`task:${id}`, id);
}

let refresh = Promise.resolve();
function refreshBoard(client = api) {
  // Serialize polls and post-edit refreshes so a slower old response can never
  // replace a newer board. Each request takes its token when it actually starts.
  refresh = refresh.catch(() => {}).then(async () => {
    const result = await client.board(state.backlog?.cursor);
    if (result.status === 304) return;
    if (result.since !== undefined) {
      try { store.applyDelta(result); }
      catch { store.setBoard(await client.board()); }
    } else store.setBoard(result);
  });
  return refresh;
}

export const store = {
  getBacklog:  () => state.backlog,
  getPrefs:    () => state.prefs,
  getIdentity: () => state.identity,
  getIssues:   () => state.issues,
  getIdeas:    () => state.ideas,

  setBacklog: (b) => store.setBoard(b),
  setBoard: (b) => measureSync('apply', () => publishBoard(b, new Set([..._detailCache.keys(), ..._inFlight.keys()]))),
  applyDelta: (d) => measureSync('apply', () => publishBoard(applyBoardDelta(state.backlog, d), new Set([...d.tasks_upsert.map(t => t.id), ...d.tasks_remove]))),
  refreshBoard,
  setPrefs:    (p) => { state.prefs = p;    emit('prefs'); },
  setIdentity: (i) => { state.identity = i; emit('identity'); },
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
  getTaskDetailFull,
  invalidateTask,
  beginEdit(id) { _editing.set(id, (_editing.get(id) || 0) + 1); },
  endEdit(id) { const n = (_editing.get(id) || 1) - 1; if (n) _editing.set(id, n); else { _editing.delete(id); emitValue(`task:${id}`, id); } },
  isEditing: id => (_editing.get(id) || 0) > 0,
};

const _detailCache = new Map();
const _inFlight = new Map();
const _generation = new Map();
const _editing = new Map();

export async function getTaskDetailFull(id, {force = false} = {}) {
  if (!force && _detailCache.has(id)) return _detailCache.get(id);
  if (_inFlight.has(id)) return _inFlight.get(id);
  const generation = _generation.get(id) || 0;
  const promise = getTaskDetail(id).then(data => {
    if (generation !== (_generation.get(id) || 0)) return getTaskDetailFull(id, {force: true});
    _detailCache.set(id, data);
    // A fetch is not acceptance of its revision. A view may decline to paint
    // while editing; only the mounted detail may advance its edit token.
    return data;
  }).finally(() => { if (_inFlight.get(id) === promise) _inFlight.delete(id); });
  _inFlight.set(id, promise);
  return promise;
}

export async function getTaskFull(id, { force = false } = {}) {
  return (await getTaskDetailFull(id, {force})).task;
}

export async function getTaskRelatedFull(id, { force = false } = {}) {
  return (await getTaskDetailFull(id, {force})).related;
}

export function invalidateTask(id) {
  _detailCache.delete(id);
  _inFlight.delete(id);
  _generation.set(id, (_generation.get(id) || 0) + 1);
}

