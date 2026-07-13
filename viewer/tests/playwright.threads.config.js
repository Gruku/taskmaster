import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';

// Self-contained route-mocked config for the thread specs.
//
// The shared playwright.config.js (run_smoke.sh) boots the live python
// backlog_server on the hardcoded port 8765 and roots the whole suite at the
// repo's real .taskmaster — the ISS-025 landmine (leaked servers, live-data
// mutation; only route-mocked specs are trustworthy). This config avoids all of
// that: it serves the STATIC viewer via `python -m http.server` on its own
// managed port and every spec mocks `**/api/**`, so no test depends on live
// backlog data or on port 8765. Playwright owns the server lifecycle (spawn +
// teardown), so nothing leaks.
const __dirname = dirname(fileURLToPath(import.meta.url));
const viewerDir = resolve(__dirname, '..');
const PORT = Number(process.env.THREADS_PORT || 8791);

export default defineConfig({
  testDir: '.',
  testMatch: /(threads-board|handover-status)\.spec\.js$/,
  timeout: 15_000,
  retries: 0,
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    headless: true,
  },
  webServer: {
    command: `python -m http.server ${PORT} --bind 127.0.0.1`,
    cwd: viewerDir,
    url: `http://127.0.0.1:${PORT}/index.html`,
    reuseExistingServer: false,
    timeout: 20_000,
  },
});
