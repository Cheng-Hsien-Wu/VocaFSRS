import { db, type PlacementItem, type PlacementSession } from '../db/dexie';
import {
  ACTIVE_PLACEMENT_STATUSES,
  PLACEMENT_SESSION_STATUSES,
  type PlacementSessionStatus,
} from '../domain';
import { api } from './api';

const LEGACY_PLACEMENT_CHECKPOINT_SIZE = 100;
const PLACEMENT_MILESTONE_SIZE = 50;
const PENDING_EVENT_STATUSES = ['draft', 'ready', 'failed'] as const;

interface ServerPlacementManifestItem {
  position: number;
  card_id: string;
}

interface PlacementApiErrorDetail {
  error?: string;
  message?: string | null;
  available_count?: number;
}

interface PlacementApiError extends Error {
  status?: number;
  detail?: string | PlacementApiErrorDetail;
}

export type PlacementPhase =
  | 'loading'
  | 'card'
  | 'fuzzy_flash'
  | 'milestone'
  | 'checkpoint'
  | 'problematic_sheet'
  | 'paused'
  | 'complete';

export interface PlacementSetupError {
  errorType: string;
  availableCount: number;
  message?: string | null;
}

export interface PlacementBootstrap {
  session: PlacementSession;
  position: number;
}

export function placementSetupError(error: unknown): PlacementSetupError | null {
  const apiError = error as PlacementApiError;
  const detail = typeof apiError.detail === 'object' ? apiError.detail : undefined;

  if (detail?.error === 'insufficient_cards') {
    return {
      errorType: 'insufficient_cards',
      availableCount: detail.available_count ?? 0,
    };
  }
  if (apiError.status === 409 || apiError.status === 400) {
    return {
      errorType: 'deck_scope_required',
      availableCount: 0,
      message: typeof apiError.detail === 'string' ? apiError.detail : detail?.message ?? null,
    };
  }
  return null;
}

export function placementPhaseForPosition(session: PlacementSession, position: number): PlacementPhase {
  if (position >= session.requestedCount) return 'complete';
  if (position > 0 && position % placementCheckpointSize(session) === 0) return 'checkpoint';
  if (position > 0 && position % PLACEMENT_MILESTONE_SIZE === 0) return 'milestone';
  return 'card';
}

export function placementCheckpointSize(session: PlacementSession): number {
  return session.checkpointSize || LEGACY_PLACEMENT_CHECKPOINT_SIZE;
}

export async function persistPlacementPhase(session: PlacementSession, phase: PlacementPhase) {
  if (phase !== 'complete' && phase !== 'checkpoint') return;
  await db.placement_sessions.update(session.id, {
    status: phase === 'complete' ? 'completed' : 'checkpoint_pending',
    updatedAt: new Date().toISOString(),
  });
}

export async function reconcileSessionPosition(
  sessionId: string,
  manifest: PlacementSession['manifest'],
): Promise<number> {
  const [items, pendingEvents] = await Promise.all([
    db.placement_items.where('sessionId').equals(sessionId).toArray(),
    db.pending_events
      .where('sessionId')
      .equals(sessionId)
      .filter(event => (
        event.type === 'placement'
        && PENDING_EVENT_STATUSES.includes(event.syncStatus as typeof PENDING_EVENT_STATUSES[number])
      ))
      .toArray(),
  ]);

  const activeItemsByPosition = new Map<number, PlacementItem>();
  for (const item of items) {
    if (!item.undone) activeItemsByPosition.set(item.position, item);
  }

  const undoneTargetKeys = new Set(
    pendingEvents
      .filter(event => event.payload.event_type === 'undo')
      .map(event => event.payload.target_event_id),
  );

  for (let position = 0; position < manifest.length; position += 1) {
    const item = activeItemsByPosition.get(position);
    if (!item || undoneTargetKeys.has(item.idempotencyKey)) return position;
  }
  return manifest.length;
}

export async function createPlacementSession(requestedCount: number): Promise<PlacementSession> {
  const serverSession = await api.createPlacementSession(requestedCount);
  const session = mapServerSession(serverSession);
  await db.placement_sessions.put(session);
  return session;
}

export async function bootstrapPlacementSession(requestedCount: number): Promise<PlacementBootstrap> {
  try {
    const serverSession = await api.getActivePlacementSession();
    if (!serverSession) {
      await db.placement_sessions
        .where('status')
        .anyOf([...ACTIVE_PLACEMENT_STATUSES])
        .modify({ status: 'abandoned', updatedAt: new Date().toISOString() });
      return { session: await createPlacementSession(requestedCount), position: 0 };
    }

    const session = mapServerSession(serverSession);
    await db.placement_sessions.put(session);
    const localPosition = await reconcileSessionPosition(session.id, session.manifest);
    const pendingEventCount = await db.pending_events
      .where('sessionId')
      .equals(session.id)
      .filter(event => (
        event.type === 'placement'
        && PENDING_EVENT_STATUSES.includes(event.syncStatus as typeof PENDING_EVENT_STATUSES[number])
      ))
      .count();

    return {
      session,
      position: pendingEventCount > 0
        ? localPosition
        : serverSession.current_position ?? localPosition,
    };
  } catch (error) {
    if (placementSetupError(error)) throw error;
    console.warn('Failed to contact server, falling back to local Dexie:', error);

    const localSessions = await db.placement_sessions
      .where('status')
      .anyOf([...ACTIVE_PLACEMENT_STATUSES])
      .toArray();
    if (localSessions.length === 0) {
      throw new Error('Cannot start a new placement session offline.', { cause: error });
    }

    const session = localSessions[0];
    return {
      session,
      position: await reconcileSessionPosition(session.id, session.manifest),
    };
  }
}

function mapServerSession(serverSession: Awaited<ReturnType<typeof api.createPlacementSession>>): PlacementSession {
  const status = parsePlacementSessionStatus(serverSession.status);
  const manifest = (JSON.parse(serverSession.manifest_json) as ServerPlacementManifestItem[]).map(item => ({
    position: item.position,
    cardId: item.card_id,
  }));
  return {
    id: serverSession.id,
    requestedCount: serverSession.requested_count,
    status,
    manifest,
    startedAt: serverSession.started_at,
    updatedAt: new Date().toISOString(),
    checkpointSize: serverSession.checkpoint_size,
  };
}

function parsePlacementSessionStatus(status: string): PlacementSessionStatus {
  if (PLACEMENT_SESSION_STATUSES.some(candidate => candidate === status)) {
    return status as PlacementSessionStatus;
  }
  throw new Error(`Unsupported placement session status: ${status}`);
}
