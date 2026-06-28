import { test, expect, chromium } from '@playwright/test';
import { spawn, execSync, ChildProcess } from 'child_process';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname_compat = path.dirname(__filename);

const TEMP_DB_PATH = path.join(__dirname_compat, '../../backend/data/temp_block_test.db');
const ABS_DB_URL = 'sqlite+aiosqlite:///' + TEMP_DB_PATH;
const USER_DATA_DIR = path.join(__dirname_compat, '../temp-block-profile');
const BACKEND_URL = 'http://127.0.0.1:8003';
const FRONTEND_URL = 'http://127.0.0.1:5175';

function apiUrl(pathname: string) {
  return `${BACKEND_URL}${pathname}`;
}

function appUrl(pathname: string) {
  return `${FRONTEND_URL}${pathname}`;
}

async function backendFetch(pathname: string, init?: RequestInit) {
  return fetch(apiUrl(pathname), {
    ...init,
    signal: AbortSignal.timeout(15000),
  });
}

let uvicornProcess: ChildProcess;
let viteProcess: ChildProcess;

test.beforeEach(async () => {
  if (fs.existsSync(TEMP_DB_PATH)) {
    try { fs.unlinkSync(TEMP_DB_PATH); } catch {}
  }
  if (fs.existsSync(USER_DATA_DIR)) {
    try { fs.rmSync(USER_DATA_DIR, { recursive: true, force: true }); } catch {}
  }

  // Seed standard database with words using the large seeder
  try {
    execSync(`cd ../backend && PYTHONPATH=. DATABASE_URL=${ABS_DB_URL} uv run python -m app.seed_large`, {
      stdio: 'inherit',
      env: { ...process.env, VOCAB_ENV: 'development' }
    });
  } catch (err) {
    console.error('Failed to seed testing database:', err);
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

async function waitActivePlacement() {
  for (let i = 0; i < 50; i++) {
    const session = await backendFetch('/api/v1/placement-sessions/active')
      .then(res => res.ok ? res.json() : null)
      .catch(() => null);
    if (session?.id) return session;
    await new Promise(r => setTimeout(r, 200));
  }
  throw new Error('Timed out waiting for active placement session');
}

async function answerFirstCheckpointBatch(session: any, prefix: string) {
  const manifest = JSON.parse(session.manifest_json);
  const events = manifest.slice(0, 100).map((item: any, index: number) => ({
    idempotency_key: `${prefix}-known-${index}`,
    event_type: 'answer',
    position: item.position,
    card_id: item.card_id,
    result: 'known',
    answered_at: new Date(Date.UTC(2024, 0, 4, 0, index, 0)).toISOString(),
  }));
  const res = await backendFetch(`/api/v1/placement-sessions/${session.id}/events/batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ events }),
  });
  if (!res.ok) throw new Error(await res.text());
}

test.describe('Placement Progress Blocking Reviews', () => {
  test('Rapid double-click answers only one placement card locally', async () => {
    test.setTimeout(60000);
    const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
      viewport: { width: 390, height: 844 },
    });
    const page = context.pages[0] || await context.newPage();

    await page.goto(appUrl('/'));
    await page.waitForLoadState('networkidle');
    await page.locator('#placement-btn-100').click();
    await page.waitForURL(/\/placement/);
    await expect(page.locator('#placement-term')).toBeVisible();

    await page.locator('#btn-known').dblclick();
    await page.waitForTimeout(500);

    await expect(page.locator('#placement-progress')).toContainText('1');
    const itemCount = await page.evaluate(async () => {
      const w = window as any;
      return w.db.placement_items.count();
    });
    const eventCount = await page.evaluate(async () => {
      const w = window as any;
      return w.db.pending_events.where('type').equals('placement').count();
    });
    expect(itemCount).toBe(1);
    expect(eventCount).toBeLessThanOrEqual(1);

    await context.close();
  });

  test('Cannot review before completing placement, even after checkpoint or clearing IndexedDB', async () => {
    test.setTimeout(90000);
    const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
      viewport: { width: 390, height: 844 },
    });
    const page = context.pages[0] || await context.newPage();

    // 1. Start placement session of 250 cards
    await page.goto(appUrl('/'));
    await page.waitForLoadState('networkidle');
    await page.locator('#placement-btn-250').click();
    await page.waitForURL(/\/placement/);
    await expect(page.locator('#placement-term')).toBeVisible();

    // 2. Move the server session to the first checkpoint without spending the test on 100 UI clicks.
    const activePlacement = await waitActivePlacement();
    const manifest = JSON.parse(activePlacement.manifest_json);
    const events = manifest.slice(0, 100).map((item: any, index: number) => ({
      idempotency_key: `checkpoint-known-${index}`,
      event_type: 'answer',
      position: item.position,
      card_id: item.card_id,
      result: 'known',
      answered_at: new Date(Date.UTC(2024, 0, 1, 0, index, 0)).toISOString(),
    }));
    {
      const res = await backendFetch(`/api/v1/placement-sessions/${activePlacement.id}/events/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ events }),
      });
      if (!res.ok) throw new Error(await res.text());
    }

    // 3. Return home after the server-side checkpoint is pending.
    await page.goto(appUrl('/'));
    await page.waitForLoadState('networkidle');

    // 4. Verify main action card is "繼續上次盤點" and study options are hidden
    await expect(page.locator('#home-primary-resume-placement')).toBeVisible();
    await expect(page.locator('#study-btn-25')).toBeHidden();

    await page.locator('#home-primary-resume-placement').click();
    await page.waitForURL(/\/placement\/checkpoint/);
    await expect(page.locator('#audit-term')).toBeVisible({ timeout: 15000 });
    await expect(page.locator('text=開始抽查')).toBeHidden();

    await page.goto(appUrl('/'));
    await page.waitForLoadState('networkidle');

    // 5. Try manually navigating to /study (URL Bypassing)
    await page.goto(appUrl('/study'));
    
    // Page should display empty/warning state or redirect back
    await expect(page.locator('text=正式複習需要先完成盤點')).toBeVisible();

    // 6. Simulate IndexedDB clearing / multi-device desync
    await page.goto(appUrl('/'));
    await page.evaluate(async () => {
      const w = window as any;
      await w.db.placement_sessions.clear();
      await w.db.placement_items.clear();
      await w.db.placement_cards.clear();
    });

    // Reload page
    await page.reload();
    await page.waitForLoadState('networkidle');

    // Home Page main action card should query server and still enforce placement blocking
    await expect(page.locator('#home-primary-resume-placement')).toBeVisible();
    await expect(page.locator('#study-btn-25')).toBeHidden();

    await context.close();
  });

  test('Checkpoint resume loads audit, and abandoning the checkpoint does not unlock review', async () => {
    test.setTimeout(90000);
    const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
      viewport: { width: 390, height: 844 },
    });
    const page = context.pages[0] || await context.newPage();

    await page.goto(appUrl('/'));
    await page.waitForLoadState('networkidle');
    await page.locator('#placement-btn-250').click();
    await page.waitForURL(/\/placement/);
    await expect(page.locator('#placement-term')).toBeVisible();

    const activePlacement = await waitActivePlacement();
    const manifest = JSON.parse(activePlacement.manifest_json);
    const events = manifest.slice(0, 100).map((item: any, index: number) => ({
      idempotency_key: `checkpoint-abandon-known-${index}`,
      event_type: 'answer',
      position: item.position,
      card_id: item.card_id,
      result: 'known',
      answered_at: new Date(Date.UTC(2024, 0, 2, 0, index, 0)).toISOString(),
    }));
    {
      const res = await backendFetch(`/api/v1/placement-sessions/${activePlacement.id}/events/batch`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ events }),
      });
      if (!res.ok) throw new Error(await res.text());
    }
    const audit = await backendFetch(`/api/v1/placement-sessions/${activePlacement.id}/audit/100`)
      .then(res => res.json());
    expect(audit.questions.length).toBeGreaterThan(0);

    // This is the regression path: server is at checkpoint, but the browser's IndexedDB
    // does not have the 100 placement items. The checkpoint page must still load checkpoint 100.
    await page.goto(appUrl('/placement/checkpoint'));
    await expect(page.locator('#audit-term')).toBeVisible({ timeout: 15000 });
    await expect(page.locator('#start-audit')).toBeHidden();

    const abandonRes = await backendFetch(`/api/v1/placement-sessions/${activePlacement.id}/abandon`, {
      method: 'POST',
    });
    if (!abandonRes.ok) throw new Error(await abandonRes.text());

    await page.goto(appUrl('/'));
    await page.waitForLoadState('networkidle');

    await expect(page.locator('#study-btn-25')).toBeHidden();
    await expect(page.locator('#home-primary-placement')).toBeVisible();

    await page.goto(appUrl('/study'));
    await expect(page.locator('text=正式複習需要先完成盤點')).toBeVisible();

    await context.close();
  });

  test('After checkpoint audit is complete, resting on home resumes the next placement card', async () => {
    test.setTimeout(90000);
    const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
      viewport: { width: 390, height: 844 },
    });
    const page = context.pages[0] || await context.newPage();

    await page.goto(appUrl('/'));
    await page.waitForLoadState('networkidle');
    await page.locator('#placement-btn-250').click();
    await page.waitForURL(/\/placement/);
    await expect(page.locator('#placement-term')).toBeVisible();

    const activePlacement = await waitActivePlacement();
    await answerFirstCheckpointBatch(activePlacement, 'checkpoint-rest');
    const manifest = JSON.parse(activePlacement.manifest_json);
    const expectedNextCardId = manifest[100].card_id;
    const secondChunk = await backendFetch(`/api/v1/placement-sessions/${activePlacement.id}/chunks/1`)
      .then(res => res.json());
    const expectedNextCard = secondChunk.find((card: any) => card.id === expectedNextCardId);
    expect(expectedNextCard).toBeTruthy();
    const audit = await backendFetch(`/api/v1/placement-sessions/${activePlacement.id}/audit/100`)
      .then(res => res.json());
    expect(audit.questions.length).toBeGreaterThan(0);

    await page.goto(appUrl('/placement/checkpoint'));
    await expect(page.locator('#audit-term')).toBeVisible({ timeout: 15000 });
    await expect(page.locator('#start-audit')).toBeHidden();
    await expect(page.locator('text=開始抽查')).toBeHidden();
    await expect(page.locator('text=快速抽查')).toBeHidden();

    for (let i = 0; i < audit.questions.length; i++) {
      const q = audit.questions[i];
      const res = await backendFetch(`/api/v1/placement-sessions/${activePlacement.id}/audit/100/answer/${q.audit_item_id}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          selected_option_id: q.card_id,
          idempotency_key: `checkpoint-rest-audit-${i}`,
          answered_at: new Date(Date.UTC(2024, 0, 4, 1, i, 0)).toISOString(),
        }),
      });
      if (!res.ok) throw new Error(await res.text());
    }

    await page.goto(appUrl('/placement/checkpoint'));
    await expect(page.locator('text=共抽查')).toBeVisible({ timeout: 15000 });
    await page.locator('#take-a-break').click();
    await page.waitForURL(/\/$/);
    await page.waitForLoadState('networkidle');

    await expect(page.locator('#home-primary-resume-placement')).toBeVisible();
    await expect(page.locator('#study-btn-25')).toBeHidden();
    await expect(page.locator('text=開始抽查')).toBeHidden();
    await expect(page.locator('text=快速抽查')).toBeHidden();

    await page.locator('#home-primary-resume-placement').click();
    await page.waitForURL(/\/placement$/);
    await expect(page.locator('#placement-term')).toBeVisible({ timeout: 15000 });
    await expect(page.locator('#placement-progress')).toContainText('100');
    await expect(page.locator('#placement-term')).toHaveText(expectedNextCard.english);
    await expect(page).not.toHaveURL(/\/placement\/checkpoint/);
    await page.locator('#btn-known').click();
    await expect(page.locator('#placement-progress')).toContainText('101');

    await context.close();
  });

  test('Checkpoint page does not create an audit from local-only checkpoint progress', async () => {
    test.setTimeout(90000);
    const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
      viewport: { width: 390, height: 844 },
    });
    const page = context.pages[0] || await context.newPage();

    await page.goto(appUrl('/'));
    await page.waitForLoadState('networkidle');
    await page.locator('#placement-btn-250').click();
    await page.waitForURL(/\/placement/);
    await expect(page.locator('#placement-term')).toBeVisible();

    const activePlacement = await waitActivePlacement();
    const manifest = JSON.parse(activePlacement.manifest_json);

    await page.evaluate(({ sessionId, manifestItems }) => {
      const w = window as any;
      const now = new Date().toISOString();
      return w.db.transaction('rw', w.db.placement_sessions, w.db.placement_items, async () => {
        await w.db.placement_sessions.put({
          id: sessionId,
          requestedCount: 250,
          status: 'checkpoint_pending',
          manifest: manifestItems.map((item: any) => ({ position: item.position, cardId: item.card_id })),
          startedAt: now,
          updatedAt: now,
        });
        await w.db.placement_items.bulkPut(manifestItems.slice(0, 100).map((item: any) => ({
          id: `local-ahead-${item.position}`,
          sessionId,
          position: item.position,
          cardId: item.card_id,
          result: 'known',
          answeredAt: now,
          idempotencyKey: `local-ahead-${item.position}`,
          undone: false,
        })));
      });
    }, { sessionId: activePlacement.id, manifestItems: manifest });

    await page.goto(appUrl('/placement/checkpoint'));
    await expect(page.locator('text=抽查載入失敗')).toBeVisible({ timeout: 15000 });
    await expect(page.locator('#audit-term')).toBeHidden();

    await context.close();
  });

  test('Stale local checkpoint state is discarded when the server has no active placement', async () => {
    test.setTimeout(90000);
    const context = await chromium.launchPersistentContext(USER_DATA_DIR, {
      viewport: { width: 390, height: 844 },
    });
    const page = context.pages[0] || await context.newPage();

    await page.goto(appUrl('/'));
    await page.waitForLoadState('networkidle');
    await page.locator('#placement-btn-250').click();
    await page.waitForURL(/\/placement/);
    await expect(page.locator('#placement-term')).toBeVisible();

    const activePlacement = await waitActivePlacement();
    const manifest = JSON.parse(activePlacement.manifest_json);

    await page.evaluate(({ sessionId, manifestItems }) => {
      const w = window as any;
      const now = new Date().toISOString();
      return w.db.transaction('rw', w.db.placement_sessions, w.db.placement_items, async () => {
        await w.db.placement_sessions.put({
          id: sessionId,
          requestedCount: 250,
          status: 'checkpoint_pending',
          manifest: manifestItems.map((item: any) => ({ position: item.position, cardId: item.card_id })),
          startedAt: now,
          updatedAt: now,
        });
        await w.db.placement_items.bulkPut(manifestItems.slice(0, 100).map((item: any) => ({
          id: `local-stale-${item.position}`,
          sessionId,
          position: item.position,
          cardId: item.card_id,
          result: 'known',
          answeredAt: now,
          idempotencyKey: `local-stale-${item.position}`,
          undone: false,
        })));
      });
    }, { sessionId: activePlacement.id, manifestItems: manifest });

    const abandonRes = await backendFetch(`/api/v1/placement-sessions/${activePlacement.id}/abandon`, {
      method: 'POST',
    });
    if (!abandonRes.ok) throw new Error(await abandonRes.text());

    await page.goto(appUrl('/placement/checkpoint'));
    await page.waitForURL(/\/$/, { timeout: 15000 });
    await page.waitForLoadState('networkidle');

    await expect(page.locator('#start-audit')).toBeHidden();
    await expect(page.locator('#study-btn-25')).toBeHidden();
    await expect(page.locator('#home-primary-placement')).toBeVisible();

    await page.goto(appUrl('/study'));
    await expect(page.locator('text=正式複習需要先完成盤點')).toBeVisible();

    await context.close();
  });
});
