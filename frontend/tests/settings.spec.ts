import { expect, test } from '@playwright/test';

const llmSettings = {
  provider: 'openrouter',
  model: 'openai/gpt-4.1-mini',
  base_url: null,
  timeout_seconds: 45,
  api_key_configured: true,
  api_key_source: 'local',
  effective_model: 'openai/gpt-4.1-mini',
  fallback_routes: [
    { provider: 'openrouter', model: 'google/gemini-2.5-flash' },
    { provider: 'openrouter', model: 'anthropic/claude-3.5-haiku' },
    { provider: 'openrouter', model: 'qwen/qwen3-30b-a3b' },
  ],
  batch_size: 10,
  max_concurrency: 2,
  effective_route_chain: [
    { provider: 'openrouter', model: 'openai/gpt-4.1-mini' },
    { provider: 'openrouter', model: 'google/gemini-2.5-flash' },
    { provider: 'openrouter', model: 'anthropic/claude-3.5-haiku' },
    { provider: 'openrouter', model: 'qwen/qwen3-30b-a3b' },
  ],
  provider_readiness: [
    { provider: 'openrouter', api_key_configured: true, api_key_source: 'local', effective_model: 'openrouter/default', fallback_available: false },
    { provider: 'gemini', api_key_configured: true, api_key_source: 'environment', effective_model: 'gemini/default', fallback_available: true },
    { provider: 'openai_compatible', api_key_configured: false, api_key_source: 'none', effective_model: 'compatible/default', fallback_available: false },
  ],
};

test.beforeEach(async ({ page }) => {
  await page.route('**/api/v1/llm-settings', async route => {
    if (route.request().method() === 'GET') {
      await route.fulfill({ contentType: 'application/json', body: JSON.stringify(llmSettings) });
      return;
    }
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(llmSettings) });
  });
  await page.route('**/api/v1/notification-settings', async route => {
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify({ minimum_due_count: 10, discord_configured: true }),
    });
  });
});

test('settings supports ordered models and uses consistent notification actions', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto('/');
  await page.waitForLoadState('networkidle');

  const llmPanel = page.locator('.home-llm-panel');
  const routes = llmPanel.locator('.home-llm-route');
  await expect(routes).toHaveCount(3);
  await expect(llmPanel.locator('.home-llm-current')).toHaveCount(0);

  await llmPanel.getByRole('button', { name: '將備援模型 2 上移' }).click();
  await expect(routes.nth(0).locator('input')).toHaveValue('anthropic/claude-3.5-haiku');

  const notificationPanel = page.locator('.home-notification-panel');
  await expect(notificationPanel).toContainText('待複習題數達到');
  await expect(notificationPanel.locator('.material-symbol')).toHaveCount(0);
  await expect(notificationPanel.locator('.home-notification-control').getByRole('button', { name: '儲存' })).toBeVisible();
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
});

test('settings rejects the primary model as a fallback and clears a draft key on provider change', async ({ page }) => {
  let updateRequests = 0;
  await page.route('**/api/v1/llm-settings', async route => {
    if (route.request().method() === 'PUT') updateRequests += 1;
    await route.fallback();
  });

  await page.goto('/');
  await page.waitForLoadState('networkidle');
  const llmPanel = page.locator('.home-llm-panel');

  await llmPanel.locator('.home-llm-route').first().locator('input').fill('openai/gpt-4.1-mini');
  await llmPanel.getByRole('button', { name: '儲存', exact: true }).click();
  await expect(page.getByText('LLM 設定儲存失敗：備援模型不能和主要模型相同')).toBeVisible();
  expect(updateRequests).toBe(0);

  await llmPanel.locator('details summary').click();
  const apiKey = llmPanel.locator('input[type="password"]');
  await apiKey.fill('unsaved-openrouter-key');
  await llmPanel.getByLabel('主要服務').selectOption('gemini');
  await expect(apiKey).toHaveValue('');
});

test('switching OpenRouter to Auto preserves its local key without a redundant route summary', async ({ page }) => {
  let updatePayload: Record<string, unknown> | null = null;
  await page.route('**/api/v1/llm-settings', async route => {
    if (route.request().method() === 'PUT') {
      updatePayload = route.request().postDataJSON() as Record<string, unknown>;
    }
    await route.fallback();
  });

  await page.goto('/');
  await page.waitForLoadState('networkidle');
  const llmPanel = page.locator('.home-llm-panel');
  await llmPanel.getByLabel('主要服務').selectOption('auto');

  await expect(llmPanel.locator('.home-llm-current')).toHaveCount(0);
  await expect(llmPanel.getByText('依可用服務使用各自預設模型')).toBeVisible();

  await llmPanel.getByRole('button', { name: '儲存', exact: true }).click();
  await expect.poll(() => updatePayload).not.toBeNull();
  expect(updatePayload?.clear_api_key).toBe(false);
});
