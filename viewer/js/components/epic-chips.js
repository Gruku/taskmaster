// User intent: every epic on the Kanban Epic row in one line, pinned first, with one open-task count each.
import { rankEpics, sortEpicsForDropdown } from '../lib/epic-ranking.js';

// Chip specs for the Epic chipRow: All, then pinned epics in pin order, then the rest ranked by open count
// (or the Epic options order). Archived epics stay out unless shown, selected or pinned.
export function epicChips({ epics, selectedIds = [], pinnedIds = [], counts = new Map(), sort = 'count', showArchived = false }) {
  const list = (epics || []).filter((e) =>
    showArchived || e.status !== 'archived' || selectedIds.includes(e.id) || pinnedIds.includes(e.id));
  const byId = new Map(list.map((e) => [e.id, e]));
  const pinned = pinnedIds.map((id) => byId.get(id)).filter(Boolean);
  const rest = list.filter((e) => !pinnedIds.includes(e.id));
  const ordered = sort === 'count' ? rankEpics(rest, counts) : sortEpicsForDropdown(rest, sort, counts);
  return [
    { value: '__all__', label: 'All', pressed: selectedIds.length === 0 },
    ...[...pinned, ...ordered].map((e) => ({
      value: e.id,
      label: e.name || e.id,
      pressed: selectedIds.includes(e.id),
      count: counts.get(e.id) ?? 0,
      swatch: e.swatch,
    })),
  ];
}
