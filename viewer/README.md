# Taskmaster Viewer

The taskmaster viewer UI. Served at `/` (and aliased at `/v3`) by the embedded HTTP server; assets are rewritten under `/static/v3/`.

## Layout

- `index.html` — shell
- `css/tokens.css` — single source of truth for design tokens. Other CSS uses `var(--*)` only.
- `css/shell.css` — shell, sidebar, topbar
- `css/components.css` — shared chips/pills/buttons
- `css/screens/*.css` — per-screen styles (added in Plans 2–6)
- `js/main.js` — entry; boots store, sidebar, router, polling
- `js/router.js` — hash routing
- `js/store.js` — in-memory state + subscriptions
- `js/api.js` — HTTP client for `/api/*`
- `js/components/*.js` — shared UI helpers
- `js/screens/*.js` — one module per screen, exports `mount(root, deps)` and `meta`

## Run

```bash
# From the repo root, on any free port:
python -c "from taskmaster.backlog_server import _make_server; s, p = _make_server(host='127.0.0.1', port=0); print(f'http://127.0.0.1:{p}/v3'); s.serve_forever()"
```

Root (`/`) serves this viewer shell — no pref flip needed. `/v3` is a kept alias for open tabs and tests.

## Test

- Server: `python -m pytest tests/`
- Unit: `npm --prefix viewer run test:unit`. Mocked UI (no server, no backlog): `npm --prefix viewer run test:mock`.
- Live UI smoke: `TM_LIVE_SPECS_OK=1 bash viewer/tests/run_smoke.sh`. These specs write viewer prefs and backlog
  entities to the server they reach (port 8765), so both `run_smoke.sh` and `tests/playwright.config.js`
  (`npm run test:e2e`) refuse to start unless `TM_LIVE_SPECS_OK=1` is set.
- `run_smoke.sh` also refuses a root that holds a `.taskmaster` backlog unless `TM_LIVE_SPECS_ROOT_OK` equals
  that root exactly (the refusal prints the path), and refuses when a viewer already answers on port 8765.
