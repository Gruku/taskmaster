// In-memory store. Screens read via getters and subscribe to keys.
// Polling is initiated by main.js, not here.

const state = {
  backlog: null,    // parsed backlog YAML object
  prefs: null,      // viewer prefs
  identity: null,   // {root, version}
  autoState: null,  // populated by Plan 6
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

  setBacklog:  (b) => { state.backlog = b;  emit('backlog'); },
  setPrefs:    (p) => { state.prefs = p;    emit('prefs'); },
  setIdentity: (i) => { state.identity = i; emit('identity'); },
  setAutoState:(a) => { state.autoState = a; emit('autoState'); },

  subscribe(key, cb) {
    if (!subscribers.has(key)) subscribers.set(key, new Set());
    subscribers.get(key).add(cb);
    return () => subscribers.get(key).delete(cb);
  },
};
