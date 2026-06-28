import { test, expect, chromium } from '@playwright/test';
import { spawn, execSync, ChildProcess } from 'child_process';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname_compat = path.dirname(__filename);

const TEMP_DB_PATH = path.join(__dirname_compat, '../../backend/data/temp_mistakes_export_test.db');
const ABS_DB_URL = 'sqlite+aiosqlite:///' + TEMP_DB_PATH;
const USER_DATA_DIR = path.join(__dirname_compat, '../temp-mistakes-export-profile');

let uvicornProcess: ChildProcess;
let viteProcess: ChildProcess;

async function waitPort(port: number) {
  for (let i = 0; i < 50; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/`);
      if (res.status !== 500) {
        return;
      }
    } catch {}
    await new Promise(r => setTimeout(r, 200));
  }
  throw new Error(`Port ${port} failed to respond in time`);
}

test.beforeAll(async () => {
  // Clean up old test database and browser profile directories
  if (fs.existsSync(TEMP_DB_PATH)) {
    try {
      fs.unlinkSync(TEMP_DB_PATH);
    } catch {}
  }
  const metaFile = TEMP_DB_PATH + '.json';
  if (fs.existsSync(metaFile)) {
    try {
      fs.unlinkSync(metaFile);
    } catch {}
  }
  if (fs.existsSync(USER_DATA_DIR)) {
    try {
      fs.rmSync(USER_DATA_DIR, { recursive: true, force: true });
    } catch {}
  }

  // Seed the mistakes export test data
  try {
    console.log('Running app.seed_mistakes_export...');
    execSync(`cd ../backend && PYTHONPATH=. DATABASE_URL=${ABS_DB_URL} uv run python -m app.seed_mistakes_export`, {
      stdio: 'inherit',
      env: { ...process.env, VOCAB_ENV: 'development' }
    });
  } catch (err) {
    console.error('Failed to run seed_mistakes_export.py:', err);
    throw err;
  }

  // Spawn isolated backend on port 8004
  console.log('Spawning isolated FastAPI server on port 8004...');
  uvicornProcess = spawn('uv', [
    'run', 'uvicorn', 'main:app',
    '--host', '127.0.0.1',
    '--port', '8004'
  ], {
    cwd: path.join(__dirname_compat, '../../backend'),
    stdio: 'ignore',
    env: {
      ...process.env,
      DATABASE_URL: ABS_DB_URL,
      VOCAB_ENV: 'development'
    }
  });

  // Preview the built app; E2E does not need HMR or file watchers.
  console.log('Spawning isolated Vite dev server on port 5176...');
  viteProcess = spawn('npx', [
    'vite', 'preview', '--port', '5176'
  ], {
    cwd: path.join(__dirname_compat, '..'),
    stdio: 'ignore',
    env: {
      ...process.env,
      VITE_API_PORT: '8004',
      PORT: '5176'
    }
  });

  await waitPort(8004);
  await waitPort(5176);
  console.log('Isolated servers are fully ready.');
});

test.afterAll(async () => {
  if (uvicornProcess) {
    uvicornProcess.kill('SIGINT');
  }
  if (viteProcess) {
    viteProcess.kill('SIGINT');
  }

  await new Promise(r => setTimeout(r, 1000));

  if (fs.existsSync(TEMP_DB_PATH)) {
    try {
      fs.unlinkSync(TEMP_DB_PATH);
    } catch {}
  }
  const metaFile = TEMP_DB_PATH + '.json';
  if (fs.existsSync(metaFile)) {
    try {
      fs.unlinkSync(metaFile);
    } catch {}
  }
  
  if (fs.existsSync(USER_DATA_DIR)) {
    try {
      fs.rmSync(USER_DATA_DIR, { recursive: true, force: true });
    } catch {}
  }
});

test.describe('Mistakes Export E2E Tests', () => {
  test('Mistakes, exports, and iPhone viewport layouts', async () => {
    test.setTimeout(90000);

    // Launch with permissions for clipboard access
    const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
      viewport: { width: 390, height: 844 },
      permissions: ['clipboard-read', 'clipboard-write'],
    });

    const page = context.pages[0] || await context.newPage();

    // 1. Go to homepage
    await page.goto('http://127.0.0.1:5176/');
    await page.waitForLoadState('networkidle');

    // 2. Click Mistakes and Analysis Page link
    const mistakesLink = page.locator('#mistakes-link');
    await expect(mistakesLink).toBeVisible();
    await mistakesLink.click();
    await page.waitForURL(/\/mistakes/);

    // Verify Title / Header
    await expect(page.locator('text=數據分析與管理')).toBeVisible();

    // 3. Verify mistakes list and filters
    // By default: 7 days is active. Preclude should be listed
    await expect(page.locator('text=preclude').first()).toBeVisible();
    await expect(page.locator('text=排除、阻止、妨礙')).toBeVisible();
    
    // Check hints/counts
    await expect(page.locator('text=再試 3')).toBeVisible();
    await expect(page.locator('text=猶豫 1')).toBeVisible();

    // Click term to expand details
    await page.locator('#btn-mistake-word-preclude').click();
    
    // Verify Traditional Chinese & Long Text (newlines)
    await expect(page.locator('text=這是一個排除任何歧義的長例句。')).toBeVisible();
    await expect(page.locator('text=混淆字: 不知道')).toBeVisible();

    // Test mistake filters
    // Click rating "Again" button
    await page.locator('#filter-mistake-rating-Again').click();
    await page.waitForTimeout(300);
    await expect(page.locator('text=preclude').first()).toBeVisible();

    // Click rating "Hard" button. Filters use the latest rating, so preclude
    // should be hidden even though its historical Hard count is still shown.
    await page.locator('#filter-mistake-rating-Hard').click();
    await page.waitForTimeout(300);
    await expect(page.locator('#btn-mistake-word-preclude')).toHaveCount(0);

    // Click rating "Again" then repeated lapses filter
    await page.locator('#filter-mistake-rating-all').click();
    await page.locator('#filter-mistake-lapses').check();
    await page.waitForTimeout(300);
    await expect(page.locator('text=preclude').first()).toBeVisible(); // Preclude has lapses=2
    await page.locator('#filter-mistake-lapses').uncheck(); // cleanup

    // 4. Export Modal flow
    await page.locator('#btn-trigger-export').click();
    await page.waitForTimeout(500);

    // The public UI exports a NotebookLM-friendly podcast source only.
    await page.locator('#export-filter-type').selectOption('today');
    await page.waitForTimeout(300);

    // Preview
    const previewArea = page.locator('#export-preview-area');
    await expect(previewArea).toBeVisible();
    const previewVal = await previewArea.inputValue();
    expect(previewVal).toContain('Create an English vocabulary review podcast');
    expect(previewVal).toContain('Target vocabulary');
    expect(previewVal).toContain('preclude');
    expect(previewVal).toContain('My answer');
    expect(previewVal).toContain('Correct meaning');

    // Copy to clipboard
    await page.locator('#btn-export-copy').click();
    await page.waitForTimeout(300);
    const clipVal = await page.evaluate(async () => await navigator.clipboard.readText());
    expect(clipVal).toContain('Create an English vocabulary review podcast');

    // Download plain text source
    const [downloadText] = await Promise.all([
      page.waitForEvent('download'),
      page.locator('#btn-export-download').click(),
    ]);
    const textPath = await downloadText.path();
    const textContent = fs.readFileSync(textPath, 'utf8');
    expect(textContent).toContain('Create an English vocabulary review podcast');

    // Close Modal
    await page.locator('button[aria-label="關閉"]').click();

    // 5. Backup/restore and admin UI are intentionally not part of the app.
    await expect(page.locator('#tab-btn-maintenance')).toHaveCount(0);
    await expect(page.locator('#tab-btn-admin')).toHaveCount(0);

    await context.close();
  });
});
