import { defineConfig, devices } from '@playwright/test';

const port = 8012;
const baseURL = `http://127.0.0.1:${port}`;
const databasePath = '/tmp/vocafsrs-release-test.db';

export default defineConfig({
  testDir: './tests',
  testMatch: 'release_topology.spec.ts',
  fullyParallel: false,
  forbidOnly: true,
  retries: 0,
  workers: 1,
  reporter: 'list',
  use: {
    baseURL,
    trace: 'off',
    screenshot: 'off',
  },
  projects: [
    {
      name: 'chromium',
      use: { ...devices['Desktop Chrome'] },
    },
  ],
  webServer: {
    command: [
      'npm run build',
      `rm -f ${databasePath}`,
      `cd ../backend && UV_CACHE_DIR=/tmp/vocab-coach-uv-cache VOCAB_ENV=test DATABASE_PATH=${databasePath} PYTHONPATH=. uv run alembic upgrade head`,
      `cd ../backend && UV_CACHE_DIR=/tmp/vocab-coach-uv-cache VOCAB_ENV=test DATABASE_PATH=${databasePath} ALLOWED_ORIGINS=${baseURL} PYTHONPATH=. uv run uvicorn main:app --host 127.0.0.1 --port ${port}`,
    ].join(' && '),
    url: `${baseURL}/api/v1/health`,
    reuseExistingServer: false,
    timeout: 90_000,
  },
});
