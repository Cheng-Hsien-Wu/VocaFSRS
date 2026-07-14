import { expect, test } from '@playwright/test';

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/llm-settings', route => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      provider: 'auto', model: null, base_url: null, timeout_seconds: 45,
      api_key_configured: false, api_key_source: 'none', effective_model: '',
      fallback_routes: [], batch_size: 10, max_concurrency: 2,
      effective_route_chain: [], provider_readiness: [],
    }),
  }));
  await page.route('**/api/v1/notification-settings', route => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ minimum_due_count: 10, discord_configured: false }),
  }));
});

function studyPlan(dueCount: number, nextDue: string | null = null) {
  return {
    started: true,
    remaining_days: 0,
    remaining_new_cards: 0,
    suggested_new_cards_today: 0,
    due_count: dueCount,
    next_due: nextDue,
    pending_new_count: 0,
    available_now_count: dueCount,
    next_review_due_at: nextDue,
    pending_adjudication_count: 0,
    availability_state: dueCount > 0 ? 'available_due' : 'waiting',
    placement_status: {
      status: 'complete',
      complete: true,
      total_eligible_count: 10,
      remaining_count: 0,
      active_session_id: null,
      active_session_status: null,
    },
  };
}

test('home refreshes its data without reloading or navigating', async ({ page }) => {
  let dueCount = 1;
  let planRequests = 0;
  await page.route('**/api/v1/study-sessions/plan', async route => {
    planRequests += 1;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(studyPlan(dueCount)) });
  });

  await page.goto('/');
  await page.waitForLoadState('networkidle');
  await expect(page.locator('.home-status-strip').getByText('1', { exact: true })).toBeVisible();

  dueCount = 7;
  const requestsBeforeClick = planRequests;
  await page.getByRole('button', { name: '更新首頁狀態' }).click();
  await expect.poll(() => planRequests).toBeGreaterThan(requestsBeforeClick);
  await expect(page.locator('.home-status-strip').getByText('7', { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/$/);
  await expect(page.getByRole('button', { name: '更新首頁狀態' })).toBeEnabled();

  dueCount = 8;
  const requestsBeforeFocus = planRequests;
  await page.evaluate(() => window.dispatchEvent(new Event('focus')));
  await expect.poll(() => planRequests).toBeGreaterThan(requestsBeforeFocus);
  await expect(page.locator('.home-status-strip').getByText('8', { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/$/);

  await expect(page.getByRole('button', { name: '更新首頁狀態' })).toBeEnabled();
  dueCount = 9;
  const requestsBeforeVisibility = planRequests;
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect.poll(() => planRequests).toBeGreaterThan(requestsBeforeVisibility);
  await expect(page.locator('.home-status-strip').getByText('9', { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/$/);
});

test('home refreshes when the next review becomes due', async ({ page }) => {
  let dueCount = 0;
  let planRequests = 0;
  const nextDue = new Date(Date.now() + 1500).toISOString();
  await page.route('**/api/v1/study-sessions/plan', async route => {
    planRequests += 1;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(studyPlan(dueCount, dueCount === 0 ? nextDue : null)) });
  });

  await page.goto('/');
  await page.waitForLoadState('networkidle');
  const requestsBeforeDue = planRequests;
  dueCount = 4;
  await expect.poll(() => planRequests, { timeout: 5000 }).toBeGreaterThan(requestsBeforeDue);
  await expect(page.locator('.home-status-strip').getByText('4', { exact: true })).toBeVisible();
  await expect(page).toHaveURL(/\/$/);
});
