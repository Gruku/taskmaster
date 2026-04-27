import { defineConfig } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  testMatch: /^.*\.spec\.js$/,
  timeout: 15_000,
  retries: 0,
  use: {
    baseURL: process.env.VIEWER_BASE_URL || 'http://127.0.0.1:8765',
    headless: true,
  },
});
