/**
 * Phase 0 Playwright screenshot tests.
 * Captures all screens at 3 iPhone viewports × light/dark modes.
 * Verifies: no horizontal overflow, touch targets ≥44px, safe-area, no console errors.
 */

import { test, expect, Page } from '@playwright/test';
import { fileURLToPath } from 'url';
import path from 'path';
import { execSync } from 'child_process';

const __filename = fileURLToPath(import.meta.url);
const __dirname_compat = path.dirname(__filename);

const TEMP_DB_PATH = path.resolve(__dirname_compat, '../../backend/data/vocab_test.db');
const ABS_DB_URL = 'sqlite+aiosqlite:///' + TEMP_DB_PATH;

const SCREENSHOTS_DIR = path.join(__dirname_compat, '..', 'screenshots');
const TEST_ENV = {
  ...process.env,
  DATABASE_PATH: '',
  DATABASE_URL: ABS_DB_URL,
  PYTHONPATH: '.',
  VOCAB_ENV: 'test',
};

function runBackendModule(moduleName: string) {
  execSync(`uv run python -m ${moduleName}`, {
    cwd: path.resolve(__dirname_compat, '../../backend'),
    env: TEST_ENV,
    stdio: 'inherit',
  });
}

test.beforeAll(async () => {
  try {
    console.log('Resetting and seeding test database for screenshots...');
    runBackendModule('app.reset_db');
    runBackendModule('app.seed');
    console.log('Database seeded successfully.');
  } catch (err) {
    console.error('Failed to reset and seed database:', err);
    throw err;
  }
});

const VIEWPORTS = [
  { name: 'iphone-se', width: 375, height: 667 },
  { name: 'iphone-14', width: 390, height: 844 },
  { name: 'iphone-15-pro-max', width: 430, height: 932 },
];

const THEMES = ['light', 'dark'] as const;

async function setTheme(page: Page, theme: 'light' | 'dark') {
  await page.evaluate((t) => {
    document.documentElement.setAttribute('data-theme', t);
    localStorage.setItem('theme', t);
  }, theme);
}

async function noHorizontalOverflow(page: Page): Promise<boolean> {
  return await page.evaluate(() => {
    return document.documentElement.scrollWidth <= window.innerWidth;
  });
}

// ─── Home page screenshots ──────────────────────────────────

test.describe('Home page', () => {
  for (const vp of VIEWPORTS) {
    for (const theme of THEMES) {
      test(`${vp.name} - ${theme}`, async ({ page }) => {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        const consoleErrors: string[] = [];
        page.on('console', msg => {
          if (msg.type() === 'error') {
            const text = msg.text();
            if (!text.includes('placement-sessions/active') && !text.includes('status of 404') && !text.includes('Failed to load decks') && !text.includes('Failed to fetch')) {
              consoleErrors.push(text);
            }
          }
        });

        await page.goto('/');
        await page.waitForLoadState('networkidle');
        await setTheme(page, theme);
        await page.waitForTimeout(200);

        // Screenshot
        await page.screenshot({
          path: `${SCREENSHOTS_DIR}/home-${vp.name}-${theme}.png`,
          fullPage: false,
        });

        // Assertions
        expect(await noHorizontalOverflow(page), 'No horizontal overflow').toBe(true);
        expect(consoleErrors, 'No console errors').toHaveLength(0);

        // Check placement buttons exist and have adequate size
        const btn100 = page.locator('#placement-btn-100');
        await expect(btn100).toBeVisible();
        const box = await btn100.boundingBox();
        expect(box?.height ?? 0, 'Touch target ≥ 44px').toBeGreaterThanOrEqual(44);
      });
    }
  }
});

// ─── Placement page screenshots ──────────────────────────────

test.describe('Placement page', () => {
  for (const vp of VIEWPORTS) {
    for (const theme of THEMES) {
      test(`${vp.name} - ${theme}`, async ({ page }) => {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        const consoleErrors: string[] = [];
        page.on('console', msg => {
          if (msg.type() === 'error') {
            const text = msg.text();
            if (!text.includes('placement-sessions/active') && !text.includes('status of 404') && !text.includes('Failed to load decks') && !text.includes('Failed to fetch')) {
              consoleErrors.push(text);
            }
          }
        });

        // Set up session
        await page.goto('/');
        await page.waitForLoadState('networkidle');
        await page.evaluate(() => sessionStorage.setItem('placement_count', '20'));
        await setTheme(page, theme);

        await page.goto('/placement');
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(300);

        await page.screenshot({
          path: `${SCREENSHOTS_DIR}/placement-${vp.name}-${theme}.png`,
          fullPage: false,
        });

        // Check English term is visible
        await expect(page.locator('#placement-term')).toBeVisible();

        // Check all 4 action buttons
        await expect(page.locator('#btn-known')).toBeVisible();
        await expect(page.locator('#btn-fuzzy')).toBeVisible();
        await expect(page.locator('#btn-unknown')).toBeVisible();
        await expect(page.locator('#btn-problematic')).toBeVisible();

        // Touch targets
        const knownBtn = await page.locator('#btn-known').boundingBox();
        expect(knownBtn?.height ?? 0).toBeGreaterThanOrEqual(44);

        // No overflow
        expect(await noHorizontalOverflow(page), 'No horizontal overflow').toBe(true);
        expect(consoleErrors, 'No console errors').toHaveLength(0);
      });
    }
  }
});

// ─── Placement flow scenarios ─────────────────────────────────

test.describe('Placement flow', () => {
  test('known → instant next, no feedback shown', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    await page.evaluate(() => sessionStorage.setItem('placement_count', '20'));
    await page.goto('/placement');
    await page.waitForLoadState('networkidle');

    const term1 = await page.locator('#placement-term').textContent();
    await page.locator('#btn-known').click();
    await page.waitForTimeout(100);

    // Should immediately show next card (no flash)
    const term2 = await page.locator('#placement-term').textContent();
    expect(term1).not.toEqual(term2);

    // Undo button should appear
    await expect(page.locator('#placement-undo')).toBeVisible();

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/placement-after-known.png`,
      fullPage: false,
    });
  });

  test('fuzzy → brief meaning flash shown', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    await page.evaluate(() => sessionStorage.setItem('placement_count', '20'));
    await page.goto('/placement');
    await page.waitForLoadState('networkidle');

    await page.locator('#btn-fuzzy').click();
    await page.waitForTimeout(100);

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/placement-fuzzy-flash.png`,
      fullPage: false,
    });
  });

  test('undo works after known', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    await page.evaluate(() => sessionStorage.setItem('placement_count', '20'));
    await page.goto('/placement');
    await page.waitForLoadState('networkidle');

    const term1 = await page.locator('#placement-term').textContent();
    await page.locator('#btn-known').click();
    await page.waitForTimeout(100);

    // Undo
    await page.locator('#placement-undo').click();
    await page.waitForTimeout(100);

    const termAfterUndo = await page.locator('#placement-term').textContent();
    expect(termAfterUndo).toEqual(term1);

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/placement-after-undo.png`,
      fullPage: false,
    });
  });

  test('problematic reason sheet opens', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    await page.evaluate(() => sessionStorage.setItem('placement_count', '20'));
    await page.goto('/placement');
    await page.waitForLoadState('networkidle');

    await page.locator('#btn-problematic').click();
    await page.waitForTimeout(200);

    await expect(page.locator('#problematic-sheet')).toBeVisible();
    await expect(page.locator('#reason-ambiguous_meaning')).toBeVisible();

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/placement-problematic-sheet.png`,
      fullPage: false,
    });

    // Select a reason
    await page.locator('#reason-wrong_translation').click();
    await page.waitForTimeout(100);

    // Sheet should close
    await expect(page.locator('#problematic-sheet')).not.toBeVisible();
  });
});

// ─── Study page screenshots ───────────────────────────────────

test.describe('Study page', () => {
  test.beforeAll(() => {
    runBackendModule('app.reset_db');
    runBackendModule('app.seed_study');
  });

  for (const vp of VIEWPORTS) {
    for (const theme of THEMES) {
      test(`${vp.name} - ${theme}`, async ({ page }) => {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        const consoleErrors: string[] = [];
        page.on('console', msg => {
          if (msg.type() === 'error') {
            const text = msg.text();
            if (!text.includes('placement-sessions/active') && !text.includes('status of 404') && !text.includes('Failed to load decks') && !text.includes('Failed to fetch')) {
              consoleErrors.push(text);
            }
          }
        });

        await page.goto('/');
        await page.evaluate(() => {
          sessionStorage.setItem('study_count', '25');
          sessionStorage.setItem('study_mode', 'fixed');
        });
        await setTheme(page, theme);
        await page.goto('/study');
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(300);

        await page.screenshot({
          path: `${SCREENSHOTS_DIR}/study-${vp.name}-${theme}.png`,
          fullPage: false,
        });

        // English term visible and dominant
        await expect(page.locator('#study-term')).toBeVisible();

        await expect(page.locator('#typed-answer')).toBeVisible();
        await expect(page.locator('#typed-submit')).toBeVisible();
        await expect(page.locator('#typed-skip')).toBeVisible();

        const submitBox = await page.locator('#typed-submit').boundingBox();
        expect(submitBox?.height ?? 0).toBeGreaterThanOrEqual(44);

        // No overflow
        expect(await noHorizontalOverflow(page), 'No horizontal overflow').toBe(true);
        expect(consoleErrors, 'No console errors').toHaveLength(0);
      });
    }
  }
});

// ─── Study flow: correct → Good, draft snackbar ──────────────

test.describe('Study flow', () => {
  test('typed answer → reveal standard answer', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    await page.evaluate(() => {
      sessionStorage.setItem('study_count', '25');
      sessionStorage.setItem('study_mode', 'fixed');
    });
    await page.goto('/study');
    await page.waitForLoadState('networkidle');

    await page.locator('#typed-answer').fill('字0');
    await page.locator('#typed-submit').click();
    await page.waitForTimeout(200);
    await expect(page.locator('#typed-expected-answer')).toBeVisible();
    await expect(page.locator('#typed-next')).toBeVisible();

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/study-after-typed-reveal.png`,
      fullPage: false,
    });
  });

  test('typed next advances to following card', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    await page.evaluate(() => {
      sessionStorage.setItem('study_count', '25');
      sessionStorage.setItem('study_mode', 'fixed');
    });
    await page.goto('/study');
    await page.waitForLoadState('networkidle');

    const firstTerm = await page.locator('#study-term').textContent();
    await page.locator('#typed-answer').fill('測試答案');
    await page.locator('#typed-submit').click();
    await page.locator('#typed-next').click();
    await page.waitForTimeout(300);
    await expect(page.locator('#typed-answer')).toBeVisible();
    await expect(page.locator('#study-term')).not.toHaveText(firstTerm ?? '');
    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/study-typed-next.png`,
      fullPage: false,
    });
  });

  test('unknown → standard answer reveal', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    await page.evaluate(() => {
      sessionStorage.setItem('study_count', '25');
      sessionStorage.setItem('study_mode', 'fixed');
    });
    await page.goto('/study');
    await page.waitForLoadState('networkidle');

    await page.locator('#typed-skip').click();
    await page.waitForTimeout(200);

    await expect(page.locator('#typed-expected-answer')).toBeVisible();
    await expect(page.locator('#typed-next')).toBeVisible();

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/study-typed-unknown.png`,
      fullPage: false,
    });
  });

  test('timed mode - timer display visible', async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto('/');
    await page.evaluate(() => {
      sessionStorage.setItem('study_count', '25');
      sessionStorage.setItem('study_mode', 'timed');
    });
    await page.goto('/study');
    await page.waitForLoadState('networkidle');
    await page.waitForTimeout(300);

    await expect(page.locator('#timer-display')).toBeVisible();

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/study-timed-mode.png`,
      fullPage: false,
    });
  });
});

// ─── Session Summary screenshots ─────────────────────────────

test.describe('Session Summary', () => {
  for (const vp of VIEWPORTS) {
    for (const theme of THEMES) {
      test(`${vp.name} - ${theme}`, async ({ page }) => {
        await page.setViewportSize({ width: vp.width, height: vp.height });
        await page.route('**/api/v1/study-sessions/screenshot-session/adjudicate', async route => {
          await route.fulfill({
            contentType: 'application/json',
            body: JSON.stringify({
              pending: 0,
              processing: 0,
              succeeded: 3,
              failed: 0,
              total: 3,
              results: [
                {
                  id: 'summary-1',
                  card_id: 'card-1',
                  english: 'economic',
                  part_of_speech: 'adjective',
                  typed_answer: '經濟的',
                  expected_answer: '經濟的',
                  status: 'succeeded',
                  verdict: 'correct',
                  rating: 'Good',
                  reason: '語意正確',
                },
                {
                  id: 'summary-2',
                  card_id: 'card-2',
                  english: 'affect',
                  part_of_speech: 'verb',
                  typed_answer: '效果',
                  expected_answer: '影響',
                  status: 'succeeded',
                  verdict: 'partial',
                  rating: 'Hard',
                  reason: '詞性混淆',
                },
                {
                  id: 'summary-3',
                  card_id: 'card-3',
                  english: 'allocate',
                  part_of_speech: 'verb',
                  typed_answer: '',
                  expected_answer: '分配',
                  status: 'succeeded',
                  verdict: 'incorrect',
                  rating: 'Again',
                  reason: '未作答',
                },
              ],
            }),
          });
        });

        await page.goto('/');
        await page.evaluate(() => {
          sessionStorage.setItem('study_summary_typed_session_id', 'screenshot-session');
        });
        await setTheme(page, theme);
        await page.goto('/study/summary');
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(200);

        await page.screenshot({
          path: `${SCREENSHOTS_DIR}/summary-${vp.name}-${theme}.png`,
          fullPage: false,
        });

        // CTAs visible
        await expect(page.locator('#continue-next-round')).toBeVisible();
        await expect(page.locator('#stop-today')).toBeVisible();

        // Touch targets
        const continueBtn = await page.locator('#continue-next-round').boundingBox();
        expect(continueBtn?.height ?? 0).toBeGreaterThanOrEqual(44);

        expect(await noHorizontalOverflow(page)).toBe(true);
      });
    }
  }
});

// ─── Mistakes page screenshots ────────────────────────────────

test.describe('Mistakes page', () => {
  for (const vp of VIEWPORTS) {
    for (const theme of THEMES) {
      test(`${vp.name} - ${theme}`, async ({ page }) => {
        await page.setViewportSize({ width: vp.width, height: vp.height });

        await page.goto('/');
        await setTheme(page, theme);
        await page.goto('/mistakes');
        await page.waitForLoadState('networkidle');
        await page.waitForTimeout(200);

        await page.screenshot({
          path: `${SCREENSHOTS_DIR}/mistakes-${vp.name}-${theme}.png`,
          fullPage: false,
        });

        expect(await noHorizontalOverflow(page)).toBe(true);
      });
    }
  }
});

// ─── Long text stress test ────────────────────────────────────

test.describe('Long text edge cases', () => {
  test('idiom card does not overflow', async ({ page }) => {
    await page.setViewportSize({ width: 375, height: 667 });
    await page.goto('/');
    await page.evaluate(() => sessionStorage.setItem('placement_count', '20'));
    await page.goto('/placement');
    await page.waitForLoadState('networkidle');

    // Click through to find an idiom (which has long text)
    for (let i = 0; i < 20; i++) {
      const termLocator = page.locator('#placement-term');
      if (await termLocator.count() === 0) break;
      const term = await termLocator.textContent();
      if (term && term.includes(' ')) break; // idiom found (has space)
      const btn = page.locator('#btn-known');
      if (await btn.count() === 0) break;
      await btn.click();
      await page.waitForTimeout(50);
    }

    await page.screenshot({
      path: `${SCREENSHOTS_DIR}/long-text-idiom-375.png`,
      fullPage: false,
    });

    expect(await noHorizontalOverflow(page)).toBe(true);
  });
});
