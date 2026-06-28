import { expect, test } from '@playwright/test';

test('built frontend and API are served by one process', async ({ page, request }) => {
  const health = await request.get('/api/v1/health');
  expect(health.ok()).toBeTruthy();
  await expect(health.json()).resolves.toEqual({ status: 'ok' });

  await page.goto('/');
  await expect(page.locator('#root')).not.toBeEmpty();

  const assetFailures: string[] = [];
  page.on('response', (response) => {
    if (response.url().includes('/assets/') && response.status() >= 400) {
      assetFailures.push(`${response.status()} ${response.url()}`);
    }
  });

  await page.goto('/mistakes');
  await expect(page.getByRole('heading', { name: '數據分析與管理' })).toBeVisible();
  expect(assetFailures).toEqual([]);

  const missingPost = await request.post('/not-an-api-route');
  expect(missingPost.status()).toBe(404);
});
