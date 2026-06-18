# Dormant viewer components (archived from auto mode)

These are presentational components from the removed auto-mode screen, kept for a
possible future goals dashboard. **Nothing imports them today.** They reference
removed modules and MUST be rewired before reuse:

- `auto-mode.js` — imports six deleted `api.js` auto functions (`autoListSessions`,
  `autoEvents`, `autoSession`, `autoBudget`, `autoPause`, `autoStop`) and two removed
  store keys (`getAutoState`, `setActiveAutoSession`). Its `../components/sessions-strip.js`
  import is **deleted outright** (not moved — there is no file to repoint to; rebuild or drop
  that usage). Its imports of the components below were merely moved here and only need
  relative-path depth fix-ups.
- `quest-spine.js` → `auto-spine-layout.js` (co-located here; import resolves).
- `auto-side-panels.js` → `../components/budget-meter.js` (path now broken).
- `auto-mode-live-block.js` → `../lib/time.js` (path now broken).
- `flight-log.js`, `auto-spine-layout.js` — pure render/data, no external imports.

Build/test globs do not pick up `_dormant/` (unit glob is `tests/unit/*.test.js`;
Playwright `testDir` is `tests/`). Do not add imports from live code into this dir.
