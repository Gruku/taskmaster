// User intent: one answer, everywhere, to "did this field change?" — the form, the inline field and the conflict banner
// agree, so an empty field never counts as an edit in one place and not another. Imports nothing.

// null, undefined, '', [] and {} are one emptiness; lists compare in order, maps by key.
export function normal(v) {
  if (v == null || v === '') return null;
  if (Array.isArray(v)) return v.length ? v.map(normal) : null;
  if (typeof v === 'object') {
    const keys = Object.keys(v).sort();
    return keys.length ? Object.fromEntries(keys.map((k) => [k, normal(v[k])])) : null;
  }
  return v;
}

export function sameValue(a, b) {
  return JSON.stringify(normal(a)) === JSON.stringify(normal(b));
}
