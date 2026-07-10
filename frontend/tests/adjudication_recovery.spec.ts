import { expect, test } from '@playwright/test';


const processingStatus = {
  session_id: 'recovery-session',
  pending: 0,
  processing: 1,
  succeeded: 0,
  failed: 0,
  total: 1,
  results: [{
    id: 'answer-recovery',
    session_item_id: 'item-recovery',
    card_id: 'card-recovery',
    english: 'allocate',
    part_of_speech: 'verb',
    typed_answer: '分配',
    expected_answer: '分配',
    status: 'processing',
    verdict: null,
    rating: null,
    reason: null,
  }],
};

const completedStatus = {
  session_id: 'recovery-session',
  pending: 0,
  processing: 0,
  succeeded: 1,
  failed: 0,
  total: 1,
  results: [{
    ...processingStatus.results[0],
    status: 'succeeded',
    verdict: 'correct',
    rating: 'Good',
    reason: '語意正確',
  }],
};

const failedStatus = {
  ...processingStatus,
  processing: 0,
  failed: 1,
  results: [{
    ...processingStatus.results[0],
    status: 'failed',
    error_message: 'temporary failure',
  }],
};

const pendingStatus = {
  ...processingStatus,
  pending: 1,
  processing: 0,
  results: [{
    ...processingStatus.results[0],
    status: 'pending',
  }],
};


test('summary probes processing work once on load and once after reload', async ({ page }) => {
  let statusRequests = 0;
  let adjudicateRequests = 0;
  let completed = false;

  await page.route('**/api/v1/study-sessions/recovery-session/adjudication-status', async route => {
    statusRequests += 1;
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(completed ? completedStatus : processingStatus),
    });
  });
  await page.route('**/api/v1/study-sessions/recovery-session/adjudicate', async route => {
    adjudicateRequests += 1;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(processingStatus) });
  });

  await page.addInitScript(() => {
    const now = Date.now();
    sessionStorage.setItem('study_summary_typed_session_id', 'stale-session');
    sessionStorage.setItem('study_summary_typed_session_id_stored_at', String(now - 1000));
    localStorage.setItem('study_summary_typed_session_id', 'recovery-session');
    localStorage.setItem('study_summary_typed_session_id_stored_at', String(now));
  });
  await page.goto('/study/summary');

  await expect(page.getByText('LLM 批改中')).toBeVisible();
  await expect.poll(() => adjudicateRequests).toBe(1);
  await page.waitForTimeout(1800);
  expect(adjudicateRequests).toBe(1);

  await page.reload();
  await expect(page.getByText('LLM 批改中')).toBeVisible();
  await expect.poll(() => adjudicateRequests).toBe(2);

  completed = true;
  await expect(page.getByText('LLM 批改完成')).toBeVisible({ timeout: 5000 });
  await expect(page.getByText('allocate')).toBeVisible();
  expect(adjudicateRequests).toBe(2);
  expect(statusRequests).toBeGreaterThanOrEqual(3);
});

test('summary retries a pending claim after the initial POST is interrupted', async ({ page }) => {
  let phase: 'pending' | 'processing' | 'completed' = 'pending';
  let adjudicateRequests = 0;
  let processingReads = 0;

  await page.route('**/api/v1/study-sessions/recovery-session/adjudication-status', async route => {
    if (phase === 'processing') {
      processingReads += 1;
      if (processingReads >= 2) phase = 'completed';
    }
    const response = phase === 'pending'
      ? pendingStatus
      : phase === 'processing'
        ? processingStatus
        : completedStatus;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(response) });
  });
  await page.route('**/api/v1/study-sessions/recovery-session/adjudicate', async route => {
    adjudicateRequests += 1;
    if (adjudicateRequests === 1) {
      await route.abort('connectionfailed');
      return;
    }
    phase = 'processing';
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(processingStatus) });
  });

  await page.addInitScript(() => {
    localStorage.setItem('study_summary_typed_session_id', 'recovery-session');
    sessionStorage.removeItem('study_summary_typed_session_id');
  });
  await page.goto('/study/summary');

  await expect(page.getByText('LLM 批改中')).toBeVisible();
  await expect(page.getByText('LLM 批改完成')).toBeVisible({ timeout: 8000 });
  expect(adjudicateRequests).toBe(2);
});

test('summary probes processing work once again when the page becomes visible', async ({ page }) => {
  let adjudicateRequests = 0;

  await page.route('**/api/v1/study-sessions/recovery-session/adjudication-status', async route => {
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(processingStatus) });
  });
  await page.route('**/api/v1/study-sessions/recovery-session/adjudicate', async route => {
    adjudicateRequests += 1;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(processingStatus) });
  });
  await page.addInitScript(() => {
    localStorage.setItem('study_summary_typed_session_id', 'recovery-session');
    sessionStorage.removeItem('study_summary_typed_session_id');
  });
  await page.goto('/study/summary');
  await expect(page.getByText('LLM 批改中')).toBeVisible();
  await expect.poll(() => adjudicateRequests).toBe(1);
  await page.waitForTimeout(1800);
  expect(adjudicateRequests).toBe(1);

  await page.evaluate(() => {
    document.dispatchEvent(new Event('visibilitychange'));
  });

  await expect.poll(() => adjudicateRequests, { timeout: 5000 }).toBe(2);
  await page.waitForTimeout(1800);
  expect(adjudicateRequests).toBe(2);
});

test('summary periodically probes processing work that becomes stale while visible', async ({ page }) => {
  let adjudicateRequests = 0;

  await page.route('**/api/v1/study-sessions/recovery-session/adjudication-status', route =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify(processingStatus) }));
  await page.route('**/api/v1/study-sessions/recovery-session/adjudicate', async route => {
    adjudicateRequests += 1;
    await route.fulfill({
      contentType: 'application/json',
      body: JSON.stringify(adjudicateRequests === 1 ? processingStatus : completedStatus),
    });
  });
  await page.addInitScript(() => {
    localStorage.setItem('study_summary_typed_session_id', 'recovery-session');
  });
  await page.goto('/study/summary');
  await expect(page.getByText('LLM 批改中')).toBeVisible();
  await expect.poll(() => adjudicateRequests).toBe(1);

  await page.evaluate(() => {
    const afterProbeInterval = Date.now() + 61 * 1000;
    Date.now = () => afterProbeInterval;
  });

  await expect.poll(() => adjudicateRequests, { timeout: 5000 }).toBe(2);
  await expect(page.getByText('LLM 批改完成')).toBeVisible();
  await page.waitForTimeout(1800);
  expect(adjudicateRequests).toBe(2);
});

test('failed manual retry becomes actionable again when the POST is interrupted', async ({ page }) => {
  let statusRequests = 0;
  let retryRequests = 0;

  await page.route('**/api/v1/study-sessions/recovery-session/adjudication-status', async route => {
    statusRequests += 1;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(failedStatus) });
  });
  await page.route('**/api/v1/study-sessions/recovery-session/adjudication-retry', async route => {
    retryRequests += 1;
    await route.abort('connectionfailed');
  });
  await page.addInitScript(() => {
    sessionStorage.setItem('study_summary_typed_session_id', 'recovery-session');
  });
  await page.goto('/study/summary');

  const retryButton = page.getByRole('button', { name: '重新批改' });
  await expect(retryButton).toBeVisible();
  await retryButton.click();

  await expect.poll(() => retryRequests).toBe(1);
  await expect(retryButton).toBeVisible({ timeout: 5000 });
  await expect.poll(() => statusRequests).toBeGreaterThanOrEqual(2);
});

test('manual retry keeps polling when the server accepts the claim but the response is lost', async ({ page }) => {
  let phase: 'failed' | 'processing' | 'completed' = 'failed';
  let processingReads = 0;
  let statusRequests = 0;
  let delayNextStatus = false;

  await page.route('**/api/v1/study-sessions/recovery-session/adjudication-status', async route => {
    statusRequests += 1;
    const requestedPhase = phase;
    if (delayNextStatus) {
      delayNextStatus = false;
      await new Promise(resolve => setTimeout(resolve, 300));
    }
    if (phase === 'processing') {
      processingReads += 1;
      if (processingReads >= 2) phase = 'completed';
    }
    const responsePhase = requestedPhase === 'failed' ? requestedPhase : phase;
    const response = responsePhase === 'failed'
      ? failedStatus
      : responsePhase === 'processing'
        ? processingStatus
        : completedStatus;
    await route.fulfill({ contentType: 'application/json', body: JSON.stringify(response) });
  });
  await page.route('**/api/v1/study-sessions/recovery-session/adjudication-retry', async route => {
    phase = 'processing';
    await route.abort('connectionfailed');
  });
  await page.route('**/api/v1/study-sessions/recovery-session/adjudicate', route =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify(processingStatus) }));
  await page.addInitScript(() => {
    sessionStorage.setItem('study_summary_typed_session_id', 'recovery-session');
  });
  await page.goto('/study/summary');

  delayNextStatus = true;
  await page.evaluate(() => document.dispatchEvent(new Event('visibilitychange')));
  await expect.poll(() => statusRequests).toBeGreaterThanOrEqual(2);
  await page.getByRole('button', { name: '重新批改' }).click();
  await expect(page.getByText('LLM 批改完成')).toBeVisible({ timeout: 5000 });
  expect(processingReads).toBeGreaterThanOrEqual(2);
});

test('summary reads sessionStorage when localStorage reads are unavailable', async ({ page }) => {
  await page.route('**/api/v1/study-sessions/recovery-session/adjudication-status', route =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify(completedStatus) }));
  await page.addInitScript(() => {
    sessionStorage.setItem('study_summary_typed_session_id', 'recovery-session');
    sessionStorage.setItem('study_summary_typed_session_id_stored_at', String(Date.now()));
    const originalGetItem = Storage.prototype.getItem;
    Storage.prototype.getItem = function getItem(key: string) {
      if (this === window.localStorage) throw new DOMException('blocked', 'SecurityError');
      return originalGetItem.call(this, key);
    };
  });

  await page.goto('/study/summary');
  await expect(page.getByText('LLM 批改完成')).toBeVisible();
});

test('summary storage failure does not block navigation after a session completes', async ({ page }) => {
  await page.route('**/api/v1/notification-settings', route => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({ minimum_due_count: 10, discord_configured: false }),
  }));
  await page.route('**/api/v1/placement-sessions/active', route =>
    route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not found' }) }));
  await page.route('**/api/v1/study-sessions/active', route =>
    route.fulfill({ status: 404, contentType: 'application/json', body: JSON.stringify({ detail: 'not found' }) }));
  await page.route('**/api/v1/study-sessions/plan', route => route.fulfill({
    contentType: 'application/json',
    body: JSON.stringify({
      started: true,
      remaining_days: 30,
      remaining_new_cards: 0,
      suggested_new_cards_today: 0,
      pending_new_count: 0,
      due_count: 0,
      next_due: null,
      availability_state: 'blocked_pending_adjudication',
      pending_adjudication_count: 1,
      pending_adjudication_session_id: 'recovery-session',
      placement_status: {
        status: 'complete',
        complete: true,
        total_eligible_count: 1,
        remaining_count: 0,
        active_session_id: null,
        active_session_status: null,
      },
    }),
  }));
  await page.route('**/api/v1/study-sessions/recovery-session/adjudication-status', route =>
    route.fulfill({ contentType: 'application/json', body: JSON.stringify(completedStatus) }));
  await page.goto('/');
  const summaryButton = page.getByRole('button', { name: '查看結果' });
  await expect(summaryButton).toBeVisible();
  await page.evaluate(() => {
    const originalSetItem = Storage.prototype.setItem;
    Storage.prototype.setItem = function setItem(key: string, value: string) {
      if (this === window.localStorage) throw new DOMException('blocked', 'QuotaExceededError');
      return originalSetItem.call(this, key, value);
    };
  });

  await summaryButton.click();
  await expect(page).toHaveURL(/\/study\/summary$/);
  await expect(page.getByText('LLM 批改完成')).toBeVisible();
  expect(await page.evaluate(() => {
    const raw = sessionStorage.getItem('study_summary_typed_session_id_record');
    return raw ? JSON.parse(raw).id : null;
  })).toBe('recovery-session');
});

test('expired session cleanup tolerates an unavailable storage', async ({ page }) => {
  await page.addInitScript(() => {
    const expiredAt = String(Date.now() - 25 * 60 * 60 * 1000);
    for (const storage of [sessionStorage, localStorage]) {
      storage.setItem('study_summary_typed_session_id', 'expired-session');
      storage.setItem('study_summary_typed_session_id_stored_at', expiredAt);
    }
    const originalRemoveItem = Storage.prototype.removeItem;
    Storage.prototype.removeItem = function removeItem(key: string) {
      if (this === window.localStorage) throw new DOMException('blocked', 'SecurityError');
      return originalRemoveItem.call(this, key);
    };
  });

  await page.goto('/study/summary');
  await expect(page.getByText('沒有可批改的複習紀錄')).toBeVisible();
  expect(await page.evaluate(() => sessionStorage.getItem('study_summary_typed_session_id'))).toBeNull();
});
