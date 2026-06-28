import uuid
from datetime import datetime, timezone
from typing import Optional

from fsrs import Card as FSRSCard, Rating as FSRSRating, Scheduler as FSRSScheduler, State as FSRSState
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import QueueStatus, StudyItemSyncStatus
from app.models import ActivationQueue, ReviewLog, ReviewState, SessionItem, StudySession
from app.services.time import to_utc_aware, to_utc_naive

SCHEDULER_NAME = "py-fsrs"
SCHEDULER_VERSION = "6.3.1"
SCHEDULER_PARAMETERS_VERSION = "default"
LEARNING_STEP_TWO_SECONDS = 9 * 60


def _infer_missing_step(review_state: ReviewState) -> int | None:
    if review_state.step is not None:
        return review_state.step
    if review_state.state not in (FSRSState.Learning.value, FSRSState.Relearning.value):
        return None
    if not review_state.due or not review_state.last_review:
        return 0
    scheduled_seconds = (review_state.due - review_state.last_review).total_seconds()
    return 1 if scheduled_seconds >= LEARNING_STEP_TWO_SECONDS else 0


async def apply_fsrs_rating(
    db: AsyncSession,
    session: StudySession,
    item: SessionItem,
    card_id: str,
    rating_name: str,
    reviewed_at: datetime,
    idempotency_key: str,
    selected_option_card_id: Optional[str] = None,
    confidence: Optional[str] = None,
    update_session_counts: bool = True,
) -> datetime:
    reviewed_at_aware = to_utc_aware(reviewed_at)
    reviewed_at_naive = to_utc_naive(reviewed_at)

    log_q = await db.execute(select(ReviewLog).where(ReviewLog.idempotency_key == idempotency_key))
    existing_log = log_q.scalars().first()
    if existing_log:
        return existing_log.next_due

    state_q = await db.execute(select(ReviewState).where(ReviewState.card_id == card_id))
    review_state = state_q.scalars().first()

    aq_q = await db.execute(
        select(ActivationQueue).where(
            ActivationQueue.card_id == card_id,
            ActivationQueue.status == QueueStatus.PENDING,
        )
    )
    for aq_item in aq_q.scalars().all():
        aq_item.status = QueueStatus.ACTIVATED
        aq_item.activated_at = reviewed_at_naive

    is_new_state = False
    if not review_state:
        is_new_state = True
        prev_state_val = 1
        prev_step = None
        prev_stability = None
        prev_difficulty = None
        prev_due = None
        prev_last_review = None
        prev_reps = 0
        prev_lapses = 0
    else:
        prev_state_val = review_state.state
        prev_step = _infer_missing_step(review_state)
        prev_stability = review_state.stability
        prev_difficulty = review_state.difficulty
        prev_due = review_state.due
        prev_last_review = review_state.last_review
        prev_reps = review_state.reps
        prev_lapses = review_state.lapses

    fsrs_card = FSRSCard(
        state=FSRSState(prev_state_val),
        step=prev_step,
        stability=prev_stability,
        difficulty=prev_difficulty,
        last_review=prev_last_review.replace(tzinfo=timezone.utc) if prev_last_review else None,
        due=prev_due.replace(tzinfo=timezone.utc) if prev_due else None,
    )

    rating_map = {
        "Again": FSRSRating.Again,
        "Hard": FSRSRating.Hard,
        "Good": FSRSRating.Good,
    }
    if rating_name not in rating_map:
        raise ValueError(f"Invalid FSRS rating: {rating_name}")
    rating = rating_map[rating_name]
    new_fsrs_card, _ = FSRSScheduler().review_card(
        fsrs_card,
        rating,
        review_datetime=reviewed_at_aware,
    )

    elapsed = (reviewed_at_naive - prev_last_review).days if prev_last_review else 0
    scheduled = (new_fsrs_card.due - new_fsrs_card.last_review).days if new_fsrs_card.due and new_fsrs_card.last_review else 0
    new_reps = prev_reps + 1
    is_review_lapse = (
        rating_name == "Again"
        and prev_state_val == FSRSState.Review.value
    )
    new_lapses = prev_lapses + 1 if is_review_lapse else prev_lapses
    due_naive = new_fsrs_card.due.astimezone(timezone.utc).replace(tzinfo=None)
    lr_naive = new_fsrs_card.last_review.astimezone(timezone.utc).replace(tzinfo=None)

    if is_new_state:
        review_state = ReviewState(
            card_id=card_id,
            state=new_fsrs_card.state.value,
            step=new_fsrs_card.step,
            due=due_naive,
            stability=new_fsrs_card.stability,
            difficulty=new_fsrs_card.difficulty,
            elapsed_days=elapsed,
            scheduled_days=scheduled,
            reps=new_reps,
            lapses=new_lapses,
            last_review=lr_naive,
            scheduler_name=SCHEDULER_NAME,
            scheduler_version=SCHEDULER_VERSION,
            parameters_version=SCHEDULER_PARAMETERS_VERSION,
        )
        db.add(review_state)
    else:
        review_state.state = new_fsrs_card.state.value
        review_state.step = new_fsrs_card.step
        review_state.due = due_naive
        review_state.stability = new_fsrs_card.stability
        review_state.difficulty = new_fsrs_card.difficulty
        review_state.elapsed_days = elapsed
        review_state.scheduled_days = scheduled
        review_state.reps = new_reps
        review_state.lapses = new_lapses
        review_state.last_review = lr_naive

    db.add(ReviewLog(
        id=str(uuid.uuid4()),
        card_id=card_id,
        study_session_id=session.id,
        session_item_id=item.id,
        selected_option_card_id=selected_option_card_id,
        correct_option_card_id=card_id,
        was_correct=(rating_name in ["Good", "Hard"]),
        confidence=confidence,
        rating=rating.value,
        previous_state_json={
            "state": prev_state_val,
            "step": prev_step,
            "stability": prev_stability,
            "difficulty": prev_difficulty,
            "due": prev_due.isoformat() if prev_due else None,
            "last_review": prev_last_review.isoformat() if prev_last_review else None,
        },
        next_state_json={
            "state": new_fsrs_card.state.value,
            "step": new_fsrs_card.step,
            "stability": new_fsrs_card.stability,
            "difficulty": new_fsrs_card.difficulty,
            "due": new_fsrs_card.due.isoformat() if new_fsrs_card.due else None,
            "last_review": new_fsrs_card.last_review.isoformat() if new_fsrs_card.last_review else None,
        },
        previous_due=prev_due,
        next_due=due_naive,
        reviewed_at=reviewed_at_naive,
        idempotency_key=idempotency_key,
    ))

    item.answered_at = reviewed_at_naive
    item.sync_status = StudyItemSyncStatus.SYNCED
    item.idempotency_key = idempotency_key

    if update_session_counts:
        if rating_name == "Again":
            session.again_count += 1
        elif rating_name == "Hard":
            session.hard_count += 1
        elif rating_name == "Good":
            session.good_count += 1

    return due_naive
