import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from fastapi import HTTPException
from sqlalchemy import func, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import (
    ACTIVE_STUDY_STATUSES,
    AdjudicationStatus,
    CLAIMABLE_ADJUDICATION_STATUSES,
    RETRYABLE_ADJUDICATION_STATUSES,
    StudyItemSyncStatus,
    StudySessionStatus,
)
from app.llm_adjudicator import AdjudicationItem, AdjudicationUnavailable, adjudicate_answers
from app.models import Card, ReviewLog, SessionItem, StudySession, TypedStudyAnswer
from app.services.review_scheduler import apply_fsrs_rating
from app.services.time import to_utc_aware, to_utc_naive


FSRS_RATING_AGAIN = 1
FSRS_RATING_HARD = 2
FSRS_RATING_GOOD = 3
ADJUDICATION_LEASE_TIMEOUT = timedelta(minutes=15)


def ensure_session_accepts_answers(session: StudySession) -> None:
    if session.sync_status not in ACTIVE_STUDY_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "session_closed",
                "message": "Study session no longer accepts new answers",
                "status": session.sync_status,
            },
        )


def ensure_session_can_adjudicate(session: StudySession) -> None:
    if session.sync_status == StudySessionStatus.ABANDONED:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "session_abandoned",
                "message": "Abandoned study sessions cannot be adjudicated",
                "status": session.sync_status,
            },
        )


async def refresh_study_session_counts(db: AsyncSession, session: StudySession) -> None:
    await db.flush()

    rating_rows = await db.execute(
        select(ReviewLog.rating, func.count())
        .where(ReviewLog.study_session_id == session.id)
        .group_by(ReviewLog.rating)
    )
    counts = {rating: count for rating, count in rating_rows.all()}
    session.again_count = counts.get(FSRS_RATING_AGAIN, 0)
    session.hard_count = counts.get(FSRS_RATING_HARD, 0)
    session.good_count = counts.get(FSRS_RATING_GOOD, 0)

    answered_row = await db.execute(
        select(func.count())
        .select_from(SessionItem)
        .where(
            SessionItem.study_session_id == session.id,
            SessionItem.answered_at.is_not(None),
        )
    )
    session.cards_answered = answered_row.scalar_one()


async def adjudication_status_payload(db: AsyncSession, session_id: str) -> dict[str, Any]:
    answers_q = await db.execute(
        select(TypedStudyAnswer, Card)
        .join(Card, Card.id == TypedStudyAnswer.card_id)
        .where(TypedStudyAnswer.study_session_id == session_id)
        .order_by(TypedStudyAnswer.answered_at.asc(), TypedStudyAnswer.created_at.asc())
    )
    rows = answers_q.all()
    answers = [answer for answer, _ in rows]
    results = [
        {
            "id": answer.id,
            "session_item_id": answer.session_item_id,
            "card_id": answer.card_id,
            "english": card.english,
            "part_of_speech": card.part_of_speech,
            "typed_answer": answer.typed_answer,
            "expected_answer": answer.expected_answer,
            "status": answer.adjudication_status,
            "verdict": answer.verdict,
            "rating": answer.rating,
            "reason": answer.reason,
            "confidence": answer.confidence,
            "provider": answer.provider,
            "model": answer.model,
            "error_message": answer.error_message,
            "next_due": answer.next_due.isoformat() if answer.next_due else None,
        }
        for answer, card in rows
    ]
    return {
        "session_id": session_id,
        "pending": sum(1 for a in answers if a.adjudication_status == AdjudicationStatus.PENDING),
        "processing": sum(1 for a in answers if a.adjudication_status == AdjudicationStatus.PROCESSING),
        "succeeded": sum(1 for a in answers if a.adjudication_status == AdjudicationStatus.SUCCEEDED),
        "failed": sum(1 for a in answers if a.adjudication_status == AdjudicationStatus.FAILED),
        "total": len(answers),
        "results": results,
    }


async def record_typed_answers(
    db: AsyncSession,
    session_id: str,
    answers: list[Any],
) -> tuple[list[str], list[str], list[str]]:
    session = await _load_study_session(db, session_id)
    ensure_session_accepts_answers(session)

    accepted: list[str] = []
    duplicates: list[str] = []
    conflicts: list[str] = []
    seen_keys: set[str] = set()

    for answer in answers:
        if answer.idempotency_key in seen_keys:
            duplicates.append(answer.idempotency_key)
            continue
        seen_keys.add(answer.idempotency_key)

        collision = await _typed_answer_collision(
            db,
            session_id,
            answer.session_item_id,
            answer.idempotency_key,
        )
        if collision:
            (duplicates if collision == "duplicate" else conflicts).append(answer.idempotency_key)
            continue

        card = await db.get(Card, answer.card_id)
        if not card:
            continue

        answered_naive = to_utc_naive(answer.answered_at)
        try:
            async with db.begin_nested():
                claim = await db.execute(
                    update(SessionItem)
                    .where(
                        SessionItem.id == answer.session_item_id,
                        SessionItem.study_session_id == session_id,
                        SessionItem.target_card_id == answer.card_id,
                        SessionItem.answered_at.is_(None),
                    )
                    .values(
                        answered_at=answered_naive,
                        sync_status=StudyItemSyncStatus.PENDING_ADJUDICATION,
                        idempotency_key=answer.idempotency_key,
                    )
                )
                if claim.rowcount != 1:
                    conflicts.append(answer.idempotency_key)
                    continue

                db.add(
                    TypedStudyAnswer(
                        id=str(uuid.uuid4()),
                        study_session_id=session_id,
                        session_item_id=answer.session_item_id,
                        card_id=answer.card_id,
                        typed_answer=answer.typed_answer.strip(),
                        expected_answer=card.chinese_meaning,
                        answered_at=answered_naive,
                        adjudication_status=AdjudicationStatus.PENDING,
                        idempotency_key=answer.idempotency_key,
                    )
                )
                await db.flush()
        except IntegrityError:
            collision = await _typed_answer_collision(
                db,
                session_id,
                answer.session_item_id,
                answer.idempotency_key,
            )
            (duplicates if collision == "duplicate" else conflicts).append(answer.idempotency_key)
            continue

        session.cards_answered += 1
        accepted.append(answer.idempotency_key)

    await db.commit()
    return accepted, duplicates, conflicts


async def adjudicate_pending_answers(db: AsyncSession, session_id: str) -> dict[str, Any]:
    return await _adjudicate_typed_answers(
        db=db,
        session_id=session_id,
        claim_statuses=CLAIMABLE_ADJUDICATION_STATUSES,
    )


async def retry_failed_adjudication(db: AsyncSession, session_id: str) -> dict[str, Any]:
    return await _adjudicate_typed_answers(
        db=db,
        session_id=session_id,
        claim_statuses=RETRYABLE_ADJUDICATION_STATUSES,
    )


async def fail_blocking_adjudication(db: AsyncSession, session_id: str, statuses: tuple[str, ...], message: str) -> None:
    await db.execute(
        update(TypedStudyAnswer)
        .where(
            TypedStudyAnswer.study_session_id == session_id,
            TypedStudyAnswer.adjudication_status.in_(statuses),
        )
        .values(
            adjudication_status=AdjudicationStatus.FAILED,
            adjudication_claim_token=None,
            adjudication_claimed_at=None,
            error_message=message,
        )
    )


async def _load_study_session(db: AsyncSession, session_id: str) -> StudySession:
    q = await db.execute(select(StudySession).where(StudySession.id == session_id))
    session = q.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found")
    return session


async def _typed_answer_collision(
    db: AsyncSession,
    session_id: str,
    session_item_id: str,
    idempotency_key: str,
) -> Literal["duplicate", "conflict"] | None:
    existing_q = await db.execute(select(TypedStudyAnswer).where(TypedStudyAnswer.idempotency_key == idempotency_key))
    if existing_q.scalars().first():
        return "duplicate"

    existing_item_answer_q = await db.execute(
        select(TypedStudyAnswer).where(
            TypedStudyAnswer.study_session_id == session_id,
            TypedStudyAnswer.session_item_id == session_item_id,
        )
    )
    return "conflict" if existing_item_answer_q.scalars().first() else None


async def _adjudicate_typed_answers(
    db: AsyncSession,
    session_id: str,
    claim_statuses: tuple[str, ...],
) -> dict[str, Any]:
    session = await _load_study_session(db, session_id)
    ensure_session_can_adjudicate(session)

    claimable_ids = await _claimable_answer_ids(db, session_id, claim_statuses)
    if not claimable_ids:
        return await adjudication_status_payload(db, session_id)

    claim_token = str(uuid.uuid4())
    claimed_at = datetime.now(timezone.utc).replace(tzinfo=None)
    stale_before = claimed_at - ADJUDICATION_LEASE_TIMEOUT
    claim_result = await db.execute(
        update(TypedStudyAnswer)
        .where(
            TypedStudyAnswer.id.in_(claimable_ids),
            or_(
                TypedStudyAnswer.adjudication_status.in_(claim_statuses),
                (
                    (TypedStudyAnswer.adjudication_status == AdjudicationStatus.PROCESSING)
                    & (
                        TypedStudyAnswer.adjudication_claimed_at.is_(None)
                        | (TypedStudyAnswer.adjudication_claimed_at < stale_before)
                    )
                ),
            ),
        )
        .values(
            adjudication_status=AdjudicationStatus.PROCESSING,
            adjudication_claim_token=claim_token,
            adjudication_claimed_at=claimed_at,
            error_message=None,
        )
    )
    await db.commit()

    if claim_result.rowcount != len(claimable_ids):
        await _release_claimed_answers(db, claim_token)
        return await adjudication_status_payload(db, session_id)

    try:
        answers = await _load_claimed_answers(db, claimable_ids, claim_token)
        batch_items, answer_context = await _build_adjudication_batch(db, answers)
        await db.commit()

        if batch_items:
            await _apply_llm_adjudication(db, session, batch_items, answer_context, claim_token)
    except Exception as exc:
        await db.rollback()
        await _fail_claimed_answers(db, claimable_ids, claim_token, exc)
        raise

    return await adjudication_status_payload(db, session_id)


async def _fail_claimed_answers(
    db: AsyncSession,
    answer_ids: list[str],
    claim_token: str,
    error: Exception,
) -> None:
    await db.execute(
        update(TypedStudyAnswer)
        .where(
            TypedStudyAnswer.id.in_(answer_ids),
            TypedStudyAnswer.adjudication_status == AdjudicationStatus.PROCESSING,
            TypedStudyAnswer.adjudication_claim_token == claim_token,
        )
        .values(
            adjudication_status=AdjudicationStatus.FAILED,
            adjudication_claim_token=None,
            adjudication_claimed_at=None,
            error_message=str(error)[:500] or error.__class__.__name__,
        )
    )
    await db.commit()


async def _release_claimed_answers(
    db: AsyncSession,
    claim_token: str,
) -> None:
    await db.execute(
        update(TypedStudyAnswer)
        .where(
            TypedStudyAnswer.adjudication_status == AdjudicationStatus.PROCESSING,
            TypedStudyAnswer.adjudication_claim_token == claim_token,
        )
        .values(
            adjudication_status=AdjudicationStatus.PENDING,
            adjudication_claim_token=None,
            adjudication_claimed_at=None,
        )
    )
    await db.commit()


async def _claimable_answer_ids(db: AsyncSession, session_id: str, statuses: tuple[str, ...]) -> list[str]:
    stale_before = datetime.now(timezone.utc).replace(tzinfo=None) - ADJUDICATION_LEASE_TIMEOUT
    claimable_q = await db.execute(
        select(TypedStudyAnswer.id)
        .where(
            TypedStudyAnswer.study_session_id == session_id,
            or_(
                TypedStudyAnswer.adjudication_status.in_(statuses),
                (
                    (TypedStudyAnswer.adjudication_status == AdjudicationStatus.PROCESSING)
                    & (
                        TypedStudyAnswer.adjudication_claimed_at.is_(None)
                        | (TypedStudyAnswer.adjudication_claimed_at < stale_before)
                    )
                ),
            ),
        )
        .order_by(TypedStudyAnswer.answered_at.asc(), TypedStudyAnswer.created_at.asc())
    )
    return list(claimable_q.scalars().all())


async def _load_claimed_answers(
    db: AsyncSession,
    answer_ids: list[str],
    claim_token: str,
) -> list[TypedStudyAnswer]:
    answers_q = await db.execute(
        select(TypedStudyAnswer)
        .where(
            TypedStudyAnswer.id.in_(answer_ids),
            TypedStudyAnswer.adjudication_claim_token == claim_token,
        )
        .order_by(TypedStudyAnswer.answered_at.asc(), TypedStudyAnswer.created_at.asc())
    )
    return list(answers_q.scalars().all())


async def _build_adjudication_batch(
    db: AsyncSession,
    answers: list[TypedStudyAnswer],
) -> tuple[list[AdjudicationItem], dict[str, tuple[TypedStudyAnswer, SessionItem, Card]]]:
    item_ids = list({a.session_item_id for a in answers if a.session_item_id})
    card_ids = list({a.card_id for a in answers if a.card_id})

    items_by_id: dict[str, SessionItem] = {}
    cards_by_id: dict[str, Card] = {}
    if item_ids:
        item_rows = await db.execute(select(SessionItem).where(SessionItem.id.in_(item_ids)))
        items_by_id = {item.id: item for item in item_rows.scalars().all()}
    if card_ids:
        card_rows = await db.execute(select(Card).where(Card.id.in_(card_ids)))
        cards_by_id = {card.id: card for card in card_rows.scalars().all()}

    batch_items: list[AdjudicationItem] = []
    answer_context: dict[str, tuple[TypedStudyAnswer, SessionItem, Card]] = {}
    for answer in answers:
        item = items_by_id.get(answer.session_item_id)
        card = cards_by_id.get(answer.card_id)
        if not item or not card:
            answer.adjudication_status = AdjudicationStatus.FAILED
            answer.adjudication_claim_token = None
            answer.adjudication_claimed_at = None
            answer.error_message = "Missing session item or card"
            continue

        batch_items.append(
            AdjudicationItem(
                id=answer.id,
                word=card.english,
                expected=answer.expected_answer,
                typed=answer.typed_answer,
                part_of_speech=card.part_of_speech,
            )
        )
        answer_context[answer.id] = (answer, item, card)
    return batch_items, answer_context


async def _apply_llm_adjudication(
    db: AsyncSession,
    session: StudySession,
    batch_items: list[AdjudicationItem],
    answer_context: dict[str, tuple[TypedStudyAnswer, SessionItem, Card]],
    claim_token: str,
) -> None:
    try:
        results = await adjudicate_answers(batch_items)
    except AdjudicationUnavailable as exc:
        if not await _claim_is_current(db, list(answer_context), claim_token):
            await db.rollback()
            return
        for answer, _, _ in answer_context.values():
            answer.adjudication_status = AdjudicationStatus.FAILED
            answer.adjudication_claim_token = None
            answer.adjudication_claimed_at = None
            answer.error_message = str(exc)[:500]
        await db.commit()
        return

    if not await _claim_is_current(db, list(answer_context), claim_token):
        await db.rollback()
        return

    for item_input in batch_items:
        answer, item, _ = answer_context[item_input.id]
        result = results[item_input.id]
        due = await apply_fsrs_rating(
            db=db,
            session=session,
            item=item,
            card_id=answer.card_id,
            rating_name=result.rating,
            reviewed_at=to_utc_aware(answer.answered_at),
            idempotency_key=answer.idempotency_key,
            confidence=str(result.confidence),
        )
        answer.adjudication_status = AdjudicationStatus.SUCCEEDED
        answer.verdict = result.verdict
        answer.rating = result.rating
        answer.reason = result.reason
        answer.confidence = result.confidence
        answer.provider = result.provider
        answer.model = result.model
        answer.next_due = due
        answer.error_message = None
        answer.adjudication_claim_token = None
        answer.adjudication_claimed_at = None

    await refresh_study_session_counts(db, session)
    await db.commit()


async def _claim_is_current(
    db: AsyncSession,
    answer_ids: list[str],
    claim_token: str,
) -> bool:
    current_count = await db.scalar(
        select(func.count())
        .select_from(TypedStudyAnswer)
        .where(
            TypedStudyAnswer.id.in_(answer_ids),
            TypedStudyAnswer.adjudication_status == AdjudicationStatus.PROCESSING,
            TypedStudyAnswer.adjudication_claim_token == claim_token,
        )
    )
    return current_count == len(answer_ids)
