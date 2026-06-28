import Dexie, { type EntityTable } from 'dexie';
import type {
  AdjudicationStatus,
  PendingEventType,
  PendingSyncStatus,
  PlacementAuditResult,
  PlacementAuditStatus,
  PlacementAnswer,
  PlacementSessionStatus,
  StudyItemResult,
  StudySessionStatus,
} from '../domain';

export interface PlacementSession {
  id: string;
  requestedCount: number;
  status: PlacementSessionStatus;
  manifest: { position: number; cardId: string }[];
  startedAt: string;
  updatedAt: string;
  checkpointSize: number;
}

export interface PlacementItem {
  id: string;
  sessionId: string;
  position: number;
  cardId: string;
  result: PlacementAnswer;
  problematicReason?: string;
  idempotencyKey: string;
  answeredAt: string;
  undone?: boolean;
  auditReclassified?: boolean;
}

export interface PlacementAudit {
  id: string;
  sessionId: string;
  checkpoint: number;
  status: PlacementAuditStatus;
  errorRate?: number;
  createdAt: string;
}

export interface PlacementAuditItem {
  id: string;
  placementAuditId: string;
  cardId: string;
  sampleBatch: number;
  optionsJson: string; // serialized JSON array of options: {card_id: string, chinese: string}[]
  correctOptionId: string;
  resolvedResult?: PlacementAuditResult | null;
  userSelectedOptionId?: string | null;
}

export interface PendingEvent {
  localId?: number;
  sessionId: string;
  idempotencyKey: string;
  type: PendingEventType;
  payload: Record<string, unknown>;
  syncStatus: PendingSyncStatus;
  createdAt: string;
}

export interface PlacementCard {
  id: string;
  english: string;
  chineseMeaning: string;
  partOfSpeech?: string;
  senseHint?: string;
  exampleSentence?: string;
  exampleTranslation?: string;
}

export interface StudySession {
  id: string;
  requestedSize: number;
  mode: 'fixed' | 'timed';
  status: StudySessionStatus;
  manifest: { id: string; position: number; cardId: string; sourceType?: string }[];
  startedAt: string;
  updatedAt: string;
  cardsAnswered: number;
  againCount: number;
  hardCount: number;
  goodCount: number;
}

export interface StudyItem {
  id: string;
  studySessionId: string;
  position: number;
  cardId: string;
  result: StudyItemResult;
  idempotencyKey: string;
  answeredAt: string;
  typedAnswer?: string;
  adjudicationStatus?: AdjudicationStatus;
  adjudicationReason?: string;
  nextDue?: string;
  selectedOptionCardId?: string;
  correctOptionCardId: string;
}

const db = new Dexie('VocabCoachDatabase') as Dexie & {
  placement_sessions: EntityTable<PlacementSession, 'id'>;
  placement_items: EntityTable<PlacementItem, 'id'>;
  pending_events: EntityTable<PendingEvent, 'localId'>;
  placement_cards: EntityTable<PlacementCard, 'id'>;
  placement_audits: EntityTable<PlacementAudit, 'id'>;
  placement_audit_items: EntityTable<PlacementAuditItem, 'id'>;
  study_sessions: EntityTable<StudySession, 'id'>;
  study_items: EntityTable<StudyItem, 'id'>;
};

db.version(4).stores({
  placement_sessions: 'id, status, updatedAt',
  placement_items: 'id, sessionId, position, idempotencyKey',
  pending_events: '++localId, sessionId, idempotencyKey, syncStatus, type, createdAt',
  placement_cards: 'id',
  placement_audits: 'id, sessionId, checkpoint, status',
  placement_audit_items: 'id, placementAuditId, cardId, sampleBatch',
  study_sessions: 'id, status, updatedAt',
  study_items: 'id, studySessionId, position, idempotencyKey',
});

if (typeof window !== 'undefined') {
  (window as Window & { db?: typeof db }).db = db;
}

export { db };
