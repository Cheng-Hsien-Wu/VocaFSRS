import { test, expect } from '@playwright/test';
import { execSync } from 'child_process';
import path from 'path';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname = path.dirname(__filename);

const TEMP_DB_PATH = path.resolve(__dirname, '../../backend/data/vocab_test.db');
const ABS_DB_URL = 'sqlite+aiosqlite:///' + TEMP_DB_PATH;

// Reset the database to a clean, empty state before running the tests
test.beforeAll(async () => {
  try {
    console.log('Resetting test database...');
    // Drop and recreate all tables in the test DB
    execSync(`cd ../backend && UV_CACHE_DIR=/tmp/vocab-coach-uv-cache VOCAB_ENV=test DATABASE_PATH= PYTHONPATH=. DATABASE_URL=${ABS_DB_URL} uv run python -m app.reset_db`, { stdio: 'inherit' });
    console.log('Test database reset successful.');
  } catch (err) {
    console.error('Failed to reset test database:', err);
    throw err;
  }
});

test.describe('CSV Import E2E Flow', () => {
  test('Complete import flow, duplication check, and placement resumption', async ({ page }) => {
    // Navigate to homepage
    await page.goto('/');
    await page.waitForLoadState('networkidle');

    // Verify no console errors
    const consoleErrors: string[] = [];
    page.on('console', msg => {
      console.log('BROWSER LOG:', msg.text());
      if (msg.type() === 'error') {
        const text = msg.text();
        // Ignore expected 404/400 console error when checking active session or triggering clamping modal
        if (!text.includes('placement-sessions/active') && !text.includes('status of 404') && !text.includes('status of 400') && !text.includes('Failed to load decks') && !text.includes('Failed to fetch')) {
          consoleErrors.push(text);
        }
      }
    });
    page.on('response', async response => {
      if (response.url().includes('/analyze') && response.request().method() === 'POST') {
        const body = await response.text().catch(() => '');
        console.log('ANALYZE RESPONSE:', body);
      }
    });

    // 1. Navigate to Import Page
    const importBtn = page.locator('#import-csv-btn');
    await expect(importBtn).toBeVisible();
    await importBtn.click();
    await expect(page).toHaveURL(/\/import/);

    // 2. Upload representative CSV
    const csvContent = `word,meaning,part_of_speech,hint
fatigue,疲勞,noun,身體狀態
symptom,症狀,noun,生病跡象
physician,內科醫生,noun,專業人員`;

    const fileBuffer = Buffer.from(csvContent, 'utf-8');
    await page.setInputFiles('input[type="file"]', {
      name: 'import_flow_test.csv',
      mimeType: 'text/csv',
      buffer: fileBuffer
    });
    
    // Page should auto-transition to step 2: mapping
    await page.waitForTimeout(500);
    await expect(page.locator('text=設定欄位對應')).toBeVisible();

    // Verify mapping selectors prefilled by alias matching
    await expect(page.locator('select').first()).toHaveValue('word'); // english
    
    // Click Analyze
    await page.locator('button:has-text("開始分析欄位")').click();
    await page.waitForTimeout(1000);

    // 3. Review preview counts (Step 3: Preview)
    await expect(page.locator('text=匯入預覽報告')).toBeVisible();
    await expect(page.locator('text=總筆數').locator('..').locator('div').nth(1)).toHaveText('3');
    await expect(page.locator('text=新增單字').locator('..').locator('div').nth(1)).toHaveText('3');

    // Commit Import (Step 4: Summary)
    await page.locator('button:has-text("確認提交匯入")').click();
    await page.waitForTimeout(1500);

    await expect(page.locator('text=匯入順利完成！')).toBeVisible();
    await expect(
      page.locator('.import-summary-row').filter({ hasText: '新增卡片筆數：' }).locator('strong')
    ).toHaveText('3 筆');

    // Click back to Home
    await page.locator('button:has-text("回到首頁")').click();
    await expect(page).toHaveURL('/');

    // 4. Upload the same file again to verify duplicate handling
    await page.locator('#import-csv-btn').click();
    await page.waitForTimeout(300);

    await page.setInputFiles('input[type="file"]', {
      name: 'import_flow_test.csv',
      mimeType: 'text/csv',
      buffer: fileBuffer
    });
    await page.waitForTimeout(500);
    
    await page.locator('button:has-text("開始分析欄位")').click();
    await page.waitForTimeout(1000);

    // Verify duplication statistics
    await expect(page.locator('text=總筆數').locator('..').locator('div').nth(1)).toHaveText('3');
    await expect(page.locator('text=新增單字').locator('..').locator('div').nth(1)).toHaveText('0');
    await expect(page.locator('text=重複略過').locator('..').locator('div').nth(1)).toHaveText('3');

    await page.locator('button:has-text("確認提交匯入")').click();
    await page.waitForTimeout(1000);
    await expect(page.locator('text=匯入順利完成！')).toBeVisible();
    await expect(
      page.locator('.import-summary-row').filter({ hasText: '忽略之重複單字：' }).locator('strong')
    ).toHaveText('3 筆');

    // Back to home
    await page.locator('button:has-text("回到首頁")').click();

    // 5. Start Placement Session from imported cards.
    // The app only exposes fixed placement sizes; choosing 100 should clamp
    // to the 3 imported cards after the insufficient-cards confirmation.
    await page.locator('#placement-btn-100').click();
    
    await page.waitForTimeout(1000);
    await expect(page).toHaveURL(/\/placement/);

    // Confirm insufficient cards modal to proceed with 3 cards
    const confirmBtn = page.locator('#btn-insufficient-confirm');
    await expect(confirmBtn).toBeVisible();
    await confirmBtn.click();

    // Check term is visible
    const firstTerm = await page.locator('#placement-term').textContent();
    expect(firstTerm).not.toBeNull();

    // Answer "known" to the first item
    await page.locator('#btn-known').click();
    await page.waitForTimeout(300);

    // Should show second card
    const secondTerm = await page.locator('#placement-term').textContent();
    expect(secondTerm).not.toEqual(firstTerm);

    // Reload the page and verify state resumption
    await page.reload();
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(500);

    // Verify still on placement page and restored at correct term (the second term)
    await expect(page).toHaveURL(/\/placement/);
    const restoredTerm = await page.locator('#placement-term').textContent();
    expect(restoredTerm).toEqual(secondTerm);

    // Verify no console errors occurred
    expect(consoleErrors).toHaveLength(0);
  });
});
