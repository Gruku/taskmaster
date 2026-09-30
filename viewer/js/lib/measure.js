// Opt-in User Timing for reproducible browser benchmarks. Normal sessions do
// not retain performance entries or pay for measurement wrappers.
export function beginMeasure() {
  return globalThis.__TM_MEASURE__ ? performance.now() : null;
}

export function endMeasure(name, start) {
  if (start !== null) performance.measure(`tm:${name}`, {start, end: performance.now()});
}

export function measureSync(name, fn) {
  const start = beginMeasure();
  try { return fn(); }
  finally { endMeasure(name, start); }
}
