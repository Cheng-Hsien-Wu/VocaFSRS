from pydantic import BaseModel, ConfigDict, Field, model_validator
from typing import Any, Literal, List, Optional
from datetime import datetime

from app.constants import (
    PlacementAnswer,
    PlacementAuditStatus,
    PlacementEventType,
    PlacementSessionStatus,
)

class PlacementSessionCreate(BaseModel):
    requested_count: int = Field(gt=0, le=10_000)
    deck_ids: Optional[List[str]] = None

class ManifestItem(BaseModel):
    position: int
    card_id: str

class PlacementSessionResponse(BaseModel):
    id: str
    requested_count: int
    status: PlacementSessionStatus
    manifest_json: str
    started_at: datetime
    current_position: int
    checkpoint_size: int

class PlacementEventSchema(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    event_type: PlacementEventType = PlacementEventType.ANSWER
    position: int = Field(ge=0)
    card_id: Optional[str] = Field(default=None, max_length=128)
    result: Optional[PlacementAnswer] = None
    target_event_id: Optional[str] = Field(default=None, max_length=128)
    problematic_reason: Optional[str] = Field(default=None, max_length=500)
    answered_at: datetime

    @model_validator(mode="after")
    def validate_event_shape(self):
        if self.event_type in (PlacementEventType.ANSWER, PlacementEventType.AUDIT_RECLASSIFY):
            if not self.card_id or not self.result:
                raise ValueError("answer events require card_id and result")
        if self.event_type == PlacementEventType.UNDO and not self.target_event_id:
            raise ValueError("undo events require target_event_id")
        return self

class BatchEventsRequest(BaseModel):
    events: List[PlacementEventSchema] = Field(max_length=500)

class BatchEventsResponse(BaseModel):
    accepted: List[str]
    duplicates: List[str]

class CardResponse(BaseModel):
    id: str
    english: str
    chinese_meaning: str
    part_of_speech: Optional[str] = None
    example_sentence: Optional[str] = None
    example_translation: Optional[str] = None
    source: Optional[str] = None
    
    model_config = ConfigDict(from_attributes=True)


class AuditAnswerRequest(BaseModel):
    selected_option_id: str = Field(min_length=1, max_length=128)
    idempotency_key: str = Field(min_length=1, max_length=128)
    answered_at: datetime


class AuditQuestionResponse(BaseModel):
    audit_item_id: str
    card_id: str
    english: str
    options: list[dict[str, Any]]
    sample_batch: int
    answered: bool
    selected_option_id: Optional[str] = None
    is_correct: Optional[bool] = None


class AuditQuestionsResponse(BaseModel):
    status: PlacementAuditStatus
    checkpoint: int
    questions: list[AuditQuestionResponse]


LlmProvider = Literal["auto", "gemini", "openrouter", "openai_compatible"]


class LlmSettingsResponse(BaseModel):
    provider: LlmProvider
    model: Optional[str] = None
    base_url: Optional[str] = None
    timeout_seconds: int
    api_key_configured: bool
    api_key_source: Literal["local", "environment", "none"]
    effective_model: str


class LlmSettingsUpdate(BaseModel):
    provider: LlmProvider
    model: Optional[str] = Field(default=None, max_length=200)
    base_url: Optional[str] = Field(default=None, max_length=500)
    api_key: Optional[str] = Field(default=None, max_length=4000)
    clear_api_key: bool = False
    timeout_seconds: int = Field(default=45, ge=5, le=180)


class LlmSettingsTestResponse(BaseModel):
    ok: bool
    provider: Optional[str] = None
    model: Optional[str] = None
    error: Optional[str] = None
