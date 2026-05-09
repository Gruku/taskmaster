// Split a phase array into past / active / future / archived buckets.
// Status values: 'done' | 'active' | 'planned' | 'future' | 'archived' (case-insensitive).
// `archived` is filtered out FIRST so it never appears in past/future regions.
// Then the first 'active' phase wins; everything before it (still done) is past,
// everything after is future. With no active phase, done → past, planned/future → future.

/**
 * Bucket phases by status.
 * @param {Array<{id: string, status?: string}>} phases
 * @returns {{ past: Array, active: Object|null, future: Array, archived: Array }}
 */
export function bucketPhases(phases) {
  const list = Array.isArray(phases) ? phases : [];
  const norm = (s) => String(s || '').toLowerCase();

  const archived = list.filter(p => norm(p.status) === 'archived');
  const nonArchived = list.filter(p => norm(p.status) !== 'archived');

  const activeIdx = nonArchived.findIndex(p => norm(p.status) === 'active');
  if (activeIdx >= 0) {
    return {
      past:    nonArchived.slice(0, activeIdx),
      active:  nonArchived[activeIdx],
      future:  nonArchived.slice(activeIdx + 1),
      archived,
    };
  }
  return {
    past:    nonArchived.filter(p => norm(p.status) === 'done'),
    active:  null,
    future:  nonArchived.filter(p => {
      const s = norm(p.status);
      return s === 'future' || s === 'planned';
    }),
    archived,
  };
}
