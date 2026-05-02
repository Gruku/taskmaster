// Pure-logic time helpers. No DOM. Imported by browser AND node tests.

const MIN_MS  = 60 * 1000;
const HOUR_MS = 60 * MIN_MS;
const DAY_MS  = 24 * HOUR_MS;

/** Parse an ISO8601 timestamp to ms; null/undefined/empty → null. */
export function isoToMs(iso) {
  if (!iso) return null;
  const t = Date.parse(iso);
  return Number.isFinite(t) ? t : null;
}

/** Compact time-in-status: "30m" / "7h" / "2d". Returns '' for null/undefined. */
export function formatTimeInStatus(tsMs, nowMs = Date.now()) {
  if (tsMs == null) return '';
  const delta = Math.max(0, nowMs - tsMs);
  if (delta < HOUR_MS) return Math.floor(delta / MIN_MS) + 'm';
  if (delta < DAY_MS)  return Math.floor(delta / HOUR_MS) + 'h';
  return Math.floor(delta / DAY_MS) + 'd';
}

/** Per spec §3.8: fresh < 4d, stale >= 4d. Future tier "aging" is reserved. */
export function classifyTimeInStatus(tsMs, nowMs = Date.now()) {
  if (tsMs == null) return 'fresh';
  const days = (nowMs - tsMs) / DAY_MS;
  if (days >= 4) return 'stale';
  return 'fresh';
}

/** "MM:SS" or "HH:MM:SS" for elapsed-since strings on auto-mode runs. */
export function formatElapsed(ms) {
  if (ms == null || !Number.isFinite(ms) || ms < 0) ms = 0;
  const total = Math.floor(ms / 1000);
  const h = Math.floor(total / 3600);
  const m = Math.floor((total % 3600) / 60);
  const s = total % 60;
  const pad = n => String(n).padStart(2, '0');
  if (h > 0) return `${pad(h)}:${pad(m)}:${pad(s)}`;
  return `${pad(m)}:${pad(s)}`;
}

function _toMs(input) {
  if (input == null) return null;
  if (input instanceof Date) return input.getTime();
  if (typeof input === 'number') return Number.isFinite(input) ? input : null;
  return isoToMs(input);
}

const MONTH_MS = 30 * DAY_MS;
const YEAR_MS  = 365 * DAY_MS;

/**
 * Canonical relative-time formatter. Accepts iso string, ms number, or Date.
 * Compact units, single granularity ("1m"/"7h"/"3d"/"2mo"/"1y"). Default
 * suffix is " ago"; pass suffix: '' for bare ("2d") or e.g. ' running'.
 *   formatRelative('2026-05-01T...')          → "1d ago"
 *   formatRelative(ms, { suffix: '' })        → "1d"
 *   formatRelative(ms, { suffix: ' running'}) → "1d running"
 *   formatRelative(future)                    → "now"
 */
export function formatRelative(input, opts = {}) {
  const { now = Date.now(), suffix = ' ago' } = opts;
  const ms = _toMs(input);
  if (ms == null) return '';
  const nowMs = typeof now === 'number' ? now : now.getTime();
  const delta = Math.max(0, nowMs - ms);
  if (delta < MIN_MS)   return 'now';
  if (delta < HOUR_MS)  return Math.floor(delta / MIN_MS)   + 'm'  + suffix;
  if (delta < DAY_MS)   return Math.floor(delta / HOUR_MS)  + 'h'  + suffix;
  if (delta < MONTH_MS) return Math.floor(delta / DAY_MS)   + 'd'  + suffix;
  if (delta < YEAR_MS)  return Math.floor(delta / MONTH_MS) + 'mo' + suffix;
  return Math.floor(delta / YEAR_MS) + 'y' + suffix;
}

/**
 * Canonical absolute-time formatter. Accepts iso string, ms number, or Date.
 * Defaults to "May 2, 2026 · 10:30". Toggle parts off via opts.
 * Year auto-suppresses when same as current.
 *   formatAbsolute(iso)                       → "May 2, 2026 · 10:30"
 *   formatAbsolute(iso, { time: false })      → "May 2, 2026"
 *   formatAbsolute(iso, { date: false })      → "10:30"
 *   formatAbsolute(iso, { year: false })      → "May 2 · 10:30"
 *   formatAbsolute(iso, { year: true })       → forces year on
 */
export function formatAbsolute(input, opts = {}) {
  const ms = _toMs(input);
  if (ms == null) return '';
  const { date = true, time = true, year = 'auto', now = Date.now() } = opts;
  const d = new Date(ms);
  const showYear = year === true ? true
                  : year === false ? false
                  : d.getFullYear() !== new Date(typeof now === 'number' ? now : now.getTime()).getFullYear();
  const parts = [];
  if (date) {
    const dateOpts = showYear
      ? { month: 'short', day: 'numeric', year: 'numeric' }
      : { month: 'short', day: 'numeric' };
    parts.push(d.toLocaleDateString(undefined, dateOpts));
  }
  if (time) {
    parts.push(d.toLocaleTimeString(undefined, { hour: '2-digit', minute: '2-digit' }));
  }
  return parts.join(' · ');
}

/**
 * Compact duration "1h35m" / "35m" / "12s" — for live counters where MM:SS
 * is too noisy. Unlike formatElapsed (HH:MM:SS clock), this drops zero units
 * and pairs hours+minutes ergonomically.
 *   formatDurationCompact(75_000)     → "1m"
 *   formatDurationCompact(5_700_000)  → "1h35m"
 *   formatDurationCompact(45_000)     → "45s"
 */
export function formatDurationCompact(ms) {
  if (ms == null || !Number.isFinite(ms) || ms < 0) ms = 0;
  const s = Math.floor(ms / 1000);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h) return `${h}h${m}m`;
  if (m) return `${m}m`;
  return `${s}s`;
}
