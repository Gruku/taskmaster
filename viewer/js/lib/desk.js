// Pure logic for the Desk dashboard. No DOM.

export const RAIL_CAP = 5;       // max cards per continuity rail
export const MAX_AGE_DAYS = 30;  // older items collapse into the "+N older" link

// Continuity items → four rails. Items beyond cap or age window are counted
// in `older` instead of rendered; ambient items are not desk material.
export function buildRails(items) {
  const rails = {
    resume:  { items: [], older: 0 },
    review:  { items: [], older: 0 },
    decide:  { items: [], older: 0 },
    cleanup: { items: [], older: 0 },
  };
  const KEY = { resume: 'resume', review: 'review', decide: 'decide', 'clean-up': 'cleanup' };
  const sorted = [...(items || [])].sort((a, b) => (a.age_days ?? 0) - (b.age_days ?? 0));
  for (const it of sorted) {
    const key = KEY[it.action_class];
    if (!key) continue;
    const rail = rails[key];
    if ((it.age_days ?? 0) > MAX_AGE_DAYS || rail.items.length >= RAIL_CAP) {
      rail.older += 1;
    } else {
      rail.items.push(it);
    }
  }
  return rails;
}

export function sortNotes(notes) {
  const pinned = (notes || []).filter(n => n.pinned);
  const rest = (notes || []).filter(n => !n.pinned);
  const byCreatedDesc = (a, b) => (b.created || '').localeCompare(a.created || '');
  return [...pinned.sort(byCreatedDesc), ...rest.sort(byCreatedDesc)];
}

// Deterministic paper tilt in degrees, −1.2…+1.2, derived from the id so the
// board is stable across renders. Static placement — never animated.
export function tiltFor(id) {
  let h = 0;
  for (let i = 0; i < (id || '').length; i++) h = ((h << 5) - h + id.charCodeAt(i)) | 0;
  return Math.round(((Math.abs(h) % 241) / 240 * 2.4 - 1.2) * 100) / 100;
}

export function firstLine(text) {
  for (const line of (text || '').split('\n')) {
    const t = line.trim().replace(/^#+\s*/, '');
    if (t) return t;
  }
  return '';
}
