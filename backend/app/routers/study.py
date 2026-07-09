import uuid
from datetime import datetime, timezone
from typing import List, Optional, Dict, Any
from pydantic import BaseModel, ConfigDict, Field
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc

from app.database import get_db
from app.models import (
    StudySession, SessionItem, Card
)
from app.services.deck_scope import DeckScopeError, resolve_deck_ids
from app.services.placement_gate import get_placement_gate
from app.services.study_answers import (
    adjudicate_pending_answers,
    adjudication_status_payload,
    fail_blocking_adjudication,
    record_typed_answers,
    retry_failed_adjudication,
)
from app.services.study_availability import get_study_availability
from app.services.study_plan import ensure_study_plan
from app.services.study_plan_status import study_plan_payload
from app.services.study_session_builder import build_study_session_items
from app.constants import (
    ACTIVE_STUDY_STATUSES,
    BLOCKING_ADJUDICATION_STATUSES,
    StudyMode,
    StudySessionStatus,
)

router = APIRouter(prefix="/api/v1/study-sessions", tags=["study"])

class StudySessionCreate(BaseModel):
    requested_size: int = Field(gt=0, le=100)
    mode: StudyMode
    deck_ids: Optional[List[str]] = None
    activation_budget: Optional[int] = Field(default=None, ge=0, le=100)

class StudySessionResponse(BaseModel):
    id: str
    requested_size: int
    mode: StudyMode
    status: StudySessionStatus
    started_at: datetime
    finished_at: Optional[datetime] = None
    sync_status: StudySessionStatus
    cards_answered: int
    again_count: int
    hard_count: int
    good_count: int

    model_config = ConfigDict(from_attributes=True)

class TypedStudyAnswerSchema(BaseModel):
    idempotency_key: str = Field(min_length=1, max_length=128)
    session_item_id: str = Field(min_length=1, max_length=128)
    card_id: str = Field(min_length=1, max_length=128)
    typed_answer: str = Field(max_length=2_000)
    answered_at: datetime

class BatchTypedStudyAnswersRequest(BaseModel):
    answers: List[TypedStudyAnswerSchema] = Field(max_length=100)

class BatchTypedStudyAnswersResponse(BaseModel):
    accepted: List[str]
    duplicates: List[str]
    conflicts: List[str]

class AdjudicationStatusResponse(BaseModel):
    session_id: str
    pending: int
    processing: int
    succeeded: int
    failed: int
    total: int
    results: List[Dict[str, Any]]


def study_session_response(session: StudySession) -> StudySessionResponse:
    return StudySessionResponse(
        id=session.id,
        requested_size=session.requested_size,
        mode=session.mode,
        status=session.sync_status,
        started_at=session.started_at,
        finished_at=session.finished_at,
        sync_status=session.sync_status,
        cards_answered=session.cards_answered,
        again_count=session.again_count,
        hard_count=session.hard_count,
        good_count=session.good_count,
    )


@router.post("", response_model=StudySessionResponse)
async def create_study_session(data: StudySessionCreate, db: AsyncSession = Depends(get_db)):
    req_size = data.requested_size
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    try:
        deck_ids = await resolve_deck_ids(db, data.deck_ids)
    except DeckScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    placement_gate = await get_placement_gate(db, deck_ids)
    if not placement_gate.is_complete:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "placement_required",
                "message": "Formal review is locked until placement is complete.",
                "placement_status": placement_gate.to_response(),
            },
        )

    # 1. Idempotency Check: if there's already an active or paused session, return it
    active_q = await db.execute(
        select(StudySession)
        .where(StudySession.sync_status.in_(ACTIVE_STUDY_STATUSES))
        .order_by(desc(StudySession.created_at))
        .limit(1)
    )
    existing = active_q.scalars().first()
    if existing:
        return study_session_response(existing)

    await ensure_study_plan(db, now_utc)
    availability = await get_study_availability(
        db=db,
        now_utc=now_utc,
        deck_ids=deck_ids,
        has_study_plan=True,
        selection_limit=req_size + 100,
    )
    due_cards = availability.due_cards
    new_candidates = availability.new_candidates

    if availability.pending_adjudication_count > 0:
        raise HTTPException(
            status_code=409,
            detail={
                "error": "pending_adjudication",
                "message": "Finish LLM grading for the previous study session before starting another round.",
                "pending_adjudication_count": availability.pending_adjudication_count,
                "availability_state": availability.availability_state,
            },
        )

    # Calculate budget-restricted availability
    budget = data.activation_budget if data.activation_budget is not None else req_size
    budget_available = len(due_cards) + min(len(new_candidates), budget)

    if budget_available == 0:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "no_due_cards",
                "message": "No cards are due right now",
                "available_count": 0,
                "next_due": availability.next_review_due_at.isoformat() if availability.next_review_due_at else None,
                "availability_state": availability.availability_state,
                "pending_adjudication_count": availability.pending_adjudication_count,
            }
        )

    if req_size > budget_available:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "insufficient_cards",
                "message": f"Requested size {req_size} exceeds budget-restricted available cards ({budget_available})",
                "available_count": budget_available
            }
        )

    session_id = str(uuid.uuid4())
    session_items = build_study_session_items(
        due_cards=due_cards,
        new_candidates=new_candidates,
        requested_size=req_size,
        activation_budget=budget,
    )

    # Save to database
    session = StudySession(
        id=session_id,
        requested_size=len(session_items),
        mode=data.mode,
        sync_status=StudySessionStatus.ACTIVE,
        cards_answered=0,
        again_count=0,
        hard_count=0,
        good_count=0
    )
    db.add(session)
    await db.flush()

    for i, item in enumerate(session_items):
        db_item = SessionItem(
            id=str(uuid.uuid4()),
            study_session_id=session_id,
            position=i,
            target_card_id=item.card.id,
            correct_option_card_id=item.card.id,
            option_card_ids_json=[],
            source_type=item.source_type
        )
        db.add(db_item)

    await db.commit()
    await db.refresh(session)

    return study_session_response(session)

@router.get("/plan")
async def get_study_plan(db: AsyncSession = Depends(get_db)):
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    return await study_plan_payload(db, now_utc)

@router.get("/active")
async def get_active_study_session(db: AsyncSession = Depends(get_db)):
    active_q = await db.execute(
        select(StudySession)
        .where(StudySession.sync_status.in_(ACTIVE_STUDY_STATUSES))
        .order_by(desc(StudySession.created_at))
        .limit(1)
    )
    session = active_q.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="No active study session found")

    return study_session_response(session)

@router.get("/{session_id}", response_model=StudySessionResponse)
async def get_study_session(session_id: str, db: AsyncSession = Depends(get_db)):
    q = await db.execute(select(StudySession).where(StudySession.id == session_id))
    session = q.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found")
    return study_session_response(session)

@router.get("/{session_id}/items")
async def get_study_session_items(session_id: str, db: AsyncSession = Depends(get_db)):
    # Check session
    q = await db.execute(select(StudySession).where(StudySession.id == session_id))
    session = q.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found")

    items_q = await db.execute(
        select(SessionItem).where(SessionItem.study_session_id == session_id).order_by(SessionItem.position.asc())
    )
    items = items_q.scalars().all()

    # Load all cards referenced in items
    card_ids = [item.target_card_id for item in items]
    cards_res = await db.execute(select(Card).where(Card.id.in_(card_ids)))
    cards_map = {c.id: c for c in cards_res.scalars().all()}

    # Format response
    response_items = []
    for item in items:
        card = cards_map.get(item.target_card_id)
        response_items.append({
            "id": item.id,
            "position": item.position,
            "target_card_id": item.target_card_id,
            "correct_option_card_id": item.correct_option_card_id,
            "option_card_ids_json": item.option_card_ids_json,
            "source_type": item.source_type,
            "answered_at": item.answered_at,
            "sync_status": item.sync_status,
            "idempotency_key": item.idempotency_key,
            "card": {
                "id": card.id,
                "english": card.english,
                "chinese_meaning": card.chinese_meaning,
                "part_of_speech": card.part_of_speech,
                "example_sentence": card.example_sentence,
                "example_translation": card.example_translation
            } if card else None
        })

    return response_items

@router.post("/{session_id}/typed-answers/batch", response_model=BatchTypedStudyAnswersResponse)
async def batch_typed_study_answers(session_id: str, data: BatchTypedStudyAnswersRequest, db: AsyncSession = Depends(get_db)):
    accepted, duplicates, conflicts = await record_typed_answers(db, session_id, data.answers)
    return BatchTypedStudyAnswersResponse(
        accepted=accepted,
        duplicates=duplicates,
        conflicts=conflicts,
    )


@router.get("/{session_id}/adjudication-status", response_model=AdjudicationStatusResponse)
async def get_adjudication_status(session_id: str, db: AsyncSession = Depends(get_db)):
    return await adjudication_status_payload(db, session_id)


@router.post("/{session_id}/adjudicate", response_model=AdjudicationStatusResponse)
async def adjudicate_typed_answers(session_id: str, db: AsyncSession = Depends(get_db)):
    return await adjudicate_pending_answers(db, session_id)


@router.post("/{session_id}/adjudication-retry", response_model=AdjudicationStatusResponse)
async def retry_adjudication(session_id: str, db: AsyncSession = Depends(get_db)):
    return await retry_failed_adjudication(db, session_id)

@router.post("/{session_id}/abandon", response_model=StudySessionResponse)
async def abandon_study_session(session_id: str, db: AsyncSession = Depends(get_db)):
    q = await db.execute(select(StudySession).where(StudySession.id == session_id))
    session = q.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found")
    if session.sync_status == StudySessionStatus.ABANDONED:
        return study_session_response(session)
    if session.sync_status not in ACTIVE_STUDY_STATUSES:
        raise HTTPException(status_code=409, detail="Completed study sessions cannot be abandoned")

    session.sync_status = StudySessionStatus.ABANDONED
    session.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await fail_blocking_adjudication(
        db,
        session_id,
        BLOCKING_ADJUDICATION_STATUSES,
        "Session abandoned before LLM grading completed.",
    )
    await db.commit()
    await db.refresh(session)
    return study_session_response(session)

@router.post("/{session_id}/finish", response_model=StudySessionResponse)
async def finish_study_session(session_id: str, db: AsyncSession = Depends(get_db)):
    q = await db.execute(select(StudySession).where(StudySession.id == session_id))
    session = q.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="Study session not found")
    if session.sync_status == StudySessionStatus.COMPLETED:
        return study_session_response(session)
    if session.sync_status not in ACTIVE_STUDY_STATUSES:
        raise HTTPException(status_code=409, detail="Abandoned study sessions cannot be completed")

    session.sync_status = StudySessionStatus.COMPLETED
    session.finished_at = datetime.now(timezone.utc).replace(tzinfo=None)
    await db.commit()
    await db.refresh(session)
    return study_session_response(session)
