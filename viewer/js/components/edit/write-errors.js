// User intent: a write the server refused is explained in words a person reads — the server's own reason when it gives
// one, a plain sentence when it does not — and never as a method, a URL, a status code or a JSON body.
// Imports nothing, so every write path (inline field, form, conflict banner) can use it without loading the others.

// A 409 that names the revision the write lost to is a race to settle field by field. Any other 409 is the server
// refusing the write (gates still open, a legacy layout): its reason is the answer, and there is nothing to merge.
export const lostRace = (e) => e?.code === 409 && !!e.current_etag;

const sentenceCase = (key) => {
  const words = String(key).replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
};

// The error thrown by api.js's http(): `code` is the HTTP status (none when the server was never reached), `reason`
// the `error` string of a JSON body, `errors` a 422's field map, `unreadable` an answer whose JSON could not be read;
// a 409's message is already its reason ('stale' when it gave none). The raw message stays for the console.
export function describeWriteError(e, { noun = 'task' } = {}) {
  // The server answered, so it was reached, but nothing in the answer says whether the write took.
  if (e?.unreadable) return 'The server answered in a form the viewer cannot read, so it cannot tell whether the change was saved. Reload to check.';
  const code = typeof e?.code === 'number' ? e.code : null;
  if (code === 422) {
    const fields = Object.entries(e.errors || {}).filter(([, why]) => typeof why === 'string' && why);
    if (fields.length) return fields.map(([key, why]) => `${sentenceCase(key)}: ${why}`).join(' · ');
  }
  if (code === 409) {
    const reason = typeof e.message === 'string' ? e.message.trim() : '';
    if (reason && reason !== 'stale') return reason;
    return `This ${noun} changed since it was opened. Open it again to see the latest, then make the change again.`;
  }
  if (code === 404) return `This ${noun} no longer exists — it may have been archived or removed.`;
  console.warn('write refused:', e);
  if (code == null) return 'Could not reach the server, so nothing was saved. Check that the viewer is still running.';
  if (code >= 500) return 'The server could not save this change. Try again in a moment.';
  if (typeof e.reason === 'string' && e.reason.trim()) return e.reason.trim();
  return 'The server refused this change.';
}
