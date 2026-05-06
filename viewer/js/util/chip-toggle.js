// Single-click filter / shift-click multi-select semantics for chip rows.
// Used by every chip-row filter so behavior stays consistent across screens.
//
//   plain click  → filter to ONLY this key (or clear if it was already the
//                  sole active key)
//   shift-click  → toggle this key in/out of the active multi-select pool
//
// `current` is any iterable of currently-active keys (Array or Set).
// Returns a new Array of active keys.

export function chipClickNext(ev, current, key) {
  const set = new Set(current);
  if (ev && ev.shiftKey) {
    if (set.has(key)) set.delete(key);
    else              set.add(key);
  } else {
    if (set.size === 1 && set.has(key)) {
      set.clear();
    } else {
      set.clear();
      set.add(key);
    }
  }
  return [...set];
}

export const CHIP_CLICK_HINT = 'click to filter • shift-click to multi-select';
