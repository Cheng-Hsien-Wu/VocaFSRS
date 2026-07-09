import type { PlacementCard } from '../db/dexie';
import type {
  PlacementAnswer,
  PlacementEventType,
  PlacementSessionStatus,
  StudyPlanInfo,
  StudySessionStatus,
} from '../domain';

interface ApiErrorBody {
  detail?: string | { message?: string; [key: string]: unknown };
}

interface ApiError extends Error {
  status?: number;
  detail?: ApiErrorBody['detail'];
}

export interface PlacementCardDto {
  id: string;
  english: string;
  chinese_meaning: string;
  part_of_speech?: string | null;
  example_sentence?: string | null;
  example_translation?: string | null;
}

export type ImportFieldMapping = Record<string, string>;

export interface PlacementEventPayload {
  event_type?: PlacementEventType;
  target_event_id?: string;
  result?: PlacementAnswer;
  [key: string]: unknown;
}

export interface PlacementSessionDto {
  id: string;
  requested_count: number;
  status: PlacementSessionStatus;
  manifest_json: string;
  started_at: string;
  current_position: number;
  checkpoint_size: number;
}

export interface TypedStudyAnswerPayload {
  idempotency_key: string;
  session_item_id: string;
  card_id: string;
  typed_answer: string;
  answered_at: string;
}

export interface StudySessionDto {
  id: string;
  requested_size: number;
  mode: 'fixed' | 'timed';
  sync_status: StudySessionStatus;
  started_at: string;
  cards_answered: number;
  again_count: number;
  hard_count: number;
  good_count: number;
}

export interface StudySessionItemDto {
  id: string;
  position: number;
  target_card_id: string;
  source_type?: string;
  card?: PlacementCardDto;
  answered_at?: string | null;
  idempotency_key?: string | null;
  sync_status?: string | null;
}

export interface BatchAcceptedResponse {
  accepted: string[];
  duplicates: string[];
  conflicts: string[];
}

export type LlmProvider = 'auto' | 'gemini' | 'openrouter' | 'openai_compatible';

export interface LlmSettingsDto {
  provider: LlmProvider;
  model: string | null;
  base_url: string | null;
  timeout_seconds: number;
  api_key_configured: boolean;
  api_key_source: 'local' | 'environment' | 'none';
  effective_model: string;
}

export interface LlmSettingsUpdate {
  provider: LlmProvider;
  model?: string | null;
  base_url?: string | null;
  api_key?: string | null;
  clear_api_key?: boolean;
  timeout_seconds: number;
}

export interface LlmSettingsTestResponse {
  ok: boolean;
  provider?: string | null;
  model?: string | null;
  error?: string | null;
}

function apiError(message: string, status: number, detail: ApiErrorBody['detail']): ApiError {
  const err: ApiError = new Error(message);
  err.status = status;
  err.detail = detail;
  return err;
}

export function toPlacementCard(c: PlacementCardDto): PlacementCard {
  return {
    id: c.id,
    english: c.english,
    chineseMeaning: c.chinese_meaning,
    partOfSpeech: c.part_of_speech ?? undefined,
    exampleSentence: c.example_sentence ?? undefined,
    exampleTranslation: c.example_translation ?? undefined,
  };
}

export const api = {
  async getLlmSettings(): Promise<LlmSettingsDto> {
    const res = await fetch('/api/v1/llm-settings');
    if (!res.ok) throw new Error('Failed to fetch LLM settings');
    return res.json() as Promise<LlmSettingsDto>;
  },

  async updateLlmSettings(payload: LlmSettingsUpdate): Promise<LlmSettingsDto> {
    const res = await fetch('/api/v1/llm-settings', {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to save LLM settings' }));
      throw new Error(err.detail || 'Failed to save LLM settings');
    }
    return res.json() as Promise<LlmSettingsDto>;
  },

  async testLlmSettings(): Promise<LlmSettingsTestResponse> {
    const res = await fetch('/api/v1/llm-settings/test', { method: 'POST' });
    if (!res.ok) throw new Error('Failed to test LLM settings');
    return res.json() as Promise<LlmSettingsTestResponse>;
  },

  async createPlacementSession(requestedCount: number): Promise<PlacementSessionDto> {
    const res = await fetch('/api/v1/placement-sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ requested_count: requestedCount }),
    });
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      const detail = (errBody as ApiErrorBody).detail;
      const message = typeof detail === 'object' ? detail?.message : undefined;
      throw apiError(message || 'Failed to create placement session', res.status, detail);
    }
    return res.json() as Promise<PlacementSessionDto>;
  },

  async getActivePlacementSession(): Promise<PlacementSessionDto | null> {
    const res = await fetch('/api/v1/placement-sessions/active');
    if (res.status === 404) return null;
    if (!res.ok) throw new Error('Failed to fetch active session');
    return res.json() as Promise<PlacementSessionDto>;
  },

  async abandonPlacementSession(sessionId: string) {
    const res = await fetch(`/api/v1/placement-sessions/${sessionId}/abandon`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error('Failed to abandon placement session');
    return res.json();
  },

  async getAuditQuestions(sessionId: string, checkpoint: number) {
    const res = await fetch(`/api/v1/placement-sessions/${sessionId}/audit/${checkpoint}`);
    if (!res.ok) throw new Error('Failed to fetch audit questions');
    return res.json();
  },

  async answerAuditQuestion(sessionId: string, checkpoint: number, auditItemId: string, payload: { selected_option_id: string; idempotency_key: string; answered_at: string }) {
    const res = await fetch(`/api/v1/placement-sessions/${sessionId}/audit/${checkpoint}/answer/${auditItemId}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) throw new Error('Failed to answer audit question');
    return res.json();
  },

  async batchPlacementEvents(sessionId: string, events: PlacementEventPayload[]) {
    const res = await fetch(`/api/v1/placement-sessions/${sessionId}/events/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ events }),
    });
    if (!res.ok) throw new Error('Failed to sync batch');
    return res.json();
  },

  async getPlacementChunk(sessionId: string, chunkNumber: number) {
    const res = await fetch(`/api/v1/placement-sessions/${sessionId}/chunks/${chunkNumber}`);
    if (!res.ok) throw new Error('Failed to fetch placement chunk');
    const raw: PlacementCardDto[] = await res.json();
    return raw.map(toPlacementCard);
  },

  // Vocabulary import API helper methods
  async uploadImportFile(file: File) {
    const formData = new FormData();
    formData.append('file', file);
    const res = await fetch('/api/v1/imports/upload', {
      method: 'POST',
      body: formData,
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to upload vocabulary file' }));
      throw new Error(err.detail || 'Failed to upload vocabulary file');
    }
    return res.json();
  },

  async analyzeImport(id: string, mapping: ImportFieldMapping, deckSelection: string) {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    const res = await fetch(`/api/v1/imports/${id}/analyze`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ field_mapping: mapping, deck_selection: deckSelection }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to analyze import' }));
      throw new Error(err.detail || 'Failed to analyze import');
    }
    return res.json();
  },

  async getImportRows(id: string, page: number = 1, limit: number = 20, classification?: string, action?: string) {
    let url = `/api/v1/imports/${id}/rows?page=${page}&limit=${limit}`;
    if (classification) url += `&classification=${encodeURIComponent(classification)}`;
    if (action) url += `&action=${encodeURIComponent(action)}`;
    
    const res = await fetch(url);
    if (!res.ok) throw new Error('Failed to fetch import rows');
    return res.json();
  },

  async commitImport(id: string, idempotencyKey: string, requestHash: string) {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' };
    const res = await fetch(`/api/v1/imports/${id}/commit`, {
      method: 'POST',
      headers,
      body: JSON.stringify({ idempotency_key: idempotencyKey, request_hash: requestHash }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to commit import' }));
      throw new Error(err.detail || 'Failed to commit import');
    }
    return res.json();
  },

  async getStudyPlan(): Promise<StudyPlanInfo> {
    const res = await fetch('/api/v1/study-sessions/plan');
    if (!res.ok) throw new Error('Failed to fetch study plan');
    return res.json() as Promise<StudyPlanInfo>;
  },

  async createStudySession(
    requestedSize: number,
    mode: string,
    activationBudget: number | null = null,
  ): Promise<StudySessionDto> {
    const res = await fetch('/api/v1/study-sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ requested_size: requestedSize, mode, activation_budget: activationBudget }),
    });
    if (!res.ok) {
      const errBody = await res.json().catch(() => ({}));
      const detail = (errBody as ApiErrorBody).detail;
      const message = typeof detail === 'object' ? detail?.message : undefined;
      throw apiError(message || 'Failed to create study session', res.status, detail);
    }
    return res.json() as Promise<StudySessionDto>;
  },

  async getActiveStudySession(): Promise<StudySessionDto | null> {
    const res = await fetch('/api/v1/study-sessions/active');
    if (res.status === 404) return null;
    if (!res.ok) throw new Error('Failed to fetch active study session');
    return res.json() as Promise<StudySessionDto>;
  },

  async getStudySession(sessionId: string): Promise<StudySessionDto> {
    const res = await fetch(`/api/v1/study-sessions/${sessionId}`);
    if (!res.ok) throw new Error('Failed to fetch study session');
    return res.json() as Promise<StudySessionDto>;
  },

  async getStudySessionItems(sessionId: string): Promise<StudySessionItemDto[]> {
    const res = await fetch(`/api/v1/study-sessions/${sessionId}/items`);
    if (!res.ok) throw new Error('Failed to fetch study session items');
    return res.json() as Promise<StudySessionItemDto[]>;
  },

  async batchTypedStudyAnswers(
    sessionId: string,
    answers: TypedStudyAnswerPayload[],
  ): Promise<BatchAcceptedResponse> {
    const res = await fetch(`/api/v1/study-sessions/${sessionId}/typed-answers/batch`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ answers }),
    });
    if (!res.ok) throw new Error('Failed to sync typed study answers');
    return res.json() as Promise<BatchAcceptedResponse>;
  },

  async adjudicateStudySession(sessionId: string) {
    const res = await fetch(`/api/v1/study-sessions/${sessionId}/adjudicate`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error('Failed to adjudicate study session');
    return res.json();
  },

  async retryStudyAdjudication(sessionId: string) {
    const res = await fetch(`/api/v1/study-sessions/${sessionId}/adjudication-retry`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error('Failed to retry study adjudication');
    return res.json();
  },

  async getStudyAdjudicationStatus(sessionId: string) {
    const res = await fetch(`/api/v1/study-sessions/${sessionId}/adjudication-status`);
    if (!res.ok) throw new Error('Failed to fetch adjudication status');
    return res.json();
  },

  async abandonStudySession(sessionId: string) {
    const res = await fetch(`/api/v1/study-sessions/${sessionId}/abandon`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error('Failed to abandon study session');
    return res.json();
  },

  async finishStudySession(sessionId: string) {
    const res = await fetch(`/api/v1/study-sessions/${sessionId}/finish`, {
      method: 'POST',
    });
    if (!res.ok) throw new Error('Failed to finish study session');
    return res.json();
  },

  // Mistakes & Confusion APIs
  async getMistakes(params: { days?: number | null; deckId?: string | null; rating?: string | null; repeatedLapses?: boolean | null; page: number; limit: number }) {
    let url = `/api/v1/mistakes?page=${params.page}&limit=${params.limit}`;
    if (params.days) url += `&days=${params.days}`;
    if (params.deckId) url += `&deck_id=${encodeURIComponent(params.deckId)}`;
    if (params.rating && params.rating !== 'all') url += `&rating=${encodeURIComponent(params.rating)}`;
    if (params.repeatedLapses) url += `&repeated_lapses=true`;
    
    const res = await fetch(url);
    if (!res.ok) throw new Error('Failed to fetch mistakes');
    return res.json();
  },

  async getConfusions(params: { orderBy: string; page: number; limit: number }) {
    const url = `/api/v1/confusions?order_by=${params.orderBy}&page=${params.page}&limit=${params.limit}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error('Failed to fetch confusions');
    return res.json();
  },

  // Export API
  async exportData(params: { filterType: string; deckId?: string | null; limit?: number | null; format: string }) {
    const res = await fetch('/api/v1/exports', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        filter_type: params.filterType,
        deck_id: params.deckId,
        limit: params.limit,
        format: params.format
      })
    });
    if (!res.ok) throw new Error('Failed to export data');
    return res.json();
  },

  async resetProgress(confirm = 'RESET') {
    const res = await fetch('/api/v1/maintenance/reset-progress', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ confirm }),
    });
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: 'Failed to reset progress' }));
      throw new Error(err.detail || 'Failed to reset progress');
    }
    return res.json();
  },

};
