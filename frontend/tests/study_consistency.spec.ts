import { expect, test, type Page } from '@playwright/test';

const studySession = {
  id: 'consistency-study',
  requested_size: 2,
  mode: 'fixed',
  sync_status: 'active',
  started_at: '2026-06-27T00:00:00Z',
  cards_answered: 0,
  again_count: 0,
  hard_count: 0,
  good_count: 0,
};

const studyItems = [
  {
    id: 'study-item-1',
    position: 0,
    target_card_id: 'study-card-1',
    source_type: 'due',
    answered_at: null,
    idempotency_key: null,
    sync_status: null,
    card: {
      id: 'study-card-1',
      english: 'first',
      chinese_meaning: '第一',
      part_of_speech: 'adjective',
    },
  },
  {
    id: 'study-item-2',
    position: 1,
    target_card_id: 'study-card-2',
    source_type: 'due',
    answered_at: null,
    idempotency_key: null,
    sync_status: null,
    card: {
      id: 'study-card-2',
      english: 'second',
      chinese_meaning: '第二',
      part_of_speech: 'adjective',
    },
  },
];

async function clearLocalDatabase(page: Page) {
  await page.goto('/');
  await page.evaluate(async () => {
    const database = (window as typeof window & { db: any }).db;
    await Promise.all(database.tables.map((table: any) => table.clear()));
    sessionStorage.clear();
  });
}

async function seedLocalStudy(
  page: Page,
  options: { answered?: boolean; requestedSize?: number } = {},
) {
  const answered = options.answered ?? false;
  const requestedSize = options.requestedSize ?? 2;
  await clearLocalDatabase(page);
  await page.evaluate(
    async ({ session, items, hasAnswer, size }) => {
      const database = (window as typeof window & { db: any }).db;
      await database.study_sessions.put({
        id: session.id,
        requestedSize: size,
        mode: 'fixed',
        status: 'active',
        manifest: items.slice(0, size).map(item => ({
          id: item.id,
          position: item.position,
          cardId: item.target_card_id,
          sourceType: item.source_type,
        })),
        startedAt: session.started_at,
        updatedAt: session.started_at,
        cardsAnswered: hasAnswer ? 1 : 0,
        againCount: 0,
        hardCount: 0,
        goodCount: 0,
      });
      await database.placement_cards.bulkPut(
        items.slice(0, size).map(item => ({
          id: item.card.id,
          english: item.card.english,
          chineseMeaning: item.card.chinese_meaning,
          partOfSpeech: item.card.part_of_speech,
        })),
      );
      if (hasAnswer) {
        await database.study_items.put({
          id: items[0].id,
          studySessionId: session.id,
          position: 0,
          cardId: items[0].target_card_id,
          result: 'Pending',
          idempotencyKey: 'existing-answer',
          answeredAt: '2026-06-27T00:01:00Z',
          typedAnswer: '伺服器答案',
          adjudicationStatus: 'pending',
          correctOptionCardId: items[0].target_card_id,
        });
      }
      sessionStorage.setItem('study_resume', '1');
      sessionStorage.setItem('study_count', String(size));
      sessionStorage.setItem('study_mode', 'fixed');
    },
    { session: studySession, items: studyItems, hasAnswer: answered, size: requestedSize },
  );
}

test.describe('Study state consistency', () => {
  test('home hydrates an active server study session after IndexedDB is cleared', async ({ page }) => {
    await page.route('**/api/v1/placement-sessions/active', route =>
      route.fulfill({ status: 404, json: { detail: 'not found' } }));
    await page.route('**/api/v1/study-sessions/active', route =>
      route.fulfill({ json: studySession }));
    await page.route('**/api/v1/study-sessions/consistency-study/items', route =>
      route.fulfill({ json: studyItems }));
    await page.route('**/api/v1/study-sessions/plan', route =>
      route.fulfill({
        json: {
          started: true,
          remaining_days: 30,
          remaining_new_cards: 0,
          suggested_new_cards_today: 0,
          due_count: 0,
          next_due: null,
          placement_status: {
            status: 'complete',
            complete: true,
            total_eligible_count: 2,
            remaining_count: 0,
            active_session_id: null,
            active_session_status: null,
          },
        },
      }));

    await clearLocalDatabase(page);
    await page.evaluate(async () => {
      const database = (window as typeof window & { db: any }).db;
      await database.study_items.put({
        id: 'study-item-1',
        studySessionId: 'consistency-study',
        position: 0,
        cardId: 'study-card-1',
        result: 'Pending',
        idempotencyKey: 'stale-local-answer',
        answeredAt: '2026-06-27T00:01:00Z',
        typedAnswer: '過期本機答案',
        adjudicationStatus: 'pending',
        correctOptionCardId: 'study-card-1',
      });
    });
    await page.reload();

    await expect(page.getByRole('button', { name: '繼續複習' })).toBeVisible();
    await expect(page.getByText('第 0 / 2 題')).toBeVisible();
    const cached = await page.evaluate(async () =>
      (window as typeof window & { db: any }).db.study_sessions.get('consistency-study'));
    expect(cached?.status).toBe('active');
    const staleAnswer = await page.evaluate(async () =>
      (window as typeof window & { db: any }).db.study_items.get('study-item-1'));
    expect(staleAnswer).toBeUndefined();
  });

  test('item conflict refreshes server state instead of overwriting Dexie', async ({ page }) => {
    const serverItems = [
      {
        ...studyItems[0],
        answered_at: '2026-06-27T00:02:00Z',
        idempotency_key: 'answer-from-other-tab',
        sync_status: 'pending_adjudication',
      },
      studyItems[1],
    ];
    await page.route('**/api/v1/study-sessions/consistency-study/typed-answers/batch', async route => {
      const request = route.request().postDataJSON();
      const key = request.answers[0].idempotency_key;
      await route.fulfill({
        json: { accepted: [], duplicates: [], conflicts: [key] },
      });
    });
    await page.route('**/api/v1/study-sessions/consistency-study', route =>
      route.fulfill({
        json: { ...studySession, cards_answered: 1 },
      }));
    await page.route('**/api/v1/study-sessions/consistency-study/items', route =>
      route.fulfill({ json: serverItems }));

    await seedLocalStudy(page);
    await page.goto('/study');
    await expect(page.locator('#study-term')).toHaveText('first');
    await page.locator('#typed-answer').fill('本分頁答案');
    await page.locator('#typed-submit').click();
    await page.locator('#typed-next').click();

    await expect(page.locator('#study-term')).toHaveText('second');
    const cachedAnswer = await page.evaluate(async () =>
      (window as typeof window & { db: any }).db.study_items.get('study-item-1'));
    expect(cachedAnswer.idempotencyKey).toBe('answer-from-other-tab');
    expect(cachedAnswer.typedAnswer).toBeUndefined();
  });

  test('failed finish keeps the local session active and offers retry', async ({ page }) => {
    await page.route('**/api/v1/study-sessions/consistency-study/finish', route =>
      route.fulfill({ status: 503, json: { detail: 'offline' } }));

    await seedLocalStudy(page, { answered: true, requestedSize: 1 });
    await page.goto('/study');

    await expect(page.getByRole('heading', { name: '尚未完成本輪' })).toBeVisible();
    await expect(page.getByRole('button', { name: '重試完成本輪' })).toBeVisible();
    const cached = await page.evaluate(async () =>
      (window as typeof window & { db: any }).db.study_sessions.get('consistency-study'));
    expect(cached.status).toBe('active');
  });

  test('failed abandon keeps the local session active and offers retry', async ({ page }) => {
    await page.route('**/api/v1/study-sessions/consistency-study/abandon', route =>
      route.fulfill({ status: 503, json: { detail: 'offline' } }));

    await seedLocalStudy(page);
    await page.goto('/study');
    await expect(page.locator('#study-term')).toBeVisible();
    await page.getByRole('button', { name: '結束學習，回首頁' }).click();
    await page.locator('#abandon-session-btn').click();
    await page.locator('#abandon-session-btn').click();

    await expect(page.getByRole('heading', { name: '尚未放棄本輪' })).toBeVisible();
    await expect(page.getByRole('button', { name: '重試放棄本輪' })).toBeVisible();
    const cached = await page.evaluate(async () =>
      (window as typeof window & { db: any }).db.study_sessions.get('consistency-study'));
    expect(cached.status).toBe('active');
  });

  test('offline completed audit waits for sync instead of inventing a second sample', async ({ page }) => {
    await page.route('**/api/v1/placement-sessions/active', route =>
      route.fulfill({
        json: {
          id: 'offline-placement',
          requested_count: 200,
          status: 'checkpoint_pending',
          manifest_json: '[]',
          started_at: '2026-06-27T00:00:00Z',
          current_position: 100,
          checkpoint_size: 100,
        },
      }));
    await page.route('**/api/v1/placement-sessions/offline-placement/audit/100', route =>
      route.fulfill({ status: 503, json: { detail: 'offline' } }));

    await clearLocalDatabase(page);
    await page.evaluate(async () => {
      const database = (window as typeof window & { db: any }).db;
      await database.placement_audits.put({
        id: 'offline-placement_100',
        sessionId: 'offline-placement',
        checkpoint: 100,
        status: 'active',
        createdAt: '2026-06-27T00:00:00Z',
      });
      await database.placement_cards.put({
        id: 'audit-card',
        english: 'audit',
        chineseMeaning: '稽核',
      });
      await database.placement_audit_items.put({
        id: 'audit-item',
        placementAuditId: 'offline-placement_100',
        cardId: 'audit-card',
        sampleBatch: 1,
        optionsJson: JSON.stringify([
          { card_id: 'audit-card', chinese: '稽核' },
          { card_id: 'wrong-card', chinese: '錯誤' },
        ]),
        correctOptionId: 'audit-card',
        userSelectedOptionId: 'wrong-card',
        resolvedResult: 'incorrect',
      });
    });

    await page.goto('/placement/checkpoint');

    await expect(page.getByRole('heading', { name: '等待同步抽查結果' })).toBeVisible();
    await expect(page.getByText('二次抽查', { exact: true })).toHaveCount(0);
    await expect(page.getByRole('button', { name: '重新同步' })).toBeVisible();
  });
});
