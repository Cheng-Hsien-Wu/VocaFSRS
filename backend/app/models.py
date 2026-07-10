from sqlalchemy import Column, Integer, String, Text, Boolean, DateTime, ForeignKey, Float, Index, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.constants import (
    AdjudicationStatus,
    CardQualityStatus,
    DataQualityStatus,
    DEFAULT_LLM_BATCH_SIZE,
    DEFAULT_LLM_MAX_CONCURRENCY,
    DEFAULT_MINIMUM_DUE_COUNT,
    PlacementAuditStatus,
    PlacementSessionStatus,
    StudyItemSyncStatus,
    QueueStatus,
    StudySessionStatus,
)

class Deck(Base):
    __tablename__ = "decks"
    id = Column(String, primary_key=True)
    name = Column(String, nullable=False)
    description = Column(Text, nullable=True)
    deck_type = Column(String, nullable=True)
    enabled = Column(Boolean, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class Card(Base):
    __tablename__ = "cards"
    id = Column(String, primary_key=True)
    english = Column(String, nullable=False)
    english_normalized = Column(String, nullable=False)
    chinese_meaning = Column(String, nullable=False)
    chinese_normalized = Column(String, nullable=False)
    part_of_speech = Column(String, nullable=True)
    sense_hint = Column(String, nullable=True)
    example_sentence = Column(Text, nullable=True)
    example_translation = Column(Text, nullable=True)
    source = Column(String, nullable=True)
    active = Column(Boolean, default=True, index=True)
    fingerprint = Column(String, nullable=True, index=True)
    fingerprint_version = Column(Integer, default=1)
    study_eligible = Column(Boolean, default=True, index=True)
    data_quality_status = Column(String, default=CardQualityStatus.CLEAN, index=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class DeckCard(Base):
    __tablename__ = "deck_cards"
    deck_id = Column(String, ForeignKey("decks.id"), primary_key=True)
    card_id = Column(String, ForeignKey("cards.id"), primary_key=True)
    source_import_id = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

class PlacementSession(Base):
    __tablename__ = "placement_sessions"
    id = Column(String, primary_key=True)
    requested_count = Column(Integer, nullable=False)
    started_at = Column(DateTime, server_default=func.now())
    finished_at = Column(DateTime, nullable=True)
    current_position = Column(Integer, default=0)
    status = Column(String, default=PlacementSessionStatus.ACTIVE) # active, checkpoint_pending, audit_active, paused, completed, abandoned
    manifest_json = Column(JSON, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class PlacementItem(Base):
    __tablename__ = "placement_items"
    id = Column(String, primary_key=True)
    placement_session_id = Column(String, ForeignKey("placement_sessions.id"))
    position = Column(Integer, nullable=False)
    card_id = Column(String, ForeignKey("cards.id"), nullable=False)
    placement_result = Column(String, nullable=True) # known, fuzzy, unknown, problematic
    problematic_reason = Column(String, nullable=True)
    answered_at = Column(DateTime, nullable=True)
    idempotency_key = Column(String, nullable=True, unique=True)
    undone = Column(Boolean, default=False, index=True)
    audit_reclassified = Column(Boolean, default=False, index=True)
    
    __table_args__ = (
        Index("ix_placement_items_session_position", "placement_session_id", "position"),
    )

class PlacementEvent(Base):
    __tablename__ = "placement_events"
    id = Column(String, primary_key=True)
    session_id = Column(String, ForeignKey("placement_sessions.id"), nullable=False)
    event_type = Column(String, nullable=False) # answer, undo, audit_reclassify
    position = Column(Integer, nullable=False)
    card_id = Column(String, ForeignKey("cards.id"), nullable=True)
    result = Column(String, nullable=True) # known, fuzzy, unknown, problematic
    problematic_reason = Column(String, nullable=True)
    target_event_id = Column(String, nullable=True) # for undo / audit_reclassify
    idempotency_key = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_placement_events_session_pos", "session_id", "position"),
        Index("ix_placement_events_idempotency", "idempotency_key", unique=True),
    )

class PlacementAudit(Base):
    __tablename__ = "placement_audits"
    id = Column(String, primary_key=True)
    placement_session_id = Column(String, ForeignKey("placement_sessions.id"), nullable=False)
    checkpoint = Column(Integer, nullable=False) # 100, 200, 300...
    status = Column(String, default=PlacementAuditStatus.ACTIVE) # active, completed, skipped
    error_rate = Column(Float, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_placement_audits_session_checkpoint", "placement_session_id", "checkpoint", unique=True),
    )

class PlacementAuditItem(Base):
    __tablename__ = "placement_audit_items"
    id = Column(String, primary_key=True)
    placement_audit_id = Column(String, ForeignKey("placement_audits.id"), nullable=False)
    card_id = Column(String, ForeignKey("cards.id"), nullable=False)
    sample_batch = Column(Integer, default=1) # 1 or 2
    options_json = Column(JSON, nullable=False) # list of 4 options: {"card_id": "...", "chinese": "..."}
    correct_option_id = Column(String, nullable=False)
    resolved_result = Column(String, nullable=True) # correct, incorrect, problematic

    __table_args__ = (
        Index("ix_placement_audit_items_audit_batch", "placement_audit_id", "sample_batch"),
    )

class PlacementAuditEvent(Base):
    __tablename__ = "placement_audit_events"
    id = Column(String, primary_key=True)
    placement_audit_item_id = Column(String, ForeignKey("placement_audit_items.id"), nullable=False)
    selected_option_id = Column(String, nullable=False) # option card ID selected, or "unknown" / "problematic"
    is_correct = Column(Boolean, nullable=True)
    idempotency_key = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_placement_audit_events_idempotency", "idempotency_key", unique=True),
        Index("ix_placement_audit_events_item", "placement_audit_item_id", unique=True),
    )

class ActivationQueue(Base):
    __tablename__ = "activation_queue"
    id = Column(String, primary_key=True)
    card_id = Column(String, ForeignKey("cards.id"), nullable=False)
    activation_type = Column(String, nullable=False) # learn_unknown, learn_fuzzy, verify_known
    priority = Column(Integer, default=0)
    available_at = Column(DateTime, server_default=func.now())
    activated_at = Column(DateTime, nullable=True)
    status = Column(String, default=QueueStatus.PENDING) # pending, activated, skipped
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_activation_queue_card_id", "card_id", unique=True),
    )

class ReviewState(Base):
    __tablename__ = "review_states"
    card_id = Column(String, ForeignKey("cards.id"), primary_key=True)
    state = Column(Integer, nullable=False) # FSRS state (New, Learning, Review, Relearning)
    step = Column(Integer, nullable=True) # FSRS learning/relearning step
    due = Column(DateTime, nullable=True, index=True)
    stability = Column(Float, nullable=False)
    difficulty = Column(Float, nullable=False)
    elapsed_days = Column(Integer, nullable=False)
    scheduled_days = Column(Integer, nullable=False)
    reps = Column(Integer, nullable=False)
    lapses = Column(Integer, nullable=False)
    last_review = Column(DateTime, nullable=True)
    scheduler_name = Column(String, nullable=True)
    scheduler_version = Column(String, nullable=True)
    parameters_version = Column(String, nullable=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class StudySession(Base):
    __tablename__ = "study_sessions"
    id = Column(String, primary_key=True)
    requested_size = Column(Integer, nullable=False)
    mode = Column(String, nullable=False) # fixed, timed
    started_at = Column(DateTime, server_default=func.now())
    finished_at = Column(DateTime, nullable=True)
    sync_status = Column(String, default=StudySessionStatus.ACTIVE)
    cards_answered = Column(Integer, default=0)
    again_count = Column(Integer, default=0)
    hard_count = Column(Integer, default=0)
    good_count = Column(Integer, default=0)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class StudyPlan(Base):
    __tablename__ = "study_plan"
    id = Column(String, primary_key=True)
    started_at = Column(DateTime, nullable=False)
    target_days = Column(Integer, default=30, nullable=False)
    target_end_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class SessionItem(Base):
    __tablename__ = "session_items"
    id = Column(String, primary_key=True)
    study_session_id = Column(String, ForeignKey("study_sessions.id"))
    position = Column(Integer, nullable=False)
    target_card_id = Column(String, ForeignKey("cards.id"), nullable=False)
    correct_option_card_id = Column(String, nullable=False)
    option_card_ids_json = Column(JSON, nullable=False)
    source_type = Column(String, nullable=True)
    answered_at = Column(DateTime, nullable=True)
    sync_status = Column(String, default=StudyItemSyncStatus.PENDING)
    idempotency_key = Column(String, nullable=True, unique=True)

class TypedStudyAnswer(Base):
    __tablename__ = "typed_study_answers"
    id = Column(String, primary_key=True)
    study_session_id = Column(String, ForeignKey("study_sessions.id"), nullable=False)
    session_item_id = Column(String, ForeignKey("session_items.id"), nullable=False)
    card_id = Column(String, ForeignKey("cards.id"), nullable=False)
    typed_answer = Column(String, nullable=False)
    expected_answer = Column(String, nullable=False)
    answered_at = Column(DateTime, nullable=False)
    adjudication_status = Column(String, default=AdjudicationStatus.PENDING, nullable=False)
    verdict = Column(String, nullable=True)
    rating = Column(String, nullable=True)
    reason = Column(String, nullable=True)
    confidence = Column(Float, nullable=True)
    provider = Column(String, nullable=True)
    model = Column(String, nullable=True)
    error_message = Column(String, nullable=True)
    next_due = Column(DateTime, nullable=True)
    adjudication_claim_token = Column(String, nullable=True)
    adjudication_claimed_at = Column(DateTime, nullable=True)
    idempotency_key = Column(String, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

    __table_args__ = (
        Index("ix_typed_answers_idempotency", "idempotency_key", unique=True),
        Index("ix_typed_answers_session_item", "study_session_id", "session_item_id", unique=True),
        Index("ix_typed_answers_session_status", "study_session_id", "adjudication_status"),
    )

class ReviewLog(Base):
    __tablename__ = "review_logs"
    id = Column(String, primary_key=True)
    card_id = Column(String, ForeignKey("cards.id"), nullable=False)
    study_session_id = Column(String, ForeignKey("study_sessions.id"), nullable=True)
    session_item_id = Column(String, ForeignKey("session_items.id"), nullable=True)
    selected_option_card_id = Column(String, nullable=True)
    correct_option_card_id = Column(String, nullable=True)
    was_correct = Column(Boolean, nullable=False)
    confidence = Column(String, nullable=True)
    rating = Column(Integer, nullable=False) # FSRS Rating
    previous_state_json = Column(JSON, nullable=True)
    next_state_json = Column(JSON, nullable=True)
    previous_due = Column(DateTime, nullable=True)
    next_due = Column(DateTime, nullable=True)
    reviewed_at = Column(DateTime, nullable=False, index=True)
    idempotency_key = Column(String, nullable=True, unique=True)
    created_at = Column(DateTime, server_default=func.now(), nullable=True)

class ReviewReminderState(Base):
    __tablename__ = "review_reminder_state"
    id = Column(String, primary_key=True)
    last_sent_at = Column(DateTime, nullable=True)
    last_sent_target_at = Column(DateTime, nullable=True)
    last_error = Column(Text, nullable=True)
    minimum_due_count = Column(Integer, nullable=False, default=DEFAULT_MINIMUM_DUE_COUNT)
    notification_armed = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class LlmSettings(Base):
    __tablename__ = "llm_settings"
    id = Column(String, primary_key=True)
    provider = Column(String, nullable=False, default="auto")
    model = Column(String, nullable=True)
    base_url = Column(Text, nullable=True)
    api_key = Column(Text, nullable=True)
    timeout_seconds = Column(Integer, nullable=True)
    fallback_routes_json = Column("fallback_providers_json", JSON, nullable=False, default=list)
    batch_size = Column(Integer, nullable=False, default=DEFAULT_LLM_BATCH_SIZE)
    max_concurrency = Column(Integer, nullable=False, default=DEFAULT_LLM_MAX_CONCURRENCY)
    created_at = Column(DateTime, server_default=func.now())
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class ConfusionCount(Base):
    __tablename__ = "confusion_counts"
    target_card_id = Column(String, ForeignKey("cards.id"), primary_key=True)
    selected_wrong_card_id = Column(String, ForeignKey("cards.id"), primary_key=True)
    occurrence_count = Column(Integer, default=1)
    last_occurred_at = Column(DateTime, server_default=func.now(), onupdate=func.now())

class ImportJob(Base):
    __tablename__ = "import_jobs"
    id = Column(String, primary_key=True)
    original_filename = Column(String, nullable=False)
    status = Column(String, nullable=False) # pending, committing, committed, failed
    detected_encoding = Column(String, nullable=False)
    field_mapping_json = Column(JSON, nullable=True)
    total_rows = Column(Integer, default=0)
    valid_rows = Column(Integer, default=0)
    invalid_rows = Column(Integer, default=0)
    new_cards = Column(Integer, default=0)
    linked_existing_cards = Column(Integer, default=0)
    skipped_duplicates = Column(Integer, default=0)
    conflict_count = Column(Integer, default=0)
    deck_selection = Column(String, nullable=True)
    idempotency_key = Column(String, nullable=True)
    request_hash = Column(String, nullable=True)
    expires_at = Column(DateTime, nullable=False)
    created_at = Column(DateTime, server_default=func.now())
    committed_at = Column(DateTime, nullable=True)
    summary_json = Column(JSON, nullable=True)

    __table_args__ = (
        Index("ix_import_jobs_idempotency", "idempotency_key", unique=True),
    )

class ImportRowResult(Base):
    __tablename__ = "import_row_results"
    id = Column(String, primary_key=True)
    import_job_id = Column(String, ForeignKey("import_jobs.id"), nullable=False)
    row_index = Column(Integer, nullable=False)
    original_row_data = Column(JSON, nullable=False)
    classification = Column(String, nullable=False) # exact_duplicate, same_term_variant, etc.
    action = Column(String, nullable=False) # skip, link, create, reject, flag_ambiguous
    message = Column(Text, nullable=True)
    card_id = Column(String, ForeignKey("cards.id"), nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    __table_args__ = (
        Index("ix_import_row_results_job_row", "import_job_id", "row_index"),
        Index("ix_import_row_results_job_classification", "import_job_id", "classification"),
        Index("ix_import_row_results_job_action", "import_job_id", "action"),
    )

class DataQualityIssue(Base):
    __tablename__ = "data_quality_issues"
    id = Column(String, primary_key=True)
    card_id = Column(String, ForeignKey("cards.id"), nullable=False)
    source = Column(String, nullable=False)
    issue_type = Column(String, nullable=False) # potential_ambiguity, typo, etc.
    note = Column(Text, nullable=True)
    status = Column(String, default=DataQualityStatus.OPEN) # open, resolved
    created_at = Column(DateTime, server_default=func.now())
    resolved_at = Column(DateTime, nullable=True)
