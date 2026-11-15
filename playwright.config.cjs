const path = require('node:path');
const { defineConfig } = require('@playwright/test');

process.env.PLAYWRIGHT_BROWSERS_PATH ||= path.join(__dirname, '.playwright');
module.exports = defineConfig({
  testDir: './tests/browser',
  webServer: process.env.CDV_BROWSER_BASE_URL ? undefined : {
    command: 'uv run codiscover-video serve --port 8876',
    url: 'http://127.0.0.1:8876/api/status',
    reuseExistingServer: false,
    timeout: 30000,
    env: { CDV_PROVIDER: 'demo', CDV_LLM: 'demo', CDV_DATA_DIR: 'data/browser-test-db', CDV_SCHEDULER: 'false' },
  },
  testMatch: '**/*.spec.cjs',
  fullyParallel: false,
  workers: 1,
  timeout: 90000,
  expect: { timeout: 15000 },
  outputDir: './data/browser-test-results',
  reporter: [['list'], ['json', { outputFile: './data/browser-test-results/report.json' }]],
  use: {
    baseURL: process.env.CDV_BROWSER_BASE_URL || 'http://127.0.0.1:8876',
    browserName: 'chromium',
    channel: 'chromium',
    // macOS headless-shell may suspend rAF; full Chromium keeps rendering active.
    launchOptions: { args: ['--disable-frame-rate-limit', '--disable-gpu'] },
    viewport: { width: 1440, height: 1000 },
    headless: true,
    trace: 'retain-on-failure',
    screenshot: 'only-on-failure',
    actionTimeout: 15000,
  },
});
