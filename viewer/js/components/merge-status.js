// merge-status.js — Spec B surfacing: merge-rung ladder dots.
//
// Exports:
//   renderMergeLadder(task, mergeTargets?)  — HTML string; '' when no merge_status.
//   renderMergeLadderCompact(task, mergeTargets?) — condensed dot-only strip for cards.
//
// VISUAL RULES (hard constraints from CLAUDE.md / design system):
//   - NO colored left rails / border-left accents. Tinted fill + full-perimeter border only.
//   - NO hover motion (transform / translate / scale).
//   - NO box-shadows for elevation — surface stepping only.
//   - Rung states use tinted fills:
//       filled  → green tint  (--green  / rgba(95,174,110,...))
//       empty   → transparent ghost outline (--border / dim outline only)
//
// Source of truth for default ladder: project.py:DEFAULT_MERGE_TARGETS
// ("develop", "stage", "master"). Callers may pass a custom mergeTargets array
// (array of {label:string} objects) to override.

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// Default ladder — mirrors project.py:DEFAULT_MERGE_TARGETS.
const DEFAULT_MERGE_TARGETS = [
  { label: 'develop' },
  { label: 'stage' },
  { label: 'master' },
];

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/**
 * Build the tooltip string for a filled rung record.
 * @param {object} record  merge_status[label] entry
 * @returns {string}
 */
function rungTooltip(record) {
  const parts = [];
  if (record.merged_at) parts.push(record.merged_at);
  if (record.merge_commit) parts.push(record.merge_commit.slice(0, 12));
  return parts.join(' · ');
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Render the full merge-ladder section for task detail.
 * Returns an HTML string, or '' if task has no merge_status.
 *
 * @param {object|null} task
 * @param {Array<{label:string}>} [mergeTargets]  defaults to DEFAULT_MERGE_TARGETS
 * @returns {string}
 */
export function renderMergeLadder(task, mergeTargets = DEFAULT_MERGE_TARGETS) {
  if (!task || !task.merge_status) return '';

  const statusMap = task.merge_status || {};
  const rungs = mergeTargets.map(({ label }) => {
    const record = statusMap[label];
    // Any present record counts as a reached rung — including a sparse/empty {}
    // (the merge happened even if details are missing or use a future schema).
    const filled = !!(record && (record.merge_commit || record.merged_at || Object.keys(record).length > 0));
    const stateClass = filled ? 'rung--filled' : 'rung--empty';
    const tooltip = filled ? escapeHtml(rungTooltip(record)) : escapeHtml(label);
    return `<span class="ml-rung ${stateClass}" title="${tooltip}">${escapeHtml(label)}</span>`;
  });

  return `<div class="ml-track">${rungs.join('')}</div>`;
}

/**
 * Render a compact dot-strip for kanban cards.
 *
 * Cards are served from the SLIM backlog.yaml, where the heavy `merge_status`
 * field is STRIPPED (it lives in tasks/<id>.md). So the compact variant derives
 * from the slim mirror `task.merge_gate_state` instead — a single string holding
 * the highest reached ladder rung label (e.g. "stage"), or "" when nothing in
 * the ladder has been merged. (`compute_merge_gate_state` only ever returns a
 * ladder rung label or ""; a `branch:<name>` target leaves it "".)
 *
 * A rung is filled when its index <= the index of `merge_gate_state` in the
 * ladder. When `merge_gate_state` is empty/falsy or not a ladder rung, returns
 * '' — the card is a glance surface, so unmerged tasks render NOTHING rather
 * than an all-empty strip.
 *
 * @param {object|null} task
 * @param {Array<{label:string}>} [mergeTargets]  defaults to DEFAULT_MERGE_TARGETS
 * @returns {string}
 */
export function renderMergeLadderCompact(task, mergeTargets = DEFAULT_MERGE_TARGETS) {
  if (!task) return '';
  const reached = task.merge_gate_state;
  if (!reached) return '';

  const reachedIdx = mergeTargets.findIndex(({ label }) => label === reached);
  if (reachedIdx < 0) return ''; // not a ladder rung — render nothing

  const dots = mergeTargets.map(({ label }, idx) => {
    const filled = idx <= reachedIdx;
    const stateClass = filled ? 'rung--filled' : 'rung--empty';
    return `<span class="ml-dot ${stateClass}" title="${escapeHtml(label)}"></span>`;
  });

  return `<span class="ml-compact">${dots.join('')}</span>`;
}
