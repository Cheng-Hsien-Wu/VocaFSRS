import { test, expect, chromium } from '@playwright/test';
import { spawn, execSync, ChildProcess } from 'child_process';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname_compat = path.dirname(__filename);

const TEMP_DB_PATH = path.join(__dirname_compat, '../../backend/data/temp_study_test.db');
const ABS_DB_URL = 'sqlite+aiosqlite:///' + TEMP_DB_PATH;
const USER_DATA_DIR = path.join(__dirname_compat, '../temp-study-profile');

let uvicornProcess: ChildProcess;
let viteProcess: ChildProcess;

test.beforeAll(async () => {
  if (fs.existsSync(TEMP_DB_PATH)) fs.unlinkSync(TEMP_DB_PATH);
  if (fs.existsSync(USER_DATA_DIR)) fs.rmSync(USER_DATA_DIR, { recursive: true, force: true });

  execSync(`cd ../backend && PYTHONPATH=. DATABASE_URL=${ABS_DB_URL} uv run python -m app.seed_study`, {
    stdio: 'inherit',
    env: { ...process.env, VOCAB_ENV: 'development' }
  });

  uvicornProcess = spawn('uv', ['run', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8003'], {
    cwd: path.join(__dirname_compat, '../../backend'),
    stdio: 'ignore',
    env: {
      ...process.env,
      DATABASE_URL: ABS_DB_URL,
      VOCAB_ENV: 'test',
      LLM_TEST_MODE: 'mock',
    }
  });

  viteProcess = spawn('npx', ['vite', 'preview', '--port', '5175'], {
    cwd: path.join(__dirname_compat, '..'),
    stdio: 'ignore',
    env: { ...process.env, VITE_API_PORT: '8003', PORT: '5175' }
  });

  await waitPort(8003);
  await waitPort(5175);
});

test.afterAll(async () => {
  if (uvicornProcess) uvicornProcess.kill('SIGINT');
  if (viteProcess) viteProcess.kill('SIGINT');
  try {
    execSync('fuser -k 8003/tcp 5175/tcp || true');
  } catch {}
  await new Promise(r => setTimeout(r, 1000));
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

test.describe('Typed Study & LLM Adjudication E2E', () => {
  test('typed answers wait for LLM before FSRS scheduling', async () => {
    test.setTimeout(90000);

    const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
      viewport: { width: 390, height: 844 },
    });
    const page = context.pages[0] || await context.newPage();

    await page.goto('http://127.0.0.1:5175/');
    await page.waitForLoadState('load');
    await page.evaluate(() => {
      sessionStorage.setItem('study_count', '3');
      sessionStorage.setItem('study_mode', 'fixed');
      sessionStorage.removeItem('study_resume');
    });
    await page.goto('http://127.0.0.1:5175/study');
    await expect(page.locator('#study-term')).toBeVisible();

    for (let i = 0; i < 3; i++) {
      await page.locator('#typed-answer').fill(i === 0 ? '測試中文_0' : `隨便_${i}`);
      await page.locator('#typed-submit').click();
      await expect(page.locator('#typed-expected-answer')).toBeVisible();
      await page.locator('#typed-next').click();
    }

    await page.waitForURL(/\/study\/summary/, { timeout: 25000 });
    await expect(page.locator('text=LLM 批改完成')).toBeVisible({ timeout: 30000 });
    const summaryText = await page.locator('body').textContent();
    expect(summaryText).toContain('Good 1');
    expect(summaryText).toMatch(/Hard [02] · Again [02]/);

    const dbState = await page.evaluate(async () => {
      const w = window as any;
      const logs = await fetch('/api/v1/study-sessions/plan').then(r => r.json());
      return {
        planStarted: logs.started,
        localItems: await w.db.study_items.toArray(),
      };
    });
    expect(dbState.planStarted).toBe(true);
    expect(dbState.localItems).toHaveLength(3);
    expect(dbState.localItems.every((item: any) => item.result === 'Pending')).toBe(true);

    await context.close();
  });

  test('timed typed study still shows timer', async () => {
    const context = await chromium.launchPersistentContext(path.join(USER_DATA_DIR, 'timed'), {
      viewport: { width: 390, height: 844 },
    });
    const page = context.pages[0] || await context.newPage();
    await page.goto('http://127.0.0.1:5175/');
    await page.evaluate(() => {
      sessionStorage.setItem('study_count', '10');
      sessionStorage.setItem('study_mode', 'timed');
      sessionStorage.removeItem('study_resume');
    });
    await page.goto('http://127.0.0.1:5175/study');
    await expect(page.locator('#timer-display')).toBeVisible({ timeout: 10000 });
    await expect(page.locator('#typed-answer')).toBeVisible();
    await context.close();
  });
});
