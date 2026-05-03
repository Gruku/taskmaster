// Shared "is auto-mode actively running?" predicate.
// Used by auto-mode-strip, auto-status pill, sidebar live-dot, and per-card live-block.
//
// Without this, each call site invents its own check and they drift —
// notably leaving stale "running" UI on screen long after a session ends.

export function isAutoRunning(autoState) {
  if (!autoState || !autoState.mode) return false;
  if (autoState.stopped) return false;
  if (autoState.completed_at) return false;
  // pending = remaining stages for current task. Empty means there's nothing
  // left to do — either the run completed (and completed_at should be set) or
  // the record is stale.
  if (!Array.isArray(autoState.pending) || autoState.pending.length === 0) return false;
  return true;
}
