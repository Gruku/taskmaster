const RAIL_ORDER = ['decide', 'resume', 'review', 'clean-up'];

export function groupByAction(items) {
  const out = Object.fromEntries(RAIL_ORDER.map(k => [k, []]));
  for (const it of items) {
    if (out[it.action_class]) out[it.action_class].push(it);
  }
  for (const k of RAIL_ORDER) {
    out[k].sort((a, b) => (b.age_days ?? 0) - (a.age_days ?? 0));
  }
  return out;
}

export function pickHero(items) {
  const decisions = items
    .filter(i => i.type === 'decision' && i.action_class === 'decide')
    .sort((a, b) => (b.age_days ?? 0) - (a.age_days ?? 0));
  if (decisions.length) return decisions[0];
  const resumes = items
    .filter(i => i.type === 'handover' && i.action_class === 'resume')
    .sort((a, b) => (a.age_days ?? 0) - (b.age_days ?? 0));
  return resumes[0] || null;
}

export function groupByTime(items, { now = Date.now() } = {}) {
  const buckets = { today: [], yesterday: [], earlier: [], drifting: [] };
  for (const it of items) {
    const age = (it.age_days ?? 0);
    if (age < 1)        buckets.today.push(it);
    else if (age < 2)   buckets.yesterday.push(it);
    else if (age < 7)   buckets.earlier.push(it);
    else                buckets.drifting.push(it);
  }
  return buckets;
}

export function groupByEntity(items) {
  const TYPES = ['decision', 'handover', 'task', 'branch', 'idea', 'issue'];
  const out = Object.fromEntries(TYPES.map(t => [t, []]));
  for (const it of items) {
    if (out[it.type]) out[it.type].push(it);
  }
  return out;
}
