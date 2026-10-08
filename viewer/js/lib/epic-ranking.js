// Pure helpers for the Kanban epic chip row. No DOM.

export function rankEpics(epics, activeCounts) {
  const arr = Array.isArray(epics) ? epics.slice() : [];
  const lr = (e) => e.last_referenced ? Date.parse(e.last_referenced) : 0;
  const nm = (e) => String(e.name || e.id || '').toLowerCase();
  arr.sort((a, b) => {
    const ca = activeCounts.get(a.id) || 0;
    const cb = activeCounts.get(b.id) || 0;
    if (ca !== cb) return cb - ca;
    const la = lr(a), lb = lr(b);
    if (la !== lb) return lb - la;
    return nm(a).localeCompare(nm(b));
  });
  return arr;
}

export function sortEpicsForDropdown(epics, sortKey, activeCounts) {
  const arr = Array.isArray(epics) ? epics.slice() : [];
  const counts = activeCounts || new Map();
  const norm = (s) => String(s || '').toLowerCase();

  if (sortKey === 'count') {
    const nm = (e) => String(e.name || e.id || '').toLowerCase();
    arr.sort((a, b) => {
      const diff = (counts.get(b.id) || 0) - (counts.get(a.id) || 0);
      if (diff !== 0) return diff;
      return nm(a).localeCompare(nm(b));
    });
    return arr;
  }
  if (sortKey === 'status') {
    const rank = { active: 0, planned: 1, future: 1, done: 2, archived: 3 };
    arr.sort((a, b) => {
      const ra = rank[norm(a.status)] ?? 4;
      const rb = rank[norm(b.status)] ?? 4;
      return ra - rb;
    });
    return arr;
  }
  if (sortKey === 'recent') {
    const lr = (e) => e.last_referenced ? Date.parse(e.last_referenced) : 0;
    arr.sort((a, b) => {
      const la = lr(a), lb = lr(b);
      if (la === 0 && lb === 0) return 0;       // preserve input order for missing
      if (la === 0) return 1;
      if (lb === 0) return -1;
      return lb - la;
    });
    return arr;
  }
  // 'alpha'
  const nm = (e) => String(e.name || e.id || '').toLowerCase();
  arr.sort((a, b) => nm(a).localeCompare(nm(b)));
  return arr;
}
