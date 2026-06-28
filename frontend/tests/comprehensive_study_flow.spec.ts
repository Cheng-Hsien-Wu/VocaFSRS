import { test, expect, chromium } from '@playwright/test';
import { spawn, execSync, ChildProcess } from 'child_process';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname_compat = path.dirname(__filename);

const TEMP_DB_PATH = path.join(__dirname_compat, '../../backend/data/temp_comprehensive_test.db');
const ABS_DB_URL = 'sqlite+aiosqlite:///' + TEMP_DB_PATH;
const USER_DATA_DIR = path.join(__dirname_compat, '../temp-comprehensive-profile');

let uvicornProcess: ChildProcess;
let viteProcess: ChildProcess;
const TEST_ENV = {
  ...process.env,
  DATABASE_PATH: '',
  DATABASE_URL: ABS_DB_URL,
  PYTHONPATH: '.',
  VOCAB_ENV: 'development',
};

test.beforeAll(async () => {
  if (fs.existsSync(TEMP_DB_PATH)) {
    try { fs.unlinkSync(TEMP_DB_PATH); } catch {}
  }
  if (fs.existsSync(USER_DATA_DIR)) {
    try { fs.rmSync(USER_DATA_DIR, { recursive: true, force: true }); } catch {}
  }

  // Seed study database
  try {
    execSync('uv run python -m app.seed_study', {
      cwd: path.join(__dirname_compat, '../../backend'),
      stdio: 'inherit',
      env: TEST_ENV,
    });
  } catch (err) {
    console.error('Failed to seed study testing database:', err);
    throw err;
  }

  // Spawn isolated backend on port 8004
  uvicornProcess = spawn('uv', ['run', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8004'], {
    cwd: path.join(__dirname_compat, '../../backend'),
    stdio: 'ignore',
    env: {
      ...process.env,
      DATABASE_PATH: '',
      DATABASE_URL: ABS_DB_URL,
      VOCAB_ENV: 'development'
    }
  });

  // Spawn isolated frontend on port 5176
  viteProcess = spawn('npx', ['vite', 'preview', '--port', '5176'], {
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
});

test.afterAll(async () => {
  if (uvicornProcess) uvicornProcess.kill('SIGINT');
  if (viteProcess) viteProcess.kill('SIGINT');
  try { execSync('fuser -k 8004/tcp 5176/tcp || true'); } catch {}
  
  await new Promise(r => setTimeout(r, 500));

  if (fs.existsSync(TEMP_DB_PATH)) {
    try { fs.unlinkSync(TEMP_DB_PATH); } catch {}
  }
  if (fs.existsSync(USER_DATA_DIR)) {
    try { fs.rmSync(USER_DATA_DIR, { recursive: true, force: true }); } catch {}
  }
});

async function waitPort(port: number) {
  for (let i = 0; i < 50; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/`);
      if (res.status !== 500) return;
    } catch {}
    await new Promise(r => setTimeout(r, 200));
  }
  throw new Error(`Port ${port} failed to respond in time`);
}

test.describe('Comprehensive Study Flow Verification', () => {
  test('Verify that clicking "不知道" does not immediately skip to the next card', async () => {
    const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
      viewport: { width: 390, height: 844 },
    });
    const page = context.pages[0] || await context.newPage();

    // 1. Go to homepage, set study session count
    await page.goto('http://127.0.0.1:5176/');
    await page.waitForLoadState('networkidle');
    await page.evaluate(() => {
      sessionStorage.setItem('study_count', '3');
      sessionStorage.setItem('study_mode', 'fixed');
      sessionStorage.removeItem('study_resume');
    });

    // 2. The study seed creates due FSRS cards, so navigate directly to review.
    await page.goto('http://127.0.0.1:5176/study');
    await expect(page.locator('#study-term')).toBeVisible();

    const initialWord = await page.locator('#study-term').textContent();
    console.log('FSRS Test Word:', initialWord);

    // 3. Click "不知道" button
    const skipBtn = page.locator('#typed-skip');
    await expect(skipBtn).toBeVisible();
    await skipBtn.click();

    // 4. Verify correct answer reveal panel is visible
    const expectedAnswer = page.locator('#typed-expected-answer');
    await expect(expectedAnswer).toBeVisible({ timeout: 2000 });
    
    // 5. Ensure it stays visible and does not auto-advance to next question within 2 seconds
    await page.waitForTimeout(2000);
    await expect(expectedAnswer).toBeVisible();
    
    const currentWord = await page.locator('#study-term').textContent();
    expect(currentWord).toBe(initialWord); // Ensure word did not change

    await context.close();
  });

  test('Verify that partial placement does not allow review bypass', async () => {
    execSync('uv run python -m app.reset_db', {
      cwd: path.join(__dirname_compat, '../../backend'),
      stdio: 'inherit',
      env: TEST_ENV,
    });
    execSync('uv run python -m app.seed_large', {
      cwd: path.join(__dirname_compat, '../../backend'),
      stdio: 'inherit',
      env: TEST_ENV,
    });

    const context = await chromium.launchPersistentContext(path.join(USER_DATA_DIR, 'bypass'), {
      viewport: { width: 390, height: 844 },
    });
    const page = context.pages[0] || await context.newPage();

    // 1. Go to homepage
    await page.goto('http://127.0.0.1:5176/');
    await page.waitForLoadState('networkidle');

    // 2. Answer a few cards on placement
    await page.locator('#placement-btn-250').click();
    await page.waitForURL(/\/placement/);

    // Answer 5 cards to insert them in the ActivationQueue
    for (let i = 0; i < 5; i++) {
      await page.locator('#btn-known').click();
      await page.waitForTimeout(100);
    }

    // 3. Go back home
    await page.goto('http://127.0.0.1:5176/');
    await page.waitForLoadState('networkidle');

    // 4. Try manually navigating to /study (URL Bypassing)
    await page.goto('http://127.0.0.1:5176/study');

    await expect(page.locator('text=正式複習需要先完成盤點')).toBeVisible({ timeout: 5000 });

    await context.close();
  });
});
