#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/../.."

# Boot the server in the background on a known port.
PORT=8765
python -c "
from backlog_server import _make_server
s, p = _make_server(host='127.0.0.1', port=$PORT)
import threading
threading.Thread(target=s.serve_forever, daemon=False).start()
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

# Tear down.
kill $SERVER_PID 2>/dev/null || true
exit $RESULT
