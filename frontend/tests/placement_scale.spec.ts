import { test, expect, chromium } from '@playwright/test';
import { spawn, execSync, ChildProcess } from 'child_process';
import path from 'path';
import fs from 'fs';
import { fileURLToPath } from 'url';

const __filename = fileURLToPath(import.meta.url);
const __dirname_compat = path.dirname(__filename);

const TEMP_DB_PATH = path.join(__dirname_compat, '../../backend/data/temp_scale_test.db');
const ABS_DB_URL = 'sqlite+aiosqlite:///' + TEMP_DB_PATH;
const USER_DATA_DIR = path.join(__dirname_compat, '../temp-scale-profile');

let uvicornProcess: ChildProcess;
let viteProcess: ChildProcess;

test.beforeAll(async () => {
  // 1. Clean up old test DB & browser profile if exist
  if (fs.existsSync(TEMP_DB_PATH)) {
    fs.unlinkSync(TEMP_DB_PATH);
  }
  if (fs.existsSync(USER_DATA_DIR)) {
    fs.rmSync(USER_DATA_DIR, { recursive: true, force: true });
  }

  // 2. Run large seeder against temp database
  try {
    console.log('Running app.seed_large...');
    execSync(`cd ../backend && PYTHONPATH=. DATABASE_URL=${ABS_DB_URL} uv run python -m app.seed_large`, {
      stdio: 'inherit',
      env: { ...process.env, VOCAB_ENV: 'development' }
    });
  } catch (err) {
    console.error('Failed to run seed_large.py:', err);
    throw err;
  }

  // 3. Spawn isolated backend on port 8002
  console.log('Spawning isolated FastAPI server on port 8002...');
  uvicornProcess = spawn('uv', [
    'run', 'uvicorn', 'main:app',
    '--host', '127.0.0.1',
    '--port', '8002'
  ], {
    cwd: path.join(__dirname_compat, '../../backend'),
    stdio: 'ignore',
    env: {
      ...process.env,
      DATABASE_URL: ABS_DB_URL,
      VOCAB_ENV: 'development'
    }
  });

  // 4. Preview the built app; E2E does not need HMR or file watchers.
  console.log('Spawning isolated Vite preview server on port 5177...');
  viteProcess = spawn('npx', [
    'vite', 'preview', '--port', '5177'
  ], {
    cwd: path.join(__dirname_compat, '..'),
    stdio: 'ignore',
    env: {
      ...process.env,
      VITE_API_PORT: '8002',
      PORT: '5177'
    }
  });

  // 5. Wait for both servers to be responsive
  console.log('Waiting for backend and frontend ports to be ready...');
  await waitPort(8002);
  await waitPort(5177);
  console.log('Servers are fully ready.');
});

test.afterAll(async () => {
  // Kill uvicorn and vite processes
  if (uvicornProcess) {
    uvicornProcess.kill('SIGINT');
  }
  if (viteProcess) {
    viteProcess.kill('SIGINT');
  }

  // Delay slightly to let processes shut down
  await new Promise(r => setTimeout(r, 1000));

  // Clean up database file and browser profile
  if (fs.existsSync(TEMP_DB_PATH)) {
    try {
      fs.unlinkSync(TEMP_DB_PATH);
    } catch {}
  }
  if (fs.existsSync(USER_DATA_DIR)) {
    try {
      fs.rmSync(USER_DATA_DIR, { recursive: true, force: true });
    } catch {}
  }
});

async function waitPort(port: number) {
  for (let i = 0; i < 50; i++) {
    try {
      const res = await fetch(`http://127.0.0.1:${port}/`);
      if (res.status !== 500) {
        return;
      }
    } catch {}
    await new Promise(r => setTimeout(r, 200));
  }
  throw new Error(`Port ${port} failed to respond in time`);
}

test.describe('Placement Scale E2E & Browser Restart', () => {
  test('Complete scale E2E flow with browser profile restart and Dexie persistence validation', async () => {
    test.setTimeout(120000);
    // 1. Launch browser with a persistent context to simulate real browser restart
    let context = await chromium.launchPersistentContext(USER_DATA_DIR, {
      viewport: { width: 390, height: 844 },
    });
    let page = context.pages[0] || await context.newPage();

    page.on('console', msg => {
      if (msg.type() === 'error') {
        console.log('BROWSER ERROR:', msg.text());
      }
    });

    // Load home page
    await page.goto('http://127.0.0.1:5177/');
    await page.waitForLoadState('load');
    await expect(page.locator('.home-action-card')).toBeVisible();

    // 2. Use a session larger than one checkpoint so position 100 enters
    // the audit flow instead of completing the selected batch.
    await page.locator('#placement-btn-250').click();

    await page.waitForURL(/\/placement/, { timeout: 25000 });
    await expect(page.locator('#placement-term')).toBeVisible();

    // 3. Seed the first 99 answers through the API. Other browser tests cover
    // per-card clicking; this test owns large-session checkpoint persistence.
    const activeResponse = await fetch(
      'http://127.0.0.1:8002/api/v1/placement-sessions/active',
    );
    expect(activeResponse.ok).toBeTruthy();
    const active = await activeResponse.json();
    const manifest = JSON.parse(active.manifest_json);
    const seedResponse = await fetch(
      `http://127.0.0.1:8002/api/v1/placement-sessions/${active.id}/events/batch`,
      {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          events: manifest.slice(0, 99).map((item: any, index: number) => ({
            idempotency_key: `scale-seed-${index}`,
            event_type: 'answer',
            position: item.position,
            card_id: item.card_id,
            result: 'known',
            answered_at: new Date(Date.UTC(2024, 0, 3, 0, index, 0)).toISOString(),
          })),
        }),
      },
    );
    expect(seedResponse.ok).toBeTruthy();

    await page.reload({ waitUntil: 'networkidle' });
    await expect(page.locator('#placement-progress')).toContainText('99 ·');
    await page.locator('#btn-known').click();

    // When position 99 is answered, we reach currentPosition 100.
    // The page should redirect to the 100-card checkpoint!
    await page.waitForURL(/\/placement\/checkpoint/, { timeout: 25000 });
    await expect(page.locator('#audit-term')).toBeVisible();

    // Capture Dexie state BEFORE restart
    const preRestartDexie = await page.evaluate(async () => {
      const w = window as any;
      if (!w.db) return null;
      const sessions = await w.db.placement_sessions.toArray();
      const items = await w.db.placement_items.toArray();
      const pending = await w.db.pending_events.toArray();
      const audits = await w.db.placement_audits.toArray();
      const auditItems = await w.db.placement_audit_items.toArray();
      return { sessions, items, pending, audits, auditItems };
    });
    expect(preRestartDexie).not.toBeNull();

    // 5. TEST REAL BROWSER RESTART AT CHECKPOINT
    // Close browser context (saves Dexie / IndexedDB to profile)
    await context.close();

    // Launch chromium again using the SAME persistent profile
    context = await chromium.launchPersistentContext(USER_DATA_DIR, {
      viewport: { width: 390, height: 844 },
    });
    page = context.pages[0] || await context.newPage();

    page.on('console', msg => {
      if (msg.type() === 'error') {
        console.log('BROWSER ERROR:', msg.text());
      }
    });

    // Load home page, verify resumption redirect is offered or active
    await page.goto('http://127.0.0.1:5177/');
    await page.waitForLoadState('load');
    await page.waitForTimeout(1000);

    // Resumption modal/button should offer to resume active session
    const resumeBtn = page.locator('#home-primary-resume-placement');
    await expect(resumeBtn).toBeVisible();
    await resumeBtn.click();

    // Should redirect to checkpoint page — audit may auto-start so header may be absent
    await page.waitForURL(/\/placement\/checkpoint/, { timeout: 25000 });
    // Audit auto-starts on checkpoint load; just verify audit-term is shown
    await expect(page.locator('#audit-term')).toBeVisible({ timeout: 10000 });

    // Verify Dexie persistence after restart
    const postRestartDexie = await page.evaluate(async () => {
      const w = window as any;
      if (!w.db) return null;
      const sessions = await w.db.placement_sessions.toArray();
      const items = await w.db.placement_items.toArray();
      const pending = await w.db.pending_events.toArray();
      const audits = await w.db.placement_audits.toArray();
      const auditItems = await w.db.placement_audit_items.toArray();
      return { sessions, items, pending, audits, auditItems };
    });

    expect(postRestartDexie).not.toBeNull();
    const activeSession = postRestartDexie!.sessions.find(s => ['active', 'checkpoint_pending', 'audit_active', 'paused'].includes(s.status));
    expect(activeSession).toBeDefined();
    expect(activeSession!.requestedCount).toBe(250);

    // Verify active Dexie session is preserved
    expect(activeSession!.id).toBe(preRestartDexie!.sessions[0].id);

    // Verify manifest order is preserved
    expect(activeSession!.manifest.length).toBe(250);
    expect(activeSession!.manifest).toEqual(preRestartDexie!.sessions[0].manifest);

    // Verify pending events are preserved
    expect(postRestartDexie!.pending.length).toBe(preRestartDexie!.pending.length);
    for (let i = 0; i < preRestartDexie!.pending.length; i++) {
      expect(postRestartDexie!.pending[i].idempotencyKey).toBe(preRestartDexie!.pending[i].idempotencyKey);
      expect(postRestartDexie!.pending[i].type).toBe(preRestartDexie!.pending[i].type);
    }

    // Verify audit card order is preserved
    const preAuditCards = preRestartDexie!.auditItems.map(item => item.cardId);
    const postAuditCards = postRestartDexie!.auditItems.map(item => item.cardId);
    expect(postAuditCards).toEqual(preAuditCards);

    // Verify audit option order is preserved
    const preAuditOptions = preRestartDexie!.auditItems.map(item => item.optionsJson);
    const postAuditOptions = postRestartDexie!.auditItems.map(item => item.optionsJson);
    expect(postAuditOptions).toEqual(preAuditOptions);

    // 6. Complete the Audit with error rate >= 20% to trigger optional second sample
    await page.waitForTimeout(200);

    // Answer first 2 questions incorrectly to get 2/10 = 20% error rate
    for (let i = 0; i < 2; i++) {
      await expect(page.locator('#audit-term')).toBeVisible();
      await page.locator('#audit-btn-unknown').click();
      await page.waitForTimeout(200);
    }

    // Answer next 8 questions correctly to complete the first batch
    for (let i = 2; i < 10; i++) {
      await expect(page.locator('#audit-term')).toBeVisible();
      const currentTerm = await page.locator('#audit-term').textContent();
      const correctId = await page.evaluate(async (term) => {
        const w = window as any;
        const cards = await w.db.placement_cards.toArray();
        const card = cards.find((c: any) => c.english.trim() === term.trim());
        return card ? card.id : null;
      }, currentTerm || '');
      const optBtn = page.locator(`#audit-option-${correctId}`);
      if (await optBtn.isVisible()) {
        await optBtn.click();
      } else {
        await page.locator('.study-option').first().click();
      }
      await page.waitForTimeout(200);
    }

    // Since error rate is 20%, the page should load the second sample batch!
    await page.waitForTimeout(1500);
    await expect(page.locator('text=二次抽查')).toBeVisible();

    // Answer the second batch questions correctly until the audit is complete
    while (true) {
      const auditTerm = page.locator('#audit-term');
      if (!(await auditTerm.isVisible())) {
        break;
      }
      const currentTerm = await auditTerm.textContent();
      if (!currentTerm) break;
      
      const correctId = await page.evaluate(async (term) => {
        const w = window as any;
        const cards = await w.db.placement_cards.toArray();
        const card = cards.find((c: any) => c.english.trim() === term.trim());
        return card ? card.id : null;
      }, currentTerm);
      
      const optBtn = page.locator(`#audit-option-${correctId}`);
      if (await optBtn.isVisible()) {
        await optBtn.click();
      } else {
        await page.locator('.study-option').first().click();
      }
      await page.waitForTimeout(200);
    }

    // Wait for audit completion results
    await page.waitForTimeout(1500);
    await expect(page.locator('text=共抽查 20 題')).toBeVisible();
    await expect(page.locator('#continue-next-100')).toBeVisible();

    // 7. Continue beyond the restored checkpoint.
    await page.locator('#continue-next-100').click();
    await page.waitForURL(/\/placement/, { timeout: 25000 });

    // Position is 100 (answering item 101)
    await expect(page.locator('#placement-progress')).toContainText('100 ·');
    await page.locator('#btn-known').click();
    await page.waitForTimeout(100);

    // Position is 101 (answering item 102)
    await expect(page.locator('#placement-progress')).toContainText('101 ·');
    await page.locator('#btn-known').click();
    await page.waitForTimeout(500);

    await expect(page.locator('#placement-progress')).toContainText('102 ·');

    // Clean up persistent browser context
    await context.close();
  });
});
