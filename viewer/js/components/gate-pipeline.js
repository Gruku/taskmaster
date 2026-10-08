// gate-pipeline.js — Spec A surfacing: gate pipeline tracker + lane badge.
//
// Exports:
//   renderGatePipeline(task)  — HTML string; empty string when no lane.
//   laneBadge(task)           — HTML string chip showing task.lane; '' when no lane.
//   gateStateWords(raw)       — the server's gate_state mirror in words, or null when it is not one.
//
// VISUAL RULES (hard constraints from CLAUDE.md / design system):
//   - NO colored left rails / border-left accents. Use tinted fill + full-perimeter border.
//   - NO hover motion (transform / translate / scale).
//   - NO box-shadows for elevation — surface stepping only.
//   - A gate's state is a shape plus a word (the marker language of status.css); the shape carries the hue:
//       done / pass → ● success      warn → ▲ warning      fail → ◆ critical
//       skipped     → ✕ neutral      pending → ○ neutral
//   - Gates and states are said in words ("Plan review", "passed with warnings"); a machine string such as
//     `task.gate_state` is never printed as it is.
//
// Source of truth: taskmaster_v3.py blocking_gates(). Review gates only — these gate completion.
// Status gates (spec/plan/tests/impl) are non-blocking plumbing and are not shown in the tracker.

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

// Source of truth: taskmaster_v3.py blocking_gates(). Review gates only — these gate completion.
// Status gates (spec/plan/tests/impl) are non-blocking plumbing and are not shown in the tracker.
const BLOCKING_GATES = {
  full:     ['spec-review', 'plan-review', 'review-gate'],
  standard: ['design-review', 'review-gate'],
  express:  ['review-gate'],
};

// state → [marker tone, drawn shape, fallback glyph]
const STATE_MARK = {
  done:    ['success', 'dot', '●'],
  pass:    ['success', 'dot', '●'],
  warn:    ['warning', 'triangle', '▲'],
  fail:    ['critical', 'diamond', '◆'],
  skipped: ['neutral', 'cross', '✕'],
  pending: ['neutral', 'ring', '○'],
};

// state → the word a person reads beside the shape
const STATE_WORD = {
  done: 'done',
  pass: 'passed',
  warn: 'passed with warnings',
  fail: 'failed',
  skipped: 'skipped',
  pending: 'pending',
};

const GATE_LABEL = {
  'spec-review': 'Spec review',
  'plan-review': 'Plan review',
  'design-review': 'Design review',
  'review-gate': 'Review gate',
};

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

// A gate the viewer has no label for is sentence-cased from its id ('security-audit' → 'Security audit').
function gateLabel(name) {
  if (Object.hasOwn(GATE_LABEL, name)) return GATE_LABEL[name];
  const words = String(name).replace(/[-_]+/g, ' ').trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
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

// The server's gate_state mirror: 'blocked@<gate>' (a gate failed) or '<gate>:<state>' with a known state.
// Anything else (an unknown state, another shape) is null.
function parseGateState(raw) {
  if (typeof raw !== 'string') return null;
  const blocked = /^blocked@([^:@]+)$/.exec(raw);
  if (blocked) return { blocked: true, gate: blocked[1] };
  const current = /^([^:@]+):([^:]+)$/.exec(raw);
  if (current && Object.hasOwn(STATE_WORD, current[2])) return { blocked: false, gate: current[1], state: current[2] };
  return null;
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * The server's gate_state mirror in words: 'blocked@review-gate' → 'Blocked at Review gate',
 * 'plan-review:warn' → 'Plan review — passed with warnings'. Plain text (not escaped); null when malformed.
 *
 * @param {unknown} raw
 * @returns {string|null}
 */
export function gateStateWords(raw) {
  const parsed = parseGateState(raw);
  if (!parsed) return null;
  return parsed.blocked
    ? `Blocked at ${gateLabel(parsed.gate)}`
    : `${gateLabel(parsed.gate)} — ${STATE_WORD[parsed.state]}`;
}

/**
 * Render the gate pipeline tracker for a task.
 * Returns an HTML string, or '' if the task has no lane.
 *
 * @param {object|null} task
 * @returns {string}
 */
export function renderGatePipeline(task) {
  if (!task || !task.lane) return '';
  const gates = BLOCKING_GATES[task.lane];
  if (!gates) return '';

  const records = task.gates || {};

  // Build one node per required gate.
  const nodes = gates.map((gateName) => {
    const stateClass = gateStateClass(records[gateName]);
    const [tone, shape, glyph] = STATE_MARK[stateClass];
    const label = escapeHtml(gateLabel(gateName));
    return `<span class="gp-gate gate--${stateClass} marker marker--${tone}" title="${label}: ${STATE_WORD[stateClass]}">`
      + `<span class="marker__shape" data-shape="${shape}" aria-hidden="true">${glyph}</span>`
      + `<span class="marker__word">${label}</span> `
      + `<span class="gp-gate__state">${STATE_WORD[stateClass]}</span></span>`;
  }).join('');

  // The server's gate_state mirror, in words. 'blocked@<gate>' (a gate failed) always says where the task is stuck;
  // '<gate>:<state>' only when its gate is off this lane's track (a node on the track already says it). Anything else
  // (an unknown state, another shape) is not printed.
  const parsed = parseGateState(task.gate_state);
  let stateEl = '';
  if (parsed && (parsed.blocked || !gates.includes(parsed.gate))) {
    stateEl = `<span class="gp-state">Current step: ${escapeHtml(gateStateWords(task.gate_state))}</span>`;
  }

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
