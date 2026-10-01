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
  use: { baseURL: `http://127.0.0.1:${PORT}`, headless: true },
  webServer: {
    command: `python -m http.server ${PORT} --bind 127.0.0.1`,
    cwd: viewerDir,
    url: `http://127.0.0.1:${PORT}/index.html`,
    reuseExistingServer: false,
    timeout: 20_000,
  },
});
