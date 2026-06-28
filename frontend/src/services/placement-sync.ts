import { db } from '../db/dexie';
import { api } from './api';

interface AuditEventPayload {
  checkpoint: number;
  auditItemId: string;
  selectedOptionId: string;
  idempotencyKey: string;
  answeredAt: string;
}

function isAuditEventPayload(payload: Record<string, unknown>): payload is Record<string, unknown> & AuditEventPayload {
  return (
    typeof payload.checkpoint === 'number' &&
    typeof payload.auditItemId === 'string' &&
    typeof payload.selectedOptionId === 'string' &&
    typeof payload.idempotencyKey === 'string' &&
    typeof payload.answeredAt === 'string'
  );
}

declare global {
  interface Window {
    __vocabPlacementSyncInterval?: number;
    __vocabPlacementOnlineHandler?: () => void;
  }
}

let syncPromise: Promise<void> | null = null;

async function runPlacementSync(limit = 25) {
  try {
    // Include failed events so transient offline/API errors are retried.
    const placementEvents = await db.pending_events
      .where('type')
      .equals('placement')
      .filter(e => e.syncStatus === 'ready' || e.syncStatus === 'failed')
      .limit(limit)
      .toArray();
    const auditEvents = await db.pending_events
      .where('type')
      .equals('audit')
      .filter(e => e.syncStatus === 'ready' || e.syncStatus === 'failed')
      .limit(limit)
      .toArray();
      
    if (placementEvents.length === 0 && auditEvents.length === 0) return;
    
    // 1. Process placement events in batch per session
    if (placementEvents.length > 0) {
      const sessions = new Set(placementEvents.map(e => e.sessionId));
      for (const sessionId of sessions) {
        const sessionEvents = placementEvents.filter(e => e.sessionId === sessionId);
        const payloads = sessionEvents.map(e => e.payload);
        
        try {
          const response = await api.batchPlacementEvents(sessionId, payloads);
          const syncedKeys = new Set([...response.accepted, ...response.duplicates]);
          
          for (const pe of sessionEvents) {
            if (syncedKeys.has(pe.idempotencyKey)) {
              // Delete synced events to prevent IndexedDB accumulation.
              await db.pending_events.delete(pe.localId!);
            } else {
              await db.pending_events.update(pe.localId!, { syncStatus: 'failed' });
            }
          }
        } catch (err) {
          console.error(`Failed to sync placement batch for session ${sessionId}:`, err);
          for (const pe of sessionEvents) {
            const localSession = await db.placement_sessions.get(pe.sessionId);
            if (localSession?.status === 'abandoned') {
              // Delete events for abandoned sessions — they will never be retried
              await db.pending_events.delete(pe.localId!);
            } else {
              await db.pending_events.update(pe.localId!, { syncStatus: 'ready' });
            }
          }
        }
      }
    }
    
    // 2. Process audit events sequentially
    for (const ae of auditEvents) {
      try {
        if (!isAuditEventPayload(ae.payload)) {
          throw new Error('Invalid audit event payload');
        }
        const { checkpoint, auditItemId, selectedOptionId, idempotencyKey, answeredAt } = ae.payload;
        await api.answerAuditQuestion(ae.sessionId, checkpoint, auditItemId, {
          selected_option_id: selectedOptionId,
          idempotency_key: idempotencyKey,
          answered_at: answeredAt,
        });
        // Delete synced audit event to prevent IndexedDB accumulation
        await db.pending_events.delete(ae.localId!);
      } catch (err) {
        console.error(`Failed to sync audit event ${ae.idempotencyKey}:`, err);
        const localSession = await db.placement_sessions.get(ae.sessionId);
        if (localSession?.status === 'abandoned') {
          // Delete events for abandoned sessions — they will never be retried
          await db.pending_events.delete(ae.localId!);
        } else {
          await db.pending_events.update(ae.localId!, { syncStatus: 'ready' });
        }
      }
    }
  } catch (err) {
    console.error("Failed to sync placement events:", err);
  }
}

export async function syncPlacementEvents() {
  if (!syncPromise) {
    syncPromise = runPlacementSync().finally(() => {
      syncPromise = null;
    });
  }
  return syncPromise;
}

export async function forceSyncPlacementEvents() {
  if (syncPromise) {
    await syncPromise;
  }
  syncPromise = (async () => {
    for (let i = 0; i < 20; i++) {
      await runPlacementSync(100);
      const remaining = await db.pending_events
        .filter(e =>
          (e.type === 'placement' || e.type === 'audit') &&
          (e.syncStatus === 'ready' || e.syncStatus === 'failed')
        )
        .count();
      if (remaining === 0) return;
    }
  })().finally(() => {
    syncPromise = null;
  });
  return syncPromise;
}

// Background sync loop
if (typeof window !== 'undefined') {
  if (window.__vocabPlacementSyncInterval) {
    window.clearInterval(window.__vocabPlacementSyncInterval);
  }
  window.__vocabPlacementSyncInterval = window.setInterval(syncPlacementEvents, 10000);

  // Run on reconnect
  if (window.__vocabPlacementOnlineHandler) {
    window.removeEventListener('online', window.__vocabPlacementOnlineHandler);
  }
  window.__vocabPlacementOnlineHandler = () => {
    syncPlacementEvents();
  };
  window.addEventListener('online', window.__vocabPlacementOnlineHandler);
}
