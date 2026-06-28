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
    console.log('Resetting and seeding test database for clamping tests...');
    execSync(`cd ../backend && PYTHONPATH=. DATABASE_URL=${ABS_DB_URL} uv run python -m app.reset_db`, { stdio: 'inherit' });
    execSync(`cd ../backend && PYTHONPATH=. DATABASE_URL=${ABS_DB_URL} uv run python -m app.seed`, { stdio: 'inherit' });
  } catch (err) {
    console.error('Failed to seed database for clamping tests:', err);
    throw err;
  }
});

const VIEWPORTS = [
  { name: 'iphone-se', width: 375, height: 667 },
  { name: 'iphone-14', width: 390, height: 844 },
  { name: 'iphone-15-pro-max', width: 430, height: 932 },
];

const THEMES = ['light', 'dark'] as const;

async function setTheme(page: any, theme: 'light' | 'dark') {
  await page.evaluate((t) => {
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem('theme', t);
  }, theme);
}

test.beforeEach(async ({ page }) => {
  page.on('console', msg => {
    if (msg.type() === 'error') {
      console.log('BROWSER ERROR:', msg.text());
    }
  });

  // 1. Abandon active sessions on backend directly from Node context
  try {
    let active = true;
    while (active) {
      const activeRes = await fetch(`http://127.0.0.1:${API_PORT}/api/v1/placement-sessions/active`);
      if (activeRes.ok) {
        const session = await activeRes.json();
        if (session && session.id) {
          await fetch(`http://127.0.0.1:${API_PORT}/api/v1/placement-sessions/${session.id}/abandon`, { method: 'POST' });
        } else {
          active = false;
        }
      } else {
        active = false;
      }
    }
  } catch {
    // ignore
  }

  // 2. Load BASE_URL, clear sessionStorage, close Dexie database, and delete it
  await page.goto('/');
  await page.evaluate(async () => {
    sessionStorage.clear();
    const w = window as any;
    if (w.db) {
      try {
        w.db.close();
      } catch {}
    }
    await new Promise<void>((resolve) => {
      const req = indexedDB.deleteDatabase('VocabCoachDatabase');
      req.onsuccess = () => resolve();
      req.onerror = () => resolve();
      req.onblocked = () => resolve();
    });
  });
});

test.describe('Placement Clamping & Modal UI', () => {
  for (const vp of VIEWPORTS) {
    for (const theme of THEMES) {
      test(`Insufficient cards modal - ${vp.name} - ${theme}`, async ({ page }) => {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        await setTheme(page, theme);
        
        await page.evaluate(() => {
          sessionStorage.setItem('placement_count', '100');
          sessionStorage.removeItem('placement_resume');
          sessionStorage.removeItem('placement_decks');
        });
        
        await page.goto('/placement');

        // Verify insufficient card modal is displayed
        await expect(page.locator('#insufficient-modal')).toBeVisible();
        await expect(page.locator('#insufficient-title')).toHaveText('可盤點字數不足');
        await expect(page.locator('#insufficient-desc')).toContainText('僅有 26 張符合條件的卡片');

        // Capture screenshot of the modal
        await page.screenshot({
          path: `screenshots/clamping-modal-${vp.name}-${theme}.png`,
          fullPage: false,
        });

        // Test cancel
        const cancelBtn = page.locator('#btn-insufficient-cancel');
        await expect(cancelBtn).toBeVisible();
        await cancelBtn.click();
        await expect(page).toHaveURL('/');
      });

      test(`Confirm and retry - ${vp.name} - ${theme}`, async ({ page }) => {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        await setTheme(page, theme);
        
        await page.evaluate(() => {
          sessionStorage.setItem('placement_count', '100');
          sessionStorage.removeItem('placement_resume');
          sessionStorage.removeItem('placement_decks');
        });
        
        await page.goto('/placement');

        // Click confirm
        const confirmBtn = page.locator('#btn-insufficient-confirm');
        await expect(confirmBtn).toBeVisible();
        await confirmBtn.click();

        // Should load the session successfully with 26 cards
        await expect(page.locator('#insufficient-modal')).not.toBeVisible();
        await expect(page.locator('#placement-term')).toBeVisible();
        
        // Verify sessionStorage is updated to 26
        const placementCount = await page.evaluate(() => sessionStorage.getItem('placement_count'));
        expect(placementCount).toEqual('26');
      });

      test(`Stale deck scope is ignored - ${vp.name} - ${theme}`, async ({ page }) => {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        await setTheme(page, theme);
        
        // Old builds stored deck scope in sessionStorage. Current single-vocabulary
        // UI ignores that stale key and starts from the default vocabulary.
        await page.evaluate(() => {
          sessionStorage.setItem('placement_count', '10');
          sessionStorage.setItem('placement_decks', JSON.stringify(['non-existent-deck']));
          sessionStorage.removeItem('placement_resume');
        });
        
        await page.goto('/placement');
        await expect(page.locator('#placement-term')).toBeVisible();
        await expect(page.locator('text=此字庫篩選範圍中沒有符合條件的卡片')).toBeHidden();
        
        await page.screenshot({
          path: `screenshots/clamping-stale-deck-ignored-${vp.name}-${theme}.png`,
          fullPage: false,
        });
      });
    }
  }
});
