# Taskmaster Viewer (v3)

Modular rebuild of the legacy `backlog-viewer.html`. Active development under Plans 1–6 of the redesign.

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
# From the plugin dir, on any free port:
python -c "from backlog_server import _make_server; s, p = _make_server(host='127.0.0.1', port=0); print(f'http://127.0.0.1:{p}/v3'); s.serve_forever()"
```

The legacy viewer still serves at `/`. Set `viewer.use_v3 = true` in `.taskmaster/viewer.json` (or via `PUT /api/viewer/prefs`) to flip the root URL.

## Test

- Server: `python -m pytest plugins/taskmaster/tests/`
- UI smoke: `bash plugins/taskmaster/viewer/tests/run_smoke.sh`
