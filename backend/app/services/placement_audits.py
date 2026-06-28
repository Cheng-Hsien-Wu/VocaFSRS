import json
import math
import random
import uuid

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import (
    PlacementAuditStatus,
    PlacementSessionStatus,
)
from app.models import (
    Card,
    PlacementAudit,
    PlacementAuditEvent,
    PlacementAuditItem,
    PlacementSession,
)
from app.schemas import AuditQuestionResponse, AuditQuestionsResponse
from app.services.placement_audit_items import build_audit_items_with_replacements
from app.services.placement_projection import (
    get_effective_segment_knowns,
    placement_deck_ids_for_session,
)


async def load_placement_audit(
    db: AsyncSession,
    session_id: str,
    checkpoint: int,
) -> AuditQuestionsResponse:
    session = await _load_session(db, session_id)
    audit = await _get_or_create_audit(db, session, checkpoint)
    if audit.status == PlacementAuditStatus.SKIPPED:
        return AuditQuestionsResponse(
            status=PlacementAuditStatus.SKIPPED,
            checkpoint=checkpoint,
            questions=[],
        )

    items = await _load_or_create_items(db, session, audit, checkpoint)
    questions = await _build_questions(db, items)
    return AuditQuestionsResponse(
        status=PlacementAuditStatus(audit.status),
        checkpoint=checkpoint,
        questions=questions,
    )


async def _load_session(db: AsyncSession, session_id: str) -> PlacementSession:
    result = await db.execute(
        select(PlacementSession).where(PlacementSession.id == session_id)
    )
    session = result.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="Placement session not found")
    if session.status == PlacementSessionStatus.ABANDONED:
        raise HTTPException(status_code=409, detail="Placement session is abandoned")
    return session


async def _get_or_create_audit(
    db: AsyncSession,
    session: PlacementSession,
    checkpoint: int,
) -> PlacementAudit:
    result = await db.execute(
        select(PlacementAudit).where(
            PlacementAudit.placement_session_id == session.id,
            PlacementAudit.checkpoint == checkpoint,
        )
    )
    audit = result.scalars().first()
    if audit and session.current_position < checkpoint:
        raise HTTPException(status_code=409, detail="Checkpoint is not ready for audit")
    if audit:
        return audit

    if checkpoint > session.current_position or session.status not in (
        PlacementSessionStatus.CHECKPOINT_PENDING,
        PlacementSessionStatus.AUDIT_ACTIVE,
    ):
        raise HTTPException(status_code=409, detail="Checkpoint is not ready for audit")

    knowns = await get_effective_segment_knowns(
        db,
        session.id,
        checkpoint,
        json.loads(session.manifest_json),
    )
    status = (
        PlacementAuditStatus.ACTIVE
        if knowns
        else PlacementAuditStatus.SKIPPED
    )
    audit = PlacementAudit(
        id=str(uuid.uuid4()),
        placement_session_id=session.id,
        checkpoint=checkpoint,
        status=status,
        error_rate=0.0 if status == PlacementAuditStatus.SKIPPED else None,
    )
    db.add(audit)
    await db.commit()
    await db.refresh(audit)
    return audit


async def _load_or_create_items(
    db: AsyncSession,
    session: PlacementSession,
    audit: PlacementAudit,
    checkpoint: int,
) -> list[PlacementAuditItem]:
    result = await db.execute(
        select(PlacementAuditItem).where(
            PlacementAuditItem.placement_audit_id == audit.id
        )
    )
    items = list(result.scalars().all())
    if items:
        return items

    manifest = json.loads(session.manifest_json)
    knowns = await get_effective_segment_knowns(
        db,
        session.id,
        checkpoint,
        manifest,
    )
    sample_size = max(1, math.ceil(len(knowns) * 0.10))
    rng = random.Random(f"{session.id}_{checkpoint}_audit1")
    sample_candidates = rng.sample(knowns, min(sample_size, len(knowns)))
    items = await build_audit_items_with_replacements(
        db=db,
        audit_id=audit.id,
        session_id=session.id,
        deck_ids=await placement_deck_ids_for_session(db, session.id),
        selected_card_ids=sample_candidates,
        replacement_card_ids=knowns,
        sample_batch=1,
        issue_source="audit_generator",
        rng=rng,
    )
    await db.commit()
    return items


async def _build_questions(
    db: AsyncSession,
    items: list[PlacementAuditItem],
) -> list[AuditQuestionResponse]:
    card_ids = [item.card_id for item in items]
    cards = {}
    if card_ids:
        result = await db.execute(select(Card).where(Card.id.in_(card_ids)))
        cards = {card.id: card for card in result.scalars().all()}

    item_ids = [item.id for item in items]
    events = {}
    if item_ids:
        result = await db.execute(
            select(PlacementAuditEvent).where(
                PlacementAuditEvent.placement_audit_item_id.in_(item_ids)
            )
        )
        events = {
            event.placement_audit_item_id: event
            for event in result.scalars().all()
        }

    questions = []
    for item in items:
        card = cards.get(item.card_id)
        event = events.get(item.id)
        questions.append(
            AuditQuestionResponse(
                audit_item_id=item.id,
                card_id=item.card_id,
                english=card.english if card else "",
                options=json.loads(item.options_json),
                sample_batch=item.sample_batch,
                answered=event is not None,
                selected_option_id=event.selected_option_id if event else None,
                is_correct=event.is_correct if event else None,
            )
        )
    return questions
