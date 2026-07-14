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
- UI smoke: `bash viewer/tests/run_smoke.sh`
