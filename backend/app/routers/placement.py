import uuid
import json
from typing import List
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select, update, desc, func
from sqlalchemy.exc import IntegrityError

from app.database import get_db
from app.models import (
    PlacementSession, PlacementItem, PlacementEvent,
    ActivationQueue, Card
)
from app.schemas import (
    PlacementSessionCreate, PlacementSessionResponse,
    AuditAnswerRequest,
    AuditQuestionsResponse,
    BatchEventsRequest, BatchEventsResponse, CardResponse
)
from app.constants import (
    ACTIVE_PLACEMENT_STATUSES,
    PlacementEventType,
    PLACEMENT_CHECKPOINT_SIZE,
    PlacementSessionStatus,
    TERMINAL_PLACEMENT_STATUSES,
)
from app.services.deck_scope import DeckScopeError, resolve_deck_ids
from app.services.placement_audit_answers import answer_placement_audit_question
from app.services.placement_audits import load_placement_audit
from app.services.placement_activation import sync_activation_queue_for_cards
from app.services.placement_candidates import placement_candidate_query
from app.services.placement_projection import (
    evaluate_session_status,
    rebuild_placement_projection,
    reject_batch_that_crosses_checkpoint,
)

router = APIRouter(prefix="/api/v1/placement-sessions", tags=["placement"])

def placement_session_response(session: PlacementSession) -> PlacementSessionResponse:
    return PlacementSessionResponse(
        id=session.id,
        requested_count=session.requested_count,
        status=session.status,
        manifest_json=session.manifest_json,
        started_at=session.started_at,
        current_position=session.current_position,
        checkpoint_size=PLACEMENT_CHECKPOINT_SIZE,
    )

@router.post("", response_model=PlacementSessionResponse)
async def create_placement_session(data: PlacementSessionCreate, db: AsyncSession = Depends(get_db)):
    # 1. Idempotency Check: if there's already an active or paused session, return it
    active_q = await db.execute(
        select(PlacementSession)
        .where(PlacementSession.status.in_(ACTIVE_PLACEMENT_STATUSES))
        .order_by(desc(PlacementSession.created_at))
        .limit(1)
    )
    existing = active_q.scalars().first()
    if existing:
        return placement_session_response(existing)
        
    try:
        deck_ids_to_use = await resolve_deck_ids(db, data.deck_ids)
    except DeckScopeError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message)

    candidate_query = placement_candidate_query(deck_ids_to_use)
    count_q = await db.execute(select(func.count()).select_from(candidate_query.subquery()))
    available_count = int(count_q.scalar() or 0)
    if available_count == 0:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "insufficient_cards",
                "message": "No active, eligible cards found matching the criteria",
                "available_count": 0
            }
        )
        
    if data.requested_count > available_count:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "insufficient_cards",
                "message": f"Requested count {data.requested_count} exceeds available eligible cards ({available_count})",
                "available_count": available_count
            }
        )
        
    session_size = data.requested_count
    selected_q = await db.execute(candidate_query.order_by(func.random()).limit(session_size))
    selected_cards = selected_q.scalars().all()
    
    manifest = [{"position": i, "card_id": c.id} for i, c in enumerate(selected_cards)]
    manifest_json_str = json.dumps(manifest)
    
    session_id = str(uuid.uuid4())
    session = PlacementSession(
        id=session_id,
        requested_count=session_size,
        status=PlacementSessionStatus.ACTIVE,
        current_position=0,
        manifest_json=manifest_json_str
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    
    return placement_session_response(session)

@router.get("/active", response_model=PlacementSessionResponse)
async def get_active_session(db: AsyncSession = Depends(get_db)):
    result = await db.execute(
        select(PlacementSession)
        .where(PlacementSession.status.in_(ACTIVE_PLACEMENT_STATUSES))
        .order_by(desc(PlacementSession.created_at))
        .limit(1)
    )
    session = result.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="No active placement session")
    
    return placement_session_response(session)

@router.post("/{session_id}/abandon", response_model=PlacementSessionResponse)
async def abandon_session(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PlacementSession).where(PlacementSession.id == session_id))
    session = result.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="Placement session not found")
    if session.status in TERMINAL_PLACEMENT_STATUSES:
        raise HTTPException(status_code=409, detail="Placement session is closed")

    answered_card_ids = select(PlacementItem.card_id).where(
        PlacementItem.placement_session_id == session_id,
        PlacementItem.answered_at.is_not(None),
    )

    await db.execute(
        update(PlacementItem)
        .where(PlacementItem.placement_session_id == session_id)
        .values(undone=True)
    )
    await db.execute(delete(ActivationQueue).where(ActivationQueue.card_id.in_(answered_card_ids)))

    session.status = PlacementSessionStatus.ABANDONED
    await db.commit()
    await db.refresh(session)
    
    return placement_session_response(session)

@router.post("/{session_id}/events/batch", response_model=BatchEventsResponse)
async def batch_events(session_id: str, data: BatchEventsRequest, db: AsyncSession = Depends(get_db)):
    # Verify session
    result = await db.execute(select(PlacementSession).where(PlacementSession.id == session_id))
    session = result.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="Placement session not found")
    if session.status in TERMINAL_PLACEMENT_STATUSES:
        raise HTTPException(status_code=409, detail="Placement session is closed")
    await reject_batch_that_crosses_checkpoint(db, session, data.events)
    manifest = json.loads(session.manifest_json)
    manifest_card_by_position = {
        item["position"]: item["card_id"]
        for item in manifest
    }
        
    accepted = []
    duplicates = []
    modified_card_ids = set()
    seen_keys = set()
    
    for event in data.events:
        if event.idempotency_key in seen_keys:
            duplicates.append(event.idempotency_key)
            continue
        seen_keys.add(event.idempotency_key)

        # Check idempotency
        existing = await db.execute(
            select(PlacementEvent.id).where(PlacementEvent.idempotency_key == event.idempotency_key)
        )
        if existing.scalars().first():
            duplicates.append(event.idempotency_key)
            continue

        event_card_id = event.card_id
        event_position = event.position
        if event.event_type == PlacementEventType.UNDO and event.target_event_id:
            target_q = await db.execute(
                select(PlacementEvent.card_id, PlacementEvent.position).where(
                    PlacementEvent.session_id == session_id,
                    (
                        (PlacementEvent.id == event.target_event_id)
                        | (PlacementEvent.idempotency_key == event.target_event_id)
                    ),
                )
            )
            target = target_q.first()
            if not target:
                raise HTTPException(status_code=400, detail="Undo target event not found")
            event_card_id = target.card_id
            event_position = target.position
        elif manifest_card_by_position.get(event_position) != event_card_id:
            raise HTTPException(
                status_code=400,
                detail="Placement event does not match the session manifest",
            )
            
        event_id = str(uuid.uuid4())
        new_event = PlacementEvent(
            id=event_id,
            session_id=session_id,
            event_type=event.event_type,
            position=event_position,
            card_id=event_card_id,
            result=event.result,
            problematic_reason=event.problematic_reason,
            target_event_id=event.target_event_id,
            idempotency_key=event.idempotency_key,
            created_at=event.answered_at
        )
        try:
            async with db.begin_nested():
                db.add(new_event)
                await db.flush()
        except IntegrityError:
            duplicates.append(event.idempotency_key)
            continue

        accepted.append(event.idempotency_key)
        
        if event_card_id:
            modified_card_ids.add(event_card_id)
            
    if accepted or duplicates:
        await db.commit()

        modified_card_ids |= await rebuild_placement_projection(db, session)
        await db.commit()

        # Real-time idempotent ActivationQueue updates
        await sync_activation_queue_for_cards(db, session_id, modified_card_ids)
            
        await evaluate_session_status(db, session)
        await db.commit()
        
    return BatchEventsResponse(accepted=accepted, duplicates=duplicates)

@router.get(
    "/{session_id}/audit/{checkpoint}",
    response_model=AuditQuestionsResponse,
)
async def get_audit_questions(session_id: str, checkpoint: int, db: AsyncSession = Depends(get_db)):
    return await load_placement_audit(db, session_id, checkpoint)

@router.post("/{session_id}/audit/{checkpoint}/answer/{audit_item_id}")
async def answer_audit_question(
    session_id: str, checkpoint: int, audit_item_id: str,
    data: AuditAnswerRequest, db: AsyncSession = Depends(get_db)
):
    return await answer_placement_audit_question(
        db=db,
        session_id=session_id,
        checkpoint=checkpoint,
        audit_item_id=audit_item_id,
        selected_option_id=data.selected_option_id,
        idempotency_key=data.idempotency_key,
        answered_at=data.answered_at,
    )

@router.get("/{session_id}", response_model=PlacementSessionResponse)
async def get_session(session_id: str, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PlacementSession).where(PlacementSession.id == session_id))
    session = result.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="Placement session not found")
        
    return placement_session_response(session)

@router.get("/{session_id}/chunks/{chunk_number}", response_model=List[CardResponse])
async def get_placement_chunk(session_id: str, chunk_number: int, db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(PlacementSession).where(PlacementSession.id == session_id))
    session = result.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="Placement session not found")
        
    try:
        manifest = json.loads(session.manifest_json)
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to parse session manifest")
        
    chunk_size = PLACEMENT_CHECKPOINT_SIZE
    start_idx = chunk_number * chunk_size
    end_idx = (chunk_number + 1) * chunk_size
    
    # Get manifest items for this chunk
    chunk_items = [item for item in manifest if start_idx <= item["position"] < end_idx]
    if not chunk_items:
        return []
        
    card_ids = [item["card_id"] for item in chunk_items]
    
    # Query cards
    cards_result = await db.execute(select(Card).where(Card.id.in_(card_ids)))
    cards = {c.id: c for c in cards_result.scalars().all()}
    
    # Order cards to match manifest positions
    ordered_cards = []
    for item in chunk_items:
        card = cards.get(item["card_id"])
        if card:
            ordered_cards.append(card)
            
    return ordered_cards
