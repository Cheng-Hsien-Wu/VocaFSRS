import { v4 as uuidv4 } from 'uuid';

import {
  db,
  type PlacementCard,
  type StudyItem,
  type StudySession,
} from '../db/dexie';
import { ACTIVE_STUDY_STATUSES } from '../domain';
import {
  api,
  toPlacementCard,
  type StudySessionDto,
  type StudySessionItemDto,
} from './api';

interface StudyApiErrorDetail {
  error?: string;
  message?: string | null;
  available_count?: number;
  next_due?: string | null;
  availability_state?: string | null;
  pending_adjudication_count?: number;
  placement_status?: {
    remaining_count?: number;
    [key: string]: unknown;
  } | null;
}

interface StudyApiError extends Error {
  status?: number;
  detail?: string | StudyApiErrorDetail;
}

export type PlacementRequiredStatus = NonNullable<
  StudyApiErrorDetail['placement_status']
>;

export interface StudySetupError {
  errorType: string;
  availableCount: number;
  nextDue?: string | null;
  availabilityState?: string | null;
  pendingAdjudicationCount?: number;
  message?: string | null;
  placementStatus?: PlacementRequiredStatus | null;
}

export interface StudyBootstrap {
  session: StudySession;
  position: number;
  cards: Record<string, PlacementCard>;
}

export interface StudyAnswerRecord {
  session: StudySession;
  position: number;
  cards?: Record<string, PlacementCard>;
}

export function studySetupError(error: unknown): StudySetupError {
  const apiError = error as StudyApiError;
  const detail = typeof apiError.detail === 'object' ? apiError.detail : undefined;

  if (detail?.error === 'placement_required') {
    return {
      errorType: 'placement_required',
      availableCount: 0,
      message: detail.message ?? null,
      placementStatus: detail.placement_status ?? null,
    };
  }
  if (detail?.error === 'pending_adjudication') {
    return {
      errorType: 'pending_adjudication',
      availableCount: 0,
      availabilityState: detail.availability_state ?? null,
      pendingAdjudicationCount: detail.pending_adjudication_count ?? 0,
      message: detail.message ?? null,
    };
  }
  if (detail?.error === 'insufficient_cards' || detail?.error === 'no_due_cards') {
    return {
      errorType: detail.error,
      availableCount: detail.available_count ?? 0,
      nextDue: detail.next_due ?? null,
      availabilityState: detail.availability_state ?? null,
      pendingAdjudicationCount: detail.pending_adjudication_count ?? 0,
    };
  }
  if (apiError.status === 409 || apiError.status === 400) {
    return {
      errorType: 'deck_scope_required',
      availableCount: 0,
      message: typeof apiError.detail === 'string'
        ? apiError.detail
        : detail?.message ?? null,
    };
  }
  return { errorType: 'session_unavailable', availableCount: 0 };
}

export async function reconcileStudyPosition(
  sessionId: string,
  manifest: StudySession['manifest'],
): Promise<number> {
  const items = await db.study_items
    .where('studySessionId')
    .equals(sessionId)
    .toArray();
  const answeredPositions = new Set(items.map(item => item.position));
  const firstUnanswered = manifest.findIndex(
    item => !answeredPositions.has(item.position),
  );
  return firstUnanswered === -1 ? manifest.length : firstUnanswered;
}

export async function createStudySession(
  requestedSize: number,
  mode: 'fixed' | 'timed',
  activationBudget: number | null,
): Promise<StudyBootstrap> {
  const serverSession = await api.createStudySession(
    requestedSize,
    mode,
    activationBudget,
  );
  return cacheServerStudySession(serverSession);
}

export async function bootstrapStudySession(
  requestedSize: number,
  mode: 'fixed' | 'timed',
  shouldResume: boolean,
  activationBudget: number | null,
): Promise<StudyBootstrap> {
  let session: StudySession | null = null;

  if (shouldResume || sessionStorage.getItem('study_resume') === '1') {
    session = await db.study_sessions
      .where('status')
      .anyOf([...ACTIVE_STUDY_STATUSES])
      .first() ?? null;
    if (!session) {
      try {
        const serverSession = await api.getActiveStudySession();
        if (serverSession) return cacheServerStudySession(serverSession);
      } catch (error) {
        console.warn('Could not fetch active study session from server:', error);
      }
    }
  }

  if (!session) {
    const created = await createStudySession(requestedSize, mode, activationBudget);
    sessionStorage.setItem('study_resume', '1');
    return created;
  }

  const cards = await loadCachedCards(session);
  const position = await reconcileStudyPosition(session.id, session.manifest);
  session.cardsAnswered = Math.max(session.cardsAnswered, position);
  await db.study_sessions.put(session);
  return { session, position, cards };
}

export async function recordTypedStudyAnswer(
  session: StudySession,
  position: number,
  typedAnswer: string,
): Promise<StudyAnswerRecord> {
  const manifestItem = session.manifest[position];
  if (!manifestItem) return { session, position };

  const answeredAt = new Date().toISOString();
  const idempotencyKey = uuidv4();
  const response = await api.batchTypedStudyAnswers(session.id, [{
    idempotency_key: idempotencyKey,
    session_item_id: manifestItem.id,
    card_id: manifestItem.cardId,
    typed_answer: typedAnswer,
    answered_at: answeredAt,
  }]);
  if (response.conflicts.includes(idempotencyKey)) {
    const serverSession = await api.getStudySession(session.id);
    return cacheServerStudySession(serverSession);
  }
  if (
    !response.accepted.includes(idempotencyKey)
    && !response.duplicates.includes(idempotencyKey)
  ) {
    throw new Error('Typed answer was not accepted by the server.');
  }

  await db.study_items.put({
    id: manifestItem.id,
    studySessionId: session.id,
    position,
    cardId: manifestItem.cardId,
    result: 'Pending',
    idempotencyKey,
    answeredAt,
    typedAnswer,
    adjudicationStatus: 'pending',
    correctOptionCardId: manifestItem.cardId,
  });

  const updatedSession = {
    ...session,
    cardsAnswered: session.cardsAnswered + 1,
    updatedAt: new Date().toISOString(),
  };
  await db.study_sessions.put(updatedSession);
  return { session: updatedSession, position: position + 1 };
}

export async function cacheServerStudySession(
  serverSession: StudySessionDto,
): Promise<StudyBootstrap> {
  const items = await api.getStudySessionItems(serverSession.id);
  await Promise.all([
    cacheItemCards(items),
    cacheAnsweredStudyItems(serverSession.id, items),
  ]);

  const session = mapServerSession(serverSession, items);
  await db.study_sessions.put(session);
  return {
    session,
    position: await reconcileStudyPosition(session.id, session.manifest),
    cards: cardsMap(items),
  };
}

function mapServerSession(
  serverSession: StudySessionDto,
  items: StudySessionItemDto[],
): StudySession {
  return {
    id: serverSession.id,
    requestedSize: serverSession.requested_size,
    mode: serverSession.mode,
    status: serverSession.sync_status,
    manifest: items.map(item => ({
      id: item.id,
      position: item.position,
      cardId: item.target_card_id,
      sourceType: item.source_type,
    })),
    startedAt: serverSession.started_at,
    updatedAt: new Date().toISOString(),
    cardsAnswered: serverSession.cards_answered,
    againCount: serverSession.again_count,
    hardCount: serverSession.hard_count,
    goodCount: serverSession.good_count,
  };
}

function cardsMap(items: StudySessionItemDto[]): Record<string, PlacementCard> {
  return Object.fromEntries(
    items
      .filter(item => item.card)
      .map(item => [item.target_card_id, toPlacementCard(item.card!)]),
  );
}

async function loadCachedCards(
  session: StudySession,
): Promise<Record<string, PlacementCard>> {
  const cardIds = session.manifest.map(item => item.cardId);
  const cards = await db.placement_cards.where('id').anyOf(cardIds).toArray();
  return Object.fromEntries(cards.map(card => [card.id, card]));
}

async function cacheItemCards(items: StudySessionItemDto[]) {
  const cards = items.flatMap(item => (
    item.card ? [toPlacementCard(item.card)] : []
  ));
  if (cards.length > 0) await db.placement_cards.bulkPut(cards);
}

async function cacheAnsweredStudyItems(
  sessionId: string,
  items: StudySessionItemDto[],
) {
  const answeredItems: StudyItem[] = items
    .filter(
      (item): item is StudySessionItemDto & { answered_at: string } =>
        Boolean(item.answered_at),
    )
    .map(item => ({
      id: item.id,
      studySessionId: sessionId,
      position: item.position,
      cardId: item.target_card_id,
      result: 'Pending',
      idempotencyKey: item.idempotency_key ?? `server-${item.id}`,
      answeredAt: item.answered_at,
      adjudicationStatus: item.sync_status === 'pending_adjudication'
        ? 'pending'
        : undefined,
      correctOptionCardId: item.target_card_id,
    }));

  await db.transaction('rw', db.study_items, async () => {
    await db.study_items.where('studySessionId').equals(sessionId).delete();
    if (answeredItems.length > 0) {
      await db.study_items.bulkPut(answeredItems);
    }
  });
}
