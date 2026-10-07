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

const isPlain = (v) => !!v && typeof v === 'object' && !Array.isArray(v);

// `batch` without the leaf keys `newer` sets (an object in one and anything else in the other counts as covered);
// null when nothing is left.
function uncovered(batch, newer) {
  const out = {};
  for (const [k, v] of Object.entries(batch)) {
    if (!(k in newer)) out[k] = v;
    else if (isPlain(v) && isPlain(newer[k])) {
      const rest = uncovered(v, newer[k]);
      if (rest) out[k] = rest;
    }
  }
  return Object.keys(out).length ? out : null;
}

// queue(patch) merges into one pending object; `delayMs` after the last patch it is sent as one save.
// Saves never overlap: a patch that arrives during a save goes out in the next one.
// A failed save is sent again after retryDelayMs(n) with whatever was queued since merged over it; after `retries`
// failed retries in a row it is given up and onError(err, { dropped }) is told once.
// flush() is for a page going away: what is pending goes out at once with keepalive, overlap or not, and is not retried.
export function createPrefsWriter({ save, delayMs, retries = 3, retryDelayMs = (n) => delayMs * 2 ** n,
                                    setTimer = setTimeout, clearTimer = clearTimeout, onError = () => {} }) {
  let pending = null;
  let timer = null;
  let retryTimer = null;
  let inFlight = false;
  let due = false;      // the debounce elapsed during a save; send as soon as that save ends
  let failures = 0;
  let flushedInFlight = null;   // what flush() sent while the regular save was out: newer than that save

  function call(batch, opts) {
    try { return Promise.resolve(save(batch, opts)); } catch (e) { return Promise.reject(e); }
  }

  function send() {
    if (!pending) return;
    if (inFlight) { due = true; return; }
    const batch = pending;
    // Taken before the save starts, so a patch queued from here on starts a fresh batch.
    pending = null;
    due = false;
    inFlight = true;
    flushedInFlight = null;
    call(batch, { keepalive: false }).then(() => { failures = 0; }, (err) => {
      // flush() sent newer values meanwhile: only the keys it did not cover are retried, never the older ones over them.
      // (flush() reset the count, so this failure is the first of the remainder's.)
      const left = flushedInFlight ? uncovered(batch, flushedInFlight) : batch;
      if (!left) return;
      if (failures >= retries) {
        failures = 0;
        onError(err, { dropped: left });
        return;
      }
      failures++;
      // Under what was queued since: a newer value of the same key wins.
      pending = deepMerge(left, pending ?? {});
      // The retry carries everything pending, so a debounce still waiting would only send it early.
      if (timer) { clearTimer(timer); timer = null; }
      due = false;
      retryTimer = setTimer(() => { retryTimer = null; send(); }, retryDelayMs(failures));
    }).then(() => {
      inFlight = false;
      flushedInFlight = null;
      if (due) send();
    });
  }

  return {
    queue(patch) {
      // Cloned: a later merge must not write into the caller's object.
      pending = deepMerge(pending || {}, structuredClone(patch));
      // Waiting on a retry: this patch goes out with it.
      if (retryTimer) return;
      if (timer) clearTimer(timer);
      due = false;
      timer = setTimer(() => { timer = null; send(); }, delayMs);
    },
    flush() {
      if (timer) { clearTimer(timer); timer = null; }
      if (retryTimer) { clearTimer(retryTimer); retryTimer = null; }
      due = false;
      if (!pending) return;
      const batch = pending;
      pending = null;
      failures = 0;
      if (inFlight) flushedInFlight = deepMerge(flushedInFlight ?? {}, structuredClone(batch));
      call(batch, { keepalive: true }).catch((err) => onError(err, { dropped: batch }));
    },
  };
}
