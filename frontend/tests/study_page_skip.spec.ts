import { test, expect, chromium } from '@playwright/test';
import { spawn, execSync, ChildProcess } from 'child_process';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname_compat = path.dirname(__filename);

const TEMP_DB_PATH = path.join(__dirname_compat, '../../backend/data/temp_skip_test.db');
const ABS_DB_URL = 'sqlite+aiosqlite:///' + TEMP_DB_PATH;
const USER_DATA_DIR = path.join(__dirname_compat, '../temp-skip-profile');

let uvicornProcess: ChildProcess;
let viteProcess: ChildProcess;

test.beforeEach(async () => {
  if (fs.existsSync(TEMP_DB_PATH)) {
    try { fs.unlinkSync(TEMP_DB_PATH); } catch {}
  }
  if (fs.existsSync(USER_DATA_DIR)) {
    try { fs.rmSync(USER_DATA_DIR, { recursive: true, force: true }); } catch {}
  }

  // Seed study database
  try {
    execSync(`cd ../backend && PYTHONPATH=. DATABASE_URL=${ABS_DB_URL} uv run python -m app.seed_study`, {
      stdio: 'inherit',
      env: { ...process.env, VOCAB_ENV: 'development' }
    });
  } catch (err) {
    console.error('Failed to seed study testing database:', err);
    throw err;
  }

  // Spawn isolated backend on port 8003
  uvicornProcess = spawn('uv', ['run', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8003'], {
    cwd: path.join(__dirname_compat, '../../backend'),
    stdio: 'ignore',
    env: {
      ...process.env,
      DATABASE_URL: ABS_DB_URL,
      VOCAB_ENV: 'development'
    }
  });

  // Spawn isolated frontend on port 5175
  viteProcess = spawn('npx', ['vite', 'preview', '--port', '5175'], {
    cwd: path.join(__dirname_compat, '..'),
    stdio: 'ignore',
    env: {
      ...process.env,
      VITE_API_PORT: '8003',
      PORT: '5175'
    }
  });

  await waitPort(8003);
  await waitPort(5175);
});

test.afterEach(async () => {
  if (uvicornProcess) uvicornProcess.kill('SIGINT');
  if (viteProcess) viteProcess.kill('SIGINT');
  try { execSync('fuser -k 8003/tcp 5175/tcp || true'); } catch {}
  
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

test.describe('Study Skip Answer Behavior', () => {
  test('Clicking "不知道" reveals the correct answer and holds it visible until clicking next', async () => {
    test.setTimeout(60000);
    const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
      viewport: { width: 390, height: 844 },
    });
    const studyPage = context.pages[0] || await context.newPage();

    // 1. Go to homepage, set study session count
    await studyPage.goto('http://127.0.0.1:5175/');
    await studyPage.waitForLoadState('networkidle');
    await studyPage.evaluate(() => {
      sessionStorage.setItem('study_count', '3');
      sessionStorage.setItem('study_mode', 'fixed');
      sessionStorage.removeItem('study_resume');
    });

    // 2. Direct navigate to /study
    await studyPage.goto('http://127.0.0.1:5175/study');
    await expect(studyPage.locator('#study-term')).toBeVisible();

    const initialWord = await studyPage.locator('#study-term').textContent();
    console.log('Initial word:', initialWord);

    // 3. Click "不知道" button
    const skipBtn = studyPage.locator('#typed-skip');
    await expect(skipBtn).toBeVisible();
    await skipBtn.click();

    // 4. Verify correct answer reveal panel is visible
    const expectedAnswer = studyPage.locator('#typed-expected-answer');
    await expect(expectedAnswer).toBeVisible({ timeout: 2000 });
    const answerText = await expectedAnswer.textContent();
    console.log('Expected answer displayed:', answerText);
    expect(answerText?.trim().length).toBeGreaterThan(0);

    // 5. Wait for 2.5 seconds to assert it DOES NOT auto-advance or disappear
    await studyPage.waitForTimeout(2500);
    await expect(expectedAnswer).toBeVisible();
    const currentWord = await studyPage.locator('#study-term').textContent();
    expect(currentWord).toBe(initialWord); // Ensure word has not advanced yet

    // 6. Click "下一題" button to advance
    const nextBtn = studyPage.locator('#typed-next');
    await expect(nextBtn).toBeVisible();
    await nextBtn.click();

    // 7. Verify we advanced to the next card
    await expect(studyPage.locator('#study-term')).not.toHaveText(initialWord ?? '');
    const newWord = await studyPage.locator('#study-term').textContent();
    console.log('New word after clicking next:', newWord);
    expect(newWord).not.toBe(initialWord);

    await context.close();
  });
});
