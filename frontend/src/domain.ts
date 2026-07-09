export const PLACEMENT_SESSION_STATUSES = [
  'active',
  'checkpoint_pending',
  'audit_active',
  'paused',
  'completed',
  'abandoned',
] as const;

export type PlacementSessionStatus = (typeof PLACEMENT_SESSION_STATUSES)[number];

export const ACTIVE_PLACEMENT_STATUSES = [
  'active',
  'checkpoint_pending',
  'audit_active',
  'paused',
] as const satisfies readonly PlacementSessionStatus[];

export const PLACEMENT_RESULTS = ['known', 'fuzzy', 'unknown', 'problematic'] as const;
export type PlacementAnswer = (typeof PLACEMENT_RESULTS)[number];

export const PLACEMENT_EVENT_TYPES = ['answer', 'undo', 'audit_reclassify'] as const;
export type PlacementEventType = (typeof PLACEMENT_EVENT_TYPES)[number];

export const PLACEMENT_AUDIT_STATUSES = ['active', 'completed', 'skipped'] as const;
export type PlacementAuditStatus = (typeof PLACEMENT_AUDIT_STATUSES)[number];

export const PLACEMENT_AUDIT_RESULTS = ['correct', 'incorrect', 'problematic'] as const;
export type PlacementAuditResult = (typeof PLACEMENT_AUDIT_RESULTS)[number];

export const PENDING_EVENT_TYPES = ['placement', 'audit'] as const;
export type PendingEventType = (typeof PENDING_EVENT_TYPES)[number];

export const PENDING_SYNC_STATUSES = ['draft', 'ready', 'syncing', 'synced', 'failed'] as const;
export type PendingSyncStatus = (typeof PENDING_SYNC_STATUSES)[number];

export const STUDY_SESSION_STATUSES = ['active', 'paused', 'completed', 'abandoned'] as const;
export type StudySessionStatus = (typeof STUDY_SESSION_STATUSES)[number];
export const ACTIVE_STUDY_STATUSES = [
  'active',
  'paused',
] as const satisfies readonly StudySessionStatus[];

export const STUDY_ITEM_RESULTS = ['Pending', 'Again', 'Hard', 'Good'] as const;
export type StudyItemResult = (typeof STUDY_ITEM_RESULTS)[number];

export const ADJUDICATION_STATUSES = ['pending', 'processing', 'succeeded', 'failed'] as const;
export type AdjudicationStatus = (typeof ADJUDICATION_STATUSES)[number];

export const STUDY_AVAILABILITY_STATES = [
  'available_due',
  'available_new',
  'waiting',
  'pending_adjudication',
  'not_started',
  'empty',
  'deck_scope_required',
  'placement_required',
  'no_cards',
] as const;
export type StudyAvailabilityState = (typeof STUDY_AVAILABILITY_STATES)[number];

export const PLACEMENT_GATE_STATUSES = [
  'no_cards',
  'required',
  'in_progress',
  'complete',
] as const;
export type PlacementGateStatus = (typeof PLACEMENT_GATE_STATUSES)[number];

export const STUDY_SUMMARY_SESSION_STORAGE_KEY = 'study_summary_typed_session_id';

export interface StudyPlanInfo {
  started: boolean;
  remaining_days: number;
  remaining_new_cards: number;
  suggested_new_cards_today: number;
  due_count: number;
  next_due: string | null;
  pending_new_count?: number;
  available_now_count?: number;
  next_review_due_at?: string | null;
  pending_adjudication_count?: number;
  pending_adjudication_session_id?: string | null;
  availability_state?: StudyAvailabilityState;
  deck_scope_error?: string;
  placement_status?: {
    status: PlacementGateStatus;
    complete: boolean;
    total_eligible_count: number;
    remaining_count: number;
    active_session_id: string | null;
    active_session_status: PlacementSessionStatus | null;
  } | null;
}
