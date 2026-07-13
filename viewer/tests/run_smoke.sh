#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

# Locate the repo root that owns the live .taskmaster so the server can reach
# notes, tasks, and other backlog data. Walk up from the plugin dir until we
# find a directory containing .taskmaster/backlog.yaml, falling back to the
# plugin dir itself if none is found.
# NOTE: this intentionally roots the server for the WHOLE suite at the repo's
# live .taskmaster (previously cwd = viewer dir, i.e. no backlog at all) — all
# specs now see real data, so specs must create/clean their own entities.
REPO_ROOT="$(pwd)"
_search="$(pwd)"
for _ in 1 2 3 4; do
  _parent="$(dirname "$_search")"
  if [ -f "$_parent/.taskmaster/backlog.yaml" ]; then
    REPO_ROOT="$_parent"
    break
  fi
  _search="$_parent"
done
export TASKMASTER_ROOT="$REPO_ROOT"

# Boot the server in the background on a known port.
PORT=8765
python -c "
from backlog_server import _make_server
s, p = _make_server(host='127.0.0.1', port=$PORT)
import threading, time
threading.Thread(target=s.serve_forever, daemon=True).start()
# Keep main thread alive so the daemon thread (server) runs until this
# process is killed by the test harness (kill \$SERVER_PID).
while True: time.sleep(3600)
" &
SERVER_PID=$!

# Wait for it to be up.
for i in {1..40}; do
  if curl -fsS "http://127.0.0.1:$PORT/api/identity" >/dev/null 2>&1; then break; fi
  sleep 0.1
done

# Run Playwright.
cd viewer/tests
VIEWER_BASE_URL="http://127.0.0.1:$PORT" npx playwright test
RESULT=$?
VIEWER_BASE_URL="http://127.0.0.1:$PORT" npx playwright test --config playwright.threads.config.js
RESULT=$((RESULT || $?))

# Tear down.
kill $SERVER_PID 2>/dev/null || true
exit $RESULT
