import { defineConfig } from 'vite';

export default defineConfig({
  base: '/A-Setup-Finder-Company/',
  build: { target: 'es2022' },
  // Keep harper.js out of pre-bundling so `new URL('harper_wasm_bg.wasm', import.meta.url)` resolves in dev.
  optimizeDeps: { exclude: ['harper.js'] },
  test: {
    include: ['tests/unit/**/*.test.js'],
    environment: 'node',
    testTimeout: 30000,
  },
});
