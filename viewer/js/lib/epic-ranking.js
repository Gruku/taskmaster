// Pure helpers for the Kanban epic chip row. No DOM.

export const ACTIVE_TASK_STATUSES = new Set(['todo', 'in_progress', 'in_review']);

const normStatus = (s) => String(s || '').toLowerCase().replace(/-/g, '_');

export function countActiveTasksByEpic(tasks) {
  const counts = new Map();
  for (const t of (tasks || [])) {
    if (!t.epic) continue;
    if (!ACTIVE_TASK_STATUSES.has(normStatus(t.status))) continue;
    counts.set(t.epic, (counts.get(t.epic) || 0) + 1);
  }
  return counts;
}

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

export function splitQuickAndDropdown(rankedEpics, pinnedIds, capacity) {
  const cap = Math.max(0, capacity | 0);
  const all = Array.isArray(rankedEpics) ? rankedEpics : [];
  const byId = new Map(all.map(e => [e.id, e]));
  const pinSet = new Set();
  const quick = [];

  // 1. Pinned first (in pin order), skipping ghosts.
  for (const id of (Array.isArray(pinnedIds) ? pinnedIds : [])) {
    if (quick.length >= cap) break;
    const e = byId.get(id);
    if (!e || pinSet.has(id)) continue;
    quick.push(e);
    pinSet.add(id);
  }

  // 2. Fill remaining slots with top-ranked non-pinned.
  for (const e of all) {
    if (quick.length >= cap) break;
    if (pinSet.has(e.id)) continue;
    quick.push(e);
  }

  // 3. Everything else goes to dropdown (preserving ranked order).
  const quickIds = new Set(quick.map(e => e.id));
  const dropdown = all.filter(e => !quickIds.has(e.id));

  return { quick, dropdown };
}

export function sortEpicsForDropdown(epics, sortKey, activeCounts) {
  const arr = Array.isArray(epics) ? epics.slice() : [];
  const counts = activeCounts || new Map();
  const norm = (s) => String(s || '').toLowerCase();

  if (sortKey === 'count') {
    arr.sort((a, b) => (counts.get(b.id) || 0) - (counts.get(a.id) || 0));
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
