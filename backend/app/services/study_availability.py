from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import ActivationQueue, Card, DeckCard, ReviewState, SessionItem, StudySession, TypedStudyAnswer
from app.constants import (
    ACTIVE_STUDY_STATUSES,
    BLOCKING_ADJUDICATION_STATUSES,
    ActivationType,
    CardQualityStatus,
    QueueStatus,
    StudyAvailabilityState,
    StudySessionStatus,
)

AvailabilityState = StudyAvailabilityState


@dataclass(frozen=True)
class StudyCandidate:
    card: Card
    source_type: str


@dataclass(frozen=True)
class StudyAvailability:
    due_cards: list[Card]
    new_candidates: list[StudyCandidate]
    next_review_due_at: datetime | None
    pending_adjudication_count: int
    active_session_blocked_count: int
    availability_state: AvailabilityState
    due_count_value: int | None = None
    pending_new_count_value: int | None = None

    @property
    def due_count(self) -> int:
        if self.due_count_value is not None:
            return self.due_count_value
        return len(self.due_cards)

    @property
    def pending_new_count(self) -> int:
        if self.pending_new_count_value is not None:
            return self.pending_new_count_value
        return len(self.new_candidates)

    @property
    def available_now_count(self) -> int:
        return self.due_count + self.pending_new_count


async def get_study_availability(
    db: AsyncSession,
    now_utc: datetime,
    deck_ids: list[str] | None = None,
    has_study_plan: bool = False,
    selection_limit: int | None = None,
) -> StudyAvailability:
    active_card_ids = await _active_unanswered_card_ids(db)
    pending_adjudication_card_ids = await _pending_adjudication_card_ids(db)
    excluded_card_ids = active_card_ids | pending_adjudication_card_ids

    due_count = await _due_count(db, now_utc, deck_ids, excluded_card_ids)
    due_cards = [] if selection_limit == 0 else await _due_cards(db, now_utc, deck_ids, excluded_card_ids, selection_limit)
    due_exclusions = {c.id for c in due_cards} | excluded_card_ids
    new_count = await _new_count(db, now_utc, deck_ids, excluded_card_ids)
    new_limit = None if selection_limit is None else max(selection_limit - len(due_cards), 0)
    new_candidates = [] if selection_limit == 0 else await _new_candidates(db, now_utc, deck_ids, due_exclusions, new_limit)
    next_review_due_at = await _next_review_due_at(db, now_utc, deck_ids, excluded_card_ids)
    pending_adjudication_count = len(pending_adjudication_card_ids)

    if pending_adjudication_count > 0:
        availability_state = StudyAvailabilityState.PENDING_ADJUDICATION
    elif due_count:
        availability_state = StudyAvailabilityState.AVAILABLE_DUE
    elif new_count:
        availability_state = StudyAvailabilityState.AVAILABLE_NEW
    elif next_review_due_at:
        availability_state = StudyAvailabilityState.WAITING
    elif not has_study_plan:
        availability_state = StudyAvailabilityState.NOT_STARTED
    else:
        availability_state = StudyAvailabilityState.EMPTY

    return StudyAvailability(
        due_cards=due_cards,
        new_candidates=new_candidates,
        next_review_due_at=next_review_due_at,
        pending_adjudication_count=pending_adjudication_count,
        active_session_blocked_count=len(active_card_ids),
        availability_state=availability_state,
        due_count_value=due_count,
        pending_new_count_value=new_count,
    )


async def _active_unanswered_card_ids(db: AsyncSession) -> set[str]:
    sessions_q = await db.execute(
        select(StudySession.id).where(StudySession.sync_status.in_(ACTIVE_STUDY_STATUSES))
    )
    session_ids = [row[0] for row in sessions_q.all()]
    if not session_ids:
        return set()
    items_q = await db.execute(
        select(SessionItem.target_card_id).where(
            SessionItem.study_session_id.in_(session_ids),
            SessionItem.answered_at.is_(None),
        )
    )
    return {row[0] for row in items_q.all()}


async def _pending_adjudication_card_ids(db: AsyncSession) -> set[str]:
    answers_q = await db.execute(
        select(TypedStudyAnswer.card_id)
        .join(StudySession, StudySession.id == TypedStudyAnswer.study_session_id)
        .where(
            StudySession.sync_status != StudySessionStatus.ABANDONED,
            TypedStudyAnswer.adjudication_status.in_(BLOCKING_ADJUDICATION_STATUSES),
        )
    )
    return {row[0] for row in answers_q.all()}


def _apply_exclusions(stmt, excluded_card_ids: set[str]):
    if excluded_card_ids:
        stmt = stmt.where(Card.id.not_in(excluded_card_ids))
    return stmt


async def _due_count(db: AsyncSession, now_utc: datetime, deck_ids: list[str] | None, excluded_card_ids: set[str]) -> int:
    stmt = select(func.count(func.distinct(Card.id))).join(ReviewState, ReviewState.card_id == Card.id).where(
        Card.active == True,
        Card.study_eligible == True,
        Card.data_quality_status != CardQualityStatus.PROBLEMATIC,
        ReviewState.due <= now_utc,
    )
    if deck_ids:
        stmt = stmt.join(DeckCard, DeckCard.card_id == Card.id).where(DeckCard.deck_id.in_(deck_ids))
    stmt = _apply_exclusions(stmt, excluded_card_ids)
    res = await db.execute(stmt)
    return int(res.scalar() or 0)


async def _due_cards(
    db: AsyncSession,
    now_utc: datetime,
    deck_ids: list[str] | None,
    excluded_card_ids: set[str],
    limit: int | None,
) -> list[Card]:
    stmt = select(Card).join(ReviewState, ReviewState.card_id == Card.id).where(
        Card.active == True,
        Card.study_eligible == True,
        Card.data_quality_status != CardQualityStatus.PROBLEMATIC,
        ReviewState.due <= now_utc,
    )
    if deck_ids:
        stmt = stmt.join(DeckCard, DeckCard.card_id == Card.id).where(DeckCard.deck_id.in_(deck_ids))
    stmt = _apply_exclusions(stmt, excluded_card_ids).distinct().order_by(ReviewState.due.asc())
    if limit is not None:
        stmt = stmt.limit(limit)
    res = await db.execute(stmt)
    return list(res.scalars().all())


async def _new_count(
    db: AsyncSession,
    now_utc: datetime,
    deck_ids: list[str] | None,
    excluded_card_ids: set[str],
) -> int:
    stmt = (
        select(func.count(func.distinct(Card.id)))
        .join(ActivationQueue, ActivationQueue.card_id == Card.id)
        .outerjoin(ReviewState, ReviewState.card_id == Card.id)
        .where(
            Card.active == True,
            Card.study_eligible == True,
            Card.data_quality_status != CardQualityStatus.PROBLEMATIC,
            ReviewState.card_id.is_(None),
            ActivationQueue.status == QueueStatus.PENDING,
            ActivationQueue.available_at <= now_utc,
        )
    )
    if deck_ids:
        stmt = stmt.join(DeckCard, DeckCard.card_id == Card.id).where(DeckCard.deck_id.in_(deck_ids))
    stmt = _apply_exclusions(stmt, excluded_card_ids)
    res = await db.execute(stmt)
    return int(res.scalar() or 0)


async def _new_candidates(
    db: AsyncSession,
    now_utc: datetime,
    deck_ids: list[str] | None,
    excluded_card_ids: set[str],
    limit: int | None,
) -> list[StudyCandidate]:
    stmt = (
        select(Card, ActivationQueue.activation_type)
        .join(ActivationQueue, ActivationQueue.card_id == Card.id)
        .outerjoin(ReviewState, ReviewState.card_id == Card.id)
        .where(
            Card.active == True,
            Card.study_eligible == True,
            Card.data_quality_status != CardQualityStatus.PROBLEMATIC,
            ReviewState.card_id.is_(None),
            ActivationQueue.status == QueueStatus.PENDING,
            ActivationQueue.available_at <= now_utc,
        )
    )
    if deck_ids:
        stmt = stmt.join(DeckCard, DeckCard.card_id == Card.id).where(DeckCard.deck_id.in_(deck_ids))
    stmt = _apply_exclusions(stmt, excluded_card_ids).distinct().order_by(desc(ActivationQueue.priority), ActivationQueue.created_at.asc())
    if limit is not None:
        stmt = stmt.limit(limit)
    res = await db.execute(stmt)

    source_order = {
        ActivationType.LEARN_UNKNOWN: ActivationType.LEARN_UNKNOWN,
        ActivationType.LEARN_FUZZY: ActivationType.LEARN_FUZZY,
        ActivationType.VERIFY_KNOWN: ActivationType.VERIFY_KNOWN,
    }
    candidates: list[StudyCandidate] = []
    for card, activation_type in res.all():
        candidates.append(StudyCandidate(card=card, source_type=source_order.get(activation_type, "activation")))
    return candidates


async def _next_review_due_at(
    db: AsyncSession,
    now_utc: datetime,
    deck_ids: list[str] | None,
    excluded_card_ids: set[str],
) -> datetime | None:
    stmt = select(func.min(ReviewState.due)).join(Card, ReviewState.card_id == Card.id).where(
        Card.active == True,
        Card.study_eligible == True,
        Card.data_quality_status != CardQualityStatus.PROBLEMATIC,
        ReviewState.due > now_utc,
    )
    if deck_ids:
        stmt = stmt.join(DeckCard, DeckCard.card_id == Card.id).where(DeckCard.deck_id.in_(deck_ids))
    stmt = _apply_exclusions(stmt, excluded_card_ids)
    res = await db.execute(stmt)
    return res.scalar()
