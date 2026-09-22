import { defineConfig } from '@playwright/test';
import path from 'node:path';
import {fileURLToPath} from 'node:url';

const repo = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const python = process.env.N10_PYTHON || path.resolve(repo, '../../.venv/Scripts/python.exe');
export default defineConfig({
  testDir: './n10', timeout: 30000, workers: 1, retries: 0,
  outputDir: '../../test-results/n10-browser',
  use: {headless: true, trace: 'retain-on-failure'},
  projects: [
    {name: 'legacy', use: {browserName: 'chromium', baseURL: 'http://127.0.0.1:8871'}},
    {name: 'native', use: {browserName: 'chromium', baseURL: 'http://127.0.0.1:8872'}},
    {name: 'legacy-firefox', testMatch: /conditional.spec.js/, use: {browserName: 'firefox', baseURL: 'http://127.0.0.1:8871'}},
    {name: 'native-firefox', testMatch: /conditional.spec.js/, use: {browserName: 'firefox', baseURL: 'http://127.0.0.1:8872'}},
  ],
  webServer: ['legacy', 'native'].map((store, i) => ({
    command: `"${python}" "${repo}/scripts/viewer_fixture_server.py" --store ${store} --port ${8871 + i}`,
    url: `http://127.0.0.1:${8871 + i}/api/identity`, reuseExistingServer: false, timeout: 60000,
  })),
});
