// Mirror of taskmaster_v3.compute_lesson_shelf for client-side fallback +
// any in-page recompute (e.g. after a Reinforce click before re-poll).

export function computeShelfPlacement(lesson, thresholds, now = new Date()) {
  const events = (lesson.reinforce_events || []).map(e => new Date(e.at));
  const day = 86_400_000;
  const coreCount   = Number(thresholds.core_count ?? 7);
  const coreWindow  = Number(thresholds.core_window_days ?? 60) * day;
  const coreRecency = Number(thresholds.core_recency_days ?? 14) * day;
  const retiredAfter = Number(thresholds.retired_after_days ?? 30) * day;

  const inWindow  = events.filter(t => (now - t) <= coreWindow);
  const inRecency = events.filter(t => (now - t) <= coreRecency);
  const inActive  = events.filter(t => (now - t) <= retiredAfter);

  if (inWindow.length >= coreCount && inRecency.length >= 1) return 'core';
  if (inActive.length === 0) return 'retired';
  return 'active';
}

export default computeShelfPlacement;
