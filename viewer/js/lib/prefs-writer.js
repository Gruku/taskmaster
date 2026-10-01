// User intent: every preference change reaches the server — a theme choice must survive another
// preference changing within the same debounce window, or while an earlier save is still on the wire.

export function deepMerge(base, patch) {
  for (const [k, v] of Object.entries(patch)) {
    if (v && typeof v === 'object' && !Array.isArray(v) && base[k] && typeof base[k] === 'object') {
      deepMerge(base[k], v);
    } else {
      base[k] = v;
    }
  }
  return base;
}

// queue(patch) merges into one pending object; `delayMs` after the last patch it is sent as one save.
// Saves never overlap: a patch that arrives during a save goes out in the next one.
export function createPrefsWriter({ save, delayMs, setTimer = setTimeout, clearTimer = clearTimeout, onError = () => {} }) {
  let pending = null;
  let timer = null;
  let inFlight = false;
  let due = false;      // the debounce elapsed during a save; send as soon as that save ends

  function send() {
    if (!pending) return;
    if (inFlight) { due = true; return; }
    const batch = pending;
    // Taken before the save starts, so a patch queued from here on starts a fresh batch.
    pending = null;
    due = false;
    inFlight = true;
    let result;
    try { result = Promise.resolve(save(batch)); } catch (e) { result = Promise.reject(e); }
    result.catch(onError).then(() => {
      inFlight = false;
      if (due) send();
    });
  }

  return {
    queue(patch) {
      // Cloned: a later merge must not write into the caller's object.
      pending = deepMerge(pending || {}, structuredClone(patch));
      if (timer) clearTimer(timer);
      due = false;
      timer = setTimer(() => { timer = null; send(); }, delayMs);
    },
  };
}
