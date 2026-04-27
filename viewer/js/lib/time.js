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
