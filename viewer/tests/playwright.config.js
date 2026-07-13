import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: /^.*\.spec\.js$/,
  // These specs are route-mocked and self-serve the static viewer via
  // playwright.threads.config.js; they must NOT run under the live-server
  // bootstrap here (its `/` serves the legacy viewer, not the v3 app).
  testIgnore: /(threads-board|handover-status)\.spec\.js$/,
  timeout: 15_000,
  retries: 0,
  use: {
    baseURL: process.env.VIEWER_BASE_URL || 'http://127.0.0.1:8765',
    headless: true,
  },
});
