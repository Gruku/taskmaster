// gate-pipeline.js — Spec A surfacing: gate pipeline tracker + lane badge.
//
// Exports:
//   renderGatePipeline(task)  — HTML string; empty string when no lane.
//   laneBadge(task)           — HTML string chip showing task.lane; '' when no lane.
//
// VISUAL RULES (hard constraints from CLAUDE.md / design system):
//   - NO colored left rails / border-left accents. Use tinted fill + full-perimeter border.
//   - NO hover motion (transform / translate / scale).
//   - NO box-shadows for elevation — surface stepping only.
//   - Gate state uses tinted backgrounds matching the existing color tokens:
//       done     → green tint   (--green  / rgba(95,174,110,...))
//       pass     → green tint
//       warn     → amber tint   (--amber  / rgba(214,164,95,...))
//       fail     → red tint     (--red    / rgba(214,107,95,...))
//       skipped  → neutral tint (--ink-3  / rgba(124,130,144,...))
//       pending  → transparent  (--border / dim outline only)
//
// Source of truth for LANE_GATES: plugins/taskmaster/taskmaster_v3.py LANE_GATES.
// Keep this const in sync with the server when lanes change.

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/** Mirror of LANE_GATES in taskmaster_v3.py. Keep in sync. */
const LANE_GATES = {
  full:     ['spec', 'spec-review', 'plan', 'plan-review', 'tests', 'impl', 'review-gate'],
  standard: ['spec', 'design-review', 'tests', 'impl', 'review-gate'],
  express:  ['impl', 'review-gate'],
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

/**
 * Derive the display state for a single gate record.
 * Mirrors the server-side logic: skipped > verdict > status=done > pending.
 *
 * @param {object|undefined} record  gate entry from task.gates[gateName]
 * @returns {'done'|'pass'|'warn'|'fail'|'skipped'|'pending'}
 */
function gateStateClass(record) {
  if (!record) return 'pending';
  if (record.skipped) return 'skipped';
  const v = record.verdict;
  if (v === 'pass' || v === 'warn' || v === 'fail') return v;
  if (record.status === 'done') return 'done';
  return 'pending';
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Render the gate pipeline tracker for a task.
 * Returns an HTML string, or '' if the task has no lane.
 *
 * @param {object|null} task
 * @returns {string}
 */
export function renderGatePipeline(task) {
  if (!task || !task.lane) return '';
  const gates = LANE_GATES[task.lane];
  if (!gates) return '';

  const records = task.gates || {};

  // Build one node per required gate.
  const nodes = gates.map((gateName) => {
    const stateClass = gateStateClass(records[gateName]);
    return `<span class="gp-gate gate--${stateClass}" title="${escapeHtml(gateName)}">${escapeHtml(gateName)}</span>`;
  }).join('');

  // Optional gate_state one-liner (current machine state from server).
  const stateEl = task.gate_state
    ? `<span class="gp-state">${escapeHtml(task.gate_state)}</span>`
    : '';

  return `<div class="gp-track">${nodes}${stateEl}</div>`;
}

/**
 * Render a small lane chip.
 * Returns an HTML string, or '' if the task has no lane.
 *
 * @param {object|null} task
 * @returns {string}
 */
export function laneBadge(task) {
  if (!task || !task.lane) return '';
  return `<span class="lane-badge lane--${escapeHtml(task.lane)}">${escapeHtml(task.lane)}</span>`;
}
