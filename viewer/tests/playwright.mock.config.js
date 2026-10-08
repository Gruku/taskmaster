// User intent: run UI specs against the static viewer with every API call mocked — no live server, no shared port.
import { defineConfig } from '@playwright/test';
import { fileURLToPath } from 'url';
import { dirname, resolve } from 'path';

const viewerDir = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const PORT = Number(process.env.MOCK_PORT || 8792);

export default defineConfig({
  testDir: '.',
  testMatch: /\.mock\.spec\.js$/,
  timeout: 15_000,
  retries: 0,
  // Bounded: every worker cold-starts a browser at once, and on a busy machine that alone ate most of a test's budget.
  workers: 4,
  use: { baseURL: `http://127.0.0.1:${PORT}`, headless: true },
  // Not `python -m http.server`: it accepts with a backlog of 5 and no keep-alive, so a burst of parallel page loads
  // (60 module requests each) had connections refused and pages that never booted.
  webServer: {
    command: `node tests/tools/static-server.mjs ${PORT}`,
    cwd: viewerDir,
    url: `http://127.0.0.1:${PORT}/index.html`,
    reuseExistingServer: false,
    timeout: 20_000,
  },
});
