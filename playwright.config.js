import { defineConfig } from '@playwright/test';

const PORT = 4173;
export const BASE = `http://localhost:${PORT}/A-Setup-Finder-Company/`;

export default defineConfig({
  testDir: 'tests/e2e',
  timeout: 120000,
  workers: 1,
  webServer: {
    command: `npx vite preview --port ${PORT} --strictPort`,
    url: BASE,
    reuseExistingServer: false,
    timeout: 120000,
  },
  use: {
    baseURL: BASE,
    browserName: 'chromium',
    viewport: { width: 1400, height: 1000 },
    launchOptions: { args: ['--use-fake-ui-for-media-stream', '--auto-select-desktop-capture-source=Entire screen'] },
  },
});
