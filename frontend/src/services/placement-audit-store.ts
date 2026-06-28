import { v4 as uuidv4 } from 'uuid';

import {
  db,
  type PlacementAudit,
  type PlacementAuditItem,
  type PlacementSession,
} from '../db/dexie';
import type { PlacementSessionDto } from './api';

export interface AuditQuestionDto {
  audit_item_id: string;
  card_id: string;
  english: string;
  options: { card_id: string; chinese: string }[];
  sample_batch: number;
  answered: boolean;
  selected_option_id: string | null;
  is_correct: boolean | null;
}

export interface AuditQuestionsResponse {
  status: 'active' | 'completed' | 'skipped';
  error_rate?: number;
  questions: AuditQuestionDto[];
}

export interface UIAuditQuestion {
  auditItemId: string;
  cardId: string;
  english: string;
  partOfSpeech?: string;
  options: { card_id: string; chinese: string }[];
  sampleBatch: number;
  answered: boolean;
  selectedOptionId: string | null;
  isCorrect: boolean | null;
}

interface ServerPlacementManifestItem {
  position: number;
  card_id: string;
}

export function mapCheckpointSession(
  serverSession: PlacementSessionDto,
): PlacementSession {
  return {
    id: serverSession.id,
    requestedCount: serverSession.requested_count,
    status: serverSession.status,
    manifest: (
      JSON.parse(serverSession.manifest_json) as ServerPlacementManifestItem[]
    ).map(item => ({
      position: item.position,
      cardId: item.card_id,
    })),
    startedAt: serverSession.started_at,
    updatedAt: new Date().toISOString(),
    checkpointSize: serverSession.checkpoint_size,
  };
}

export async function cacheAuditQuestions(
  sessionId: string,
  checkpoint: number,
  auditData: AuditQuestionsResponse,
): Promise<UIAuditQuestion[]> {
  const auditId = `${sessionId}_${checkpoint}`;
  await db.placement_audits.put({
    id: auditId,
    sessionId,
    checkpoint,
    status: auditData.status,
    errorRate: auditData.error_rate,
    createdAt: new Date().toISOString(),
  });

  const cardIds = auditData.questions.map(question => question.card_id);
  const cards = await db.placement_cards.bulkGet(cardIds);
  const cardsById = new Map(
    cards
      .filter((card): card is NonNullable<typeof card> => Boolean(card))
      .map(card => [card.id, card]),
  );

  const storedItems: PlacementAuditItem[] = [];
  const questions = auditData.questions.map(question => {
    storedItems.push({
      id: question.audit_item_id,
      placementAuditId: auditId,
      cardId: question.card_id,
      sampleBatch: question.sample_batch,
      optionsJson: JSON.stringify(question.options),
      correctOptionId: question.card_id,
      resolvedResult: question.is_correct === null
        ? null
        : question.is_correct
          ? 'correct'
          : 'incorrect',
      userSelectedOptionId: question.selected_option_id,
    });
    return {
      auditItemId: question.audit_item_id,
      cardId: question.card_id,
      english: question.english,
      partOfSpeech: cardsById.get(question.card_id)?.partOfSpeech,
      options: question.options,
      sampleBatch: question.sample_batch,
      answered: question.answered,
      selectedOptionId: question.selected_option_id,
      isCorrect: question.is_correct,
    };
  });

  await db.placement_audit_items.bulkPut(storedItems);
  return questions;
}

export async function loadCachedAudit(
  sessionId: string,
  checkpoint: number,
): Promise<{
  audit: PlacementAudit;
  questions: UIAuditQuestion[];
} | null> {
  const auditId = `${sessionId}_${checkpoint}`;
  const audit = await db.placement_audits.get(auditId);
  if (!audit) return null;

  const items = await db.placement_audit_items
    .where('placementAuditId')
    .equals(auditId)
    .toArray();
  const questions = await Promise.all(items.map(async item => {
    const card = await db.placement_cards.get(item.cardId);
    return {
      auditItemId: item.id,
      cardId: item.cardId,
      english: card?.english ?? '',
      partOfSpeech: card?.partOfSpeech,
      options: JSON.parse(item.optionsJson),
      sampleBatch: item.sampleBatch,
      answered: item.userSelectedOptionId != null,
      selectedOptionId: item.userSelectedOptionId ?? null,
      isCorrect: item.resolvedResult === 'correct'
        ? true
        : item.resolvedResult === 'incorrect'
          ? false
          : null,
    };
  }));
  return { audit, questions };
}

export async function queueAuditAnswer({
  sessionId,
  checkpoint,
  question,
  selectedOptionId,
}: {
  sessionId: string;
  checkpoint: number;
  question: UIAuditQuestion;
  selectedOptionId: string;
}) {
  const answeredAt = new Date().toISOString();
  const idempotencyKey = uuidv4();
  await db.transaction(
    'rw',
    db.placement_audit_items,
    db.pending_events,
    async () => {
      await db.placement_audit_items.update(question.auditItemId, {
        userSelectedOptionId: selectedOptionId,
        resolvedResult: selectedOptionId === 'problematic'
          ? 'problematic'
          : selectedOptionId === question.cardId
            ? 'correct'
            : 'incorrect',
      });
      await db.pending_events.add({
        sessionId,
        idempotencyKey,
        type: 'audit',
        payload: {
          checkpoint,
          auditItemId: question.auditItemId,
          selectedOptionId,
          idempotencyKey,
          answeredAt,
        },
        syncStatus: 'ready',
        createdAt: answeredAt,
      });
    },
  );
}
