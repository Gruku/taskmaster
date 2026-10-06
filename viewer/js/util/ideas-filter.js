// User intent: what the Ideas list shows and in what order — "UX" and "ux" are one tag, the search reaches id, title,
// body, status and tags, archived ideas show only on request, and a status chip exists only for a status in use (or pressed).
import { IDEA_STATUS, statusMeta } from '../components/status.js';
import { tagKey } from '../components/tag-filter.js';

export function ideaMatchesSearch(idea, term) {
  const needle = String(term ?? '').trim().toLowerCase();
  if (!needle) return true;
  const tags = Array.isArray(idea?.tags) ? idea.tags : [];
  return [idea?.id, idea?.title, idea?.body, idea?.status, ...tags]
    .some((v) => v != null && String(v).toLowerCase().includes(needle));
}

export function applyIdeasFilters(ideas, { statuses = [], tags = [], includeArchived = false, search = '' } = {}) {
  const wanted = [...new Set((tags ?? []).map(tagKey).filter(Boolean))];
  const filtered = (ideas ?? []).filter((idea) => {
    if (!includeArchived && idea.archived) return false;
    if (statuses?.length && !statuses.includes(idea.status)) return false;
    if (wanted.length) {
      const have = new Set((Array.isArray(idea.tags) ? idea.tags : []).map(tagKey));
      if (!wanted.every((k) => have.has(k))) return false;
    }
    return ideaMatchesSearch(idea, search);
  });
  // Newest first; ISO-8601 strings sort lexically.
  return filtered.sort((a, b) => {
    const da = a.created || '';
    const db = b.created || '';
    return db < da ? -1 : db > da ? 1 : 0;
  });
}

export function ideaStatusChips(ideas, pressed = []) {
  const on = new Set(pressed ?? []);
  const counts = new Map();
  for (const idea of ideas ?? []) {
    if (idea?.status) counts.set(idea.status, (counts.get(idea.status) ?? 0) + 1);
  }
  for (const v of on) if (v && !counts.has(v)) counts.set(v, 0);
  const known = Object.keys(IDEA_STATUS).filter((v) => counts.has(v));
  const unknown = [...counts.keys()].filter((v) => !Object.hasOwn(IDEA_STATUS, v)).sort((a, b) => a.localeCompare(b));
  return [...known, ...unknown].map((value) => ({
    value, label: statusMeta('idea', value).label, count: counts.get(value), pressed: on.has(value),
  }));
}
