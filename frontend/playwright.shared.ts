import { defineConfig, devices } from '@playwright/test';

export function createPlaywrightConfig(apiPort: number, frontendPort: number) {
  const frontendUrl = `http://localhost:${frontendPort}`;
  const databaseUrl = 'sqlite+aiosqlite:///data/vocab_test.db';
  process.env.VITE_API_PORT = String(apiPort);

  return defineConfig({
    testDir: './tests',
    fullyParallel: false,
    forbidOnly: true,
    retries: 0,
    workers: 1,
    reporter: 'list',
    use: {
      baseURL: frontendUrl,
      trace: 'off',
      screenshot: 'off',
    },
    projects: [
      {
        name: 'chromium',
        use: { ...devices['Desktop Chrome'] },
      },
    ],
    webServer: [
      {
        command: `cd ../backend && UV_CACHE_DIR=/tmp/vocab-coach-uv-cache VOCAB_ENV=test DATABASE_PATH= DATABASE_URL=${databaseUrl} PYTHONPATH=. uv run uvicorn main:app --host 127.0.0.1 --port ${apiPort}`,
        url: `http://127.0.0.1:${apiPort}/api/v1/health`,
        reuseExistingServer: false,
        timeout: 45000,
      },
      {
        command: `npm run build && npm run preview -- --host 127.0.0.1 --port ${frontendPort}`,
        url: frontendUrl,
        reuseExistingServer: false,
        timeout: 45000,
        env: {
          VITE_API_PORT: String(apiPort),
        },
      },
    ],
  });
}
