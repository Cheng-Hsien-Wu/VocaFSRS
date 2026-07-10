from enum import StrEnum

APP_NAME = "VocaFSRS"
DEFAULT_DECK_NAME = "Imported Vocabulary"
DEFAULT_STUDY_TARGET_DAYS = 30
PLACEMENT_CHECKPOINT_SIZE = 100
DEFAULT_MINIMUM_DUE_COUNT = 10
DEFAULT_LLM_BATCH_SIZE = 10
DEFAULT_LLM_MAX_CONCURRENCY = 2


class PlacementSessionStatus(StrEnum):
    ACTIVE = "active"
    CHECKPOINT_PENDING = "checkpoint_pending"
    AUDIT_ACTIVE = "audit_active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class PlacementGateStatus(StrEnum):
    NO_CARDS = "no_cards"
    REQUIRED = "required"
    IN_PROGRESS = "in_progress"
    COMPLETE = "complete"


class PlacementAnswer(StrEnum):
    KNOWN = "known"
    FUZZY = "fuzzy"
    UNKNOWN = "unknown"
    PROBLEMATIC = "problematic"


class PlacementProjectionResult(StrEnum):
    KNOWN = PlacementAnswer.KNOWN
    FUZZY = PlacementAnswer.FUZZY
    UNKNOWN = PlacementAnswer.UNKNOWN
    PROBLEMATIC = PlacementAnswer.PROBLEMATIC
    SKIPPED = "skipped"


class PlacementEventType(StrEnum):
    ANSWER = "answer"
    UNDO = "undo"
    AUDIT_RECLASSIFY = "audit_reclassify"


class PlacementAuditStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    SKIPPED = "skipped"


class PlacementAuditResult(StrEnum):
    CORRECT = "correct"
    INCORRECT = "incorrect"
    PROBLEMATIC = "problematic"


class AuditSpecialOption(StrEnum):
    UNKNOWN = "unknown"
    PROBLEMATIC = "problematic"


class ActivationType(StrEnum):
    LEARN_UNKNOWN = "learn_unknown"
    LEARN_FUZZY = "learn_fuzzy"
    VERIFY_KNOWN = "verify_known"


class QueueStatus(StrEnum):
    PENDING = "pending"
    ACTIVATED = "activated"
    SKIPPED = "skipped"


class StudySessionStatus(StrEnum):
    ACTIVE = "active"
    PAUSED = "paused"
    COMPLETED = "completed"
    ABANDONED = "abandoned"


class StudyMode(StrEnum):
    FIXED = "fixed"
    TIMED = "timed"


class StudyAvailabilityState(StrEnum):
    AVAILABLE_DUE = "available_due"
    AVAILABLE_NEW = "available_new"
    WAITING = "waiting"
    PENDING_ADJUDICATION = "pending_adjudication"
    NOT_STARTED = "not_started"
    EMPTY = "empty"
    DECK_SCOPE_REQUIRED = "deck_scope_required"
    PLACEMENT_REQUIRED = "placement_required"
    NO_CARDS = "no_cards"


class StudyItemSyncStatus(StrEnum):
    PENDING = "pending"
    PENDING_ADJUDICATION = "pending_adjudication"
    SYNCED = "synced"


class AdjudicationStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    SUCCEEDED = "succeeded"
    FAILED = "failed"


class ImportJobStatus(StrEnum):
    PENDING = "pending"
    COMMITTING = "committing"
    COMMITTED = "committed"
    FAILED = "failed"


class DataQualityStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"


class CardQualityStatus(StrEnum):
    CLEAN = "clean"
    AMBIGUOUS = "ambiguous"
    PROBLEMATIC = "problematic"


ACTIVE_PLACEMENT_STATUSES = (
    PlacementSessionStatus.ACTIVE,
    PlacementSessionStatus.CHECKPOINT_PENDING,
    PlacementSessionStatus.AUDIT_ACTIVE,
    PlacementSessionStatus.PAUSED,
)
TERMINAL_PLACEMENT_STATUSES = (
    PlacementSessionStatus.ABANDONED,
    PlacementSessionStatus.COMPLETED,
)
ACTIVE_STUDY_STATUSES = (StudySessionStatus.ACTIVE, StudySessionStatus.PAUSED)
BLOCKING_ADJUDICATION_STATUSES = (
    AdjudicationStatus.PENDING,
    AdjudicationStatus.PROCESSING,
    AdjudicationStatus.FAILED,
)
CLAIMABLE_ADJUDICATION_STATUSES = (AdjudicationStatus.PENDING,)
RETRYABLE_ADJUDICATION_STATUSES = (AdjudicationStatus.FAILED,)
FINAL_ADJUDICATION_STATUS = AdjudicationStatus.SUCCEEDED
