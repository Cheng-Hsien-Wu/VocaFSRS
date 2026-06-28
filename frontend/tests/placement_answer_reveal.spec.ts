/**
 * placement_answer_reveal.spec.ts
 *
 * Proves SPEC-required behavior:
 * - known: advance immediately, no meaning revealed
 * - fuzzy: reveal Chinese meaning; user can continue immediately (non-blocking)
 * - unknown: reveal Chinese meaning; user can continue immediately (non-blocking)
 */
import { test, expect } from '@playwright/test';
import { execSync } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname_compat = path.dirname(__filename);

const TEMP_DB_PATH = path.resolve(__dirname_compat, '../../backend/data/vocab_test.db');
const ABS_DB_URL = 'sqlite+aiosqlite:///' + TEMP_DB_PATH;
const API_PORT = process.env.VITE_API_PORT || '8000';

test.beforeAll(async () => {
  try {
    const testEnv = {
      ...process.env,
      DATABASE_PATH: '',
      DATABASE_URL: ABS_DB_URL,
      PYTHONPATH: '.',
      VOCAB_ENV: 'test',
    };
    execSync('uv run python -m app.reset_db', {
      cwd: path.resolve(__dirname_compat, '../../backend'),
      env: testEnv,
      stdio: 'inherit',
    });
    execSync('uv run python -m app.seed', {
      cwd: path.resolve(__dirname_compat, '../../backend'),
      env: testEnv,
      stdio: 'inherit',
    });
  } catch (err) {
    console.error('Failed to seed database for answer-reveal tests:', err);
    throw err;
  }
});

test.beforeEach(async ({ page }) => {
  page.on('console', msg => {
    if (msg.type() === 'error') console.log('BROWSER ERROR:', msg.text());
  });
  try {
    let active = true;
    while (active) {
      const res = await fetch(`http://127.0.0.1:${API_PORT}/api/v1/placement-sessions/active`);
      if (res.ok) {
        const session = await res.json();
        if (session?.id) {
          await fetch(`http://127.0.0.1:${API_PORT}/api/v1/placement-sessions/${session.id}/abandon`, { method: 'POST' });
        } else { active = false; }
      } else { active = false; }
    }
  } catch { /* ignore */ }

  await page.goto('/');
  await page.evaluate(async () => {
    sessionStorage.clear();
    localStorage.clear();
    const dbs = await (indexedDB as any).databases?.() ?? [];
    for (const db of dbs) { if (db.name) indexedDB.deleteDatabase(db.name); }
  });
  await page.reload({ waitUntil: 'networkidle' });
});

async function startPlacementSession(page: any) {
  await page.locator('#placement-btn-100').click();
  await page.waitForURL(/\/placement/, { timeout: 15000 });
  const insufficientModal = page.locator('#insufficient-modal');
  await expect(insufficientModal.or(page.locator('#placement-term'))).toBeVisible({ timeout: 10000 });
  if (await insufficientModal.isVisible()) {
    await page.locator('#btn-insufficient-confirm').click();
  }
  await expect(page.locator('#placement-term')).toBeVisible({ timeout: 10000 });
}

test.describe('Placement answer reveal behavior', () => {
  test('known: no meaning revealed', async ({ page }) => {
    test.setTimeout(30000);
    await startPlacementSession(page);
    await page.locator('#btn-known').click();
    await expect(page.locator('#placement-answer-reveal')).not.toBeVisible();
  });

  test('fuzzy: reveals Chinese meaning and allows immediate Continue', async ({ page }) => {
    test.setTimeout(30000);
    await startPlacementSession(page);
    await page.locator('#btn-fuzzy').click();

    await expect(page.locator('#placement-answer-reveal')).toBeVisible({ timeout: 2000 });
    await expect(page.locator('#placement-flash-meaning')).toBeVisible();
    const meaning = await page.locator('#placement-flash-meaning').textContent();
    expect(meaning?.trim().length).toBeGreaterThan(0);
    await expect(page.locator('#placement-answer-reveal')).toContainText('正確意思');
    await expect(page.locator('#btn-flash-continue')).toBeVisible();

    const t0 = Date.now();
    await page.locator('#btn-flash-continue').click();
    expect(Date.now() - t0).toBeLessThan(1400);

    await expect(page.locator('#placement-answer-reveal')).not.toBeVisible();
    await expect(page.locator('#btn-known')).toBeVisible();
  });

  test('unknown: reveals Chinese meaning and allows immediate Continue', async ({ page }) => {
    test.setTimeout(30000);
    await startPlacementSession(page);
    await page.locator('#btn-unknown').click();

    await expect(page.locator('#placement-answer-reveal')).toBeVisible({ timeout: 2000 });
    await expect(page.locator('#placement-flash-meaning')).toBeVisible();
    const meaning = await page.locator('#placement-flash-meaning').textContent();
    expect(meaning?.trim().length).toBeGreaterThan(0);
    await expect(page.locator('#btn-flash-continue')).toBeVisible();

    const t0 = Date.now();
    await page.locator('#btn-flash-continue').click();
    expect(Date.now() - t0).toBeLessThan(1400);

    await expect(page.locator('#placement-answer-reveal')).not.toBeVisible();
    await expect(page.locator('#btn-known')).toBeVisible();
  });

  test('fuzzy: does NOT auto-dismiss and requires manual Continue', async ({ page }) => {
    test.setTimeout(30000);
    await startPlacementSession(page);
    await page.locator('#btn-fuzzy').click();
    await expect(page.locator('#placement-answer-reveal')).toBeVisible({ timeout: 2000 });
    await page.waitForTimeout(2000);
    // Should still be visible after 2s
    await expect(page.locator('#placement-answer-reveal')).toBeVisible();
    await page.locator('#btn-flash-continue').click();
    await expect(page.locator('#placement-answer-reveal')).not.toBeVisible();
    await expect(page.locator('#btn-known')).toBeVisible();
  });

  test('unknown: does NOT auto-dismiss and requires manual Continue', async ({ page }) => {
    test.setTimeout(30000);
    await startPlacementSession(page);
    await page.locator('#btn-unknown').click();
    await expect(page.locator('#placement-answer-reveal')).toBeVisible({ timeout: 2000 });
    await page.waitForTimeout(2000);
    // Should still be visible after 2s
    await expect(page.locator('#placement-answer-reveal')).toBeVisible();
    await page.locator('#btn-flash-continue').click();
    await expect(page.locator('#placement-answer-reveal')).not.toBeVisible();
    await expect(page.locator('#btn-known')).toBeVisible();
  });

  test('rapid answering: 5 fuzzy/unknown without waiting for auto-dismiss', async ({ page }) => {
    test.setTimeout(30000);
    await startPlacementSession(page);
    for (let i = 0; i < 5; i++) {
      const btn = i % 2 === 0 ? '#btn-fuzzy' : '#btn-unknown';
      await page.locator(btn).click();
      await expect(page.locator('#btn-flash-continue')).toBeVisible({ timeout: 2000 });
      await page.locator('#btn-flash-continue').click();
      await expect(page.locator('#btn-known')).toBeVisible({ timeout: 2000 });
    }
  });

  test('Placement: no TTS autoplay on card display', async ({ page }) => {
    test.setTimeout(30000);
    await page.addInitScript(() => {
      (window as any).__ttsCalls = 0;
      const orig = SpeechSynthesis.prototype.speak;
      SpeechSynthesis.prototype.speak = function (...args: any[]) {
        (window as any).__ttsCalls++;
        return orig.apply(this, args);
      };
    });
    await startPlacementSession(page);
    await page.waitForTimeout(500);
    expect(await page.evaluate(() => (window as any).__ttsCalls ?? 0)).toBe(0);
    await page.locator('#btn-known').click();
    await page.waitForTimeout(300);
    expect(await page.evaluate(() => (window as any).__ttsCalls ?? 0)).toBe(0);
  });
});
