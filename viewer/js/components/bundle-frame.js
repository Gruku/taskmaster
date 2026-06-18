// Bundle frame (Variant A) — groups tasks sharing a bundle slug into a framed block.
//
// Usage:
//   const el = renderBundleFrame({ slug, tasks, total }, { density, epicColors, groupBy });
//
// Inputs:
//   slug        — bundle slug string
//   tasks       — task objects in this column with this slug
//   total       — total tasks across ALL columns with this slug (for "N of M here" text)
//   density     — 'minimal' | 'full' (passed to renderCard)
//   epicColors  — {epicId → hex}
//   groupBy     — 'status' | 'phase' | 'epic'

import { renderCard } from './card.js';

// Lane strictness order: full is strictest, express is loosest.
const LANE_RANK = { express: 1, standard: 2, full: 3 };

/**
 * Compute the strictest lane across a set of tasks.
 * Returns null if no task has a lane.
 */
function strictestLane(tasks) {
  let best = null;
  let bestRank = 0;
  for (const t of tasks) {
    const lane = t.lane || null;
    if (!lane) continue;
    const rank = LANE_RANK[lane] || 0;
    if (rank > bestRank) {
      bestRank = rank;
      best = lane;
    }
  }
  return best;
}

/**
 * Stable hue 1..6 from a slug string (sum char codes % 6 + 1).
 * Same slug always returns same hue.
 */
function slugHue(slug) {
  let sum = 0;
  for (let i = 0; i < slug.length; i++) sum += slug.charCodeAt(i);
  return (sum % 6) + 1;
}

/**
 * Renders a bundle frame containing the given tasks.
 *
 * @param {{ slug: string, tasks: object[], total: number }} bundle
 * @param {{ density?: string, epicColors?: object, groupBy?: string }} opts
 * @returns {HTMLElement}
 */
export function renderBundleFrame({ slug, tasks, total }, { density = 'full', epicColors = {}, groupBy = 'status' } = {}) {
  const frame = document.createElement('div');
  frame.className = `bundle-frame bh-${slugHue(slug)}`;

  // ── Header ──
  const head = document.createElement('div');
  head.className = 'bundle-frame-head';

  const hex = document.createElement('span');
  hex.className = 'hex';
  hex.textContent = '⬢';
  head.appendChild(hex);

  const slugEl = document.createElement('span');
  slugEl.className = 'slug';
  slugEl.textContent = slug;
  head.appendChild(slugEl);

  // Lane badge — only if at least one task has a lane
  const lane = strictestLane(tasks);
  if (lane) {
    const laneEl = document.createElement('span');
    laneEl.className = 'lane';
    laneEl.textContent = lane.toUpperCase();
    head.appendChild(laneEl);
  }

  // Count text
  const ct = document.createElement('span');
  ct.className = 'ct';
  const n = tasks.length;
  if (total && tasks.length < total) {
    ct.textContent = `${n} of ${total} here`;
  } else {
    ct.textContent = `${n} task${n === 1 ? '' : 's'}`;
  }
  head.appendChild(ct);

  frame.appendChild(head);

  // ── Cards ──
  for (const task of tasks) {
    frame.appendChild(renderCard({ task, density, epicColors, groupBy, hideBundleChip: true }));
  }

  return frame;
}
