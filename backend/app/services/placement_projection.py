import json
import uuid
from datetime import datetime, timezone
from typing import List

from fastapi import HTTPException
from sqlalchemy import desc, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    DataQualityIssue,
    DeckCard,
    PlacementAudit,
    PlacementAuditItem,
    PlacementEvent,
    PlacementItem,
    PlacementSession,
)
from app.schemas import PlacementEventSchema
from app.constants import (
    DataQualityStatus,
    PlacementAuditResult,
    PlacementAuditStatus,
    PlacementAnswer,
    PlacementEventType,
    PlacementProjectionResult,
    PLACEMENT_CHECKPOINT_SIZE,
    PlacementSessionStatus,
)
from app.services.placement_event_state import effective_placement_result


async def evaluate_session_status(db: AsyncSession, session: PlacementSession) -> None:
    audit_q = await db.execute(
        select(PlacementAudit).where(
            PlacementAudit.placement_session_id == session.id,
            PlacementAudit.status == PlacementAuditStatus.ACTIVE,
        )
    )
    active_audit = audit_q.scalars().first()
    if active_audit:
        session.status = PlacementSessionStatus.AUDIT_ACTIVE
        return

    checkpoint_q = await db.execute(select(PlacementAudit).where(PlacementAudit.placement_session_id == session.id))
    resolved_checkpoints = {audit.checkpoint for audit in checkpoint_q.scalars().all()}

    next_checkpoint = (session.current_position // PLACEMENT_CHECKPOINT_SIZE) * PLACEMENT_CHECKPOINT_SIZE
    if next_checkpoint > 0 and next_checkpoint not in resolved_checkpoints:
        segment_knowns = await get_effective_segment_knowns(
            db,
            session.id,
            next_checkpoint,
            json.loads(session.manifest_json),
        )
        if segment_knowns:
            session.status = PlacementSessionStatus.CHECKPOINT_PENDING
            return

        db.add(
            PlacementAudit(
                id=str(uuid.uuid4()),
                placement_session_id=session.id,
                checkpoint=next_checkpoint,
                status=PlacementAuditStatus.SKIPPED,
                error_rate=0.0,
            )
        )
        await db.commit()
        await db.refresh(session)

    if session.current_position >= session.requested_count:
        session.status = PlacementSessionStatus.COMPLETED
        session.finished_at = datetime.now(timezone.utc)
    elif session.status not in (PlacementSessionStatus.PAUSED, PlacementSessionStatus.ABANDONED):
        session.status = PlacementSessionStatus.ACTIVE


async def earliest_unresolved_checkpoint(db: AsyncSession, session: PlacementSession) -> int | None:
    if session.current_position < PLACEMENT_CHECKPOINT_SIZE:
        return None

    audit_q = await db.execute(
        select(PlacementAudit.checkpoint, PlacementAudit.status).where(PlacementAudit.placement_session_id == session.id)
    )
    resolved = {
        checkpoint
        for checkpoint, status in audit_q.all()
        if status in (PlacementAuditStatus.COMPLETED, PlacementAuditStatus.SKIPPED)
    }
    latest_checkpoint = (
        session.current_position // PLACEMENT_CHECKPOINT_SIZE
    ) * PLACEMENT_CHECKPOINT_SIZE
    for checkpoint in range(
        PLACEMENT_CHECKPOINT_SIZE,
        latest_checkpoint + 1,
        PLACEMENT_CHECKPOINT_SIZE,
    ):
        if checkpoint not in resolved:
            return checkpoint
    return None


async def reject_batch_that_crosses_checkpoint(
    db: AsyncSession,
    session: PlacementSession,
    events: List[PlacementEventSchema],
) -> None:
    unresolved = await earliest_unresolved_checkpoint(db, session)
    if unresolved is not None:
        if any(event.event_type == PlacementEventType.ANSWER and event.position >= unresolved for event in events):
            raise HTTPException(status_code=409, detail="Checkpoint audit required before continuing placement")
        return

    next_checkpoint = (
        (session.current_position // PLACEMENT_CHECKPOINT_SIZE) + 1
    ) * PLACEMENT_CHECKPOINT_SIZE
    if any(event.event_type == PlacementEventType.ANSWER and event.position >= next_checkpoint for event in events):
        raise HTTPException(status_code=409, detail="Placement batch crosses checkpoint boundary")


async def rebuild_placement_projection(db: AsyncSession, session: PlacementSession) -> set[str]:
    manifest = json.loads(session.manifest_json)
    events_q = await db.execute(
        select(PlacementEvent)
        .where(PlacementEvent.session_id == session.id)
        .order_by(PlacementEvent.created_at, PlacementEvent.id)
    )
    all_events = events_q.scalars().all()

    effective_answers = {}
    event_by_id = {event.id: event for event in all_events}
    event_by_key = {event.idempotency_key: event for event in all_events if event.idempotency_key}
    touched_card_ids = {event.card_id for event in all_events if event.card_id}

    for event in all_events:
        if event.event_type in (PlacementEventType.ANSWER, PlacementEventType.AUDIT_RECLASSIFY):
            effective_answers[event.position] = event
        elif event.event_type == PlacementEventType.UNDO:
            target = event_by_id.get(event.target_event_id) or event_by_key.get(event.target_event_id)
            if target and target.position in effective_answers:
                touched_card_ids.add(target.card_id)
                if effective_answers[target.position].id == target.id:
                    del effective_answers[target.position]

    session.current_position = next((pos for pos in range(len(manifest)) if pos not in effective_answers), len(manifest))
    await _upsert_projection_items(db, session, manifest, effective_answers)
    return {card_id for card_id in touched_card_ids if card_id}


async def add_placement_event_once(db: AsyncSession, event: PlacementEvent) -> None:
    existing = await db.execute(select(PlacementEvent.id).where(PlacementEvent.idempotency_key == event.idempotency_key))
    if not existing.scalar():
        db.add(event)


async def apply_audit_side_effects(
    db: AsyncSession,
    session_id: str,
    checkpoint: int,
    audit_item_id: str,
    item: PlacementAuditItem,
    resolved_res: str | PlacementAuditResult,
) -> None:
    if resolved_res == PlacementAuditResult.PROBLEMATIC:
        await _apply_problematic_audit_result(db, session_id, checkpoint, audit_item_id, item)
    elif resolved_res == PlacementAuditResult.INCORRECT:
        await _apply_incorrect_audit_result(db, session_id, audit_item_id, item)


async def placement_deck_ids_for_session(db: AsyncSession, session_id: str) -> list[str]:
    deck_cards_q = await db.execute(
        select(DeckCard.deck_id)
        .join(PlacementItem, PlacementItem.card_id == DeckCard.card_id)
        .where(PlacementItem.placement_session_id == session_id)
    )
    return list(set(deck_cards_q.scalars().all()))


async def get_effective_segment_knowns(db: AsyncSession, session_id: str, checkpoint: int, manifest: list) -> List[str]:
    start_pos = checkpoint - PLACEMENT_CHECKPOINT_SIZE
    end_pos = checkpoint - 1
    segment_cards = [item["card_id"] for item in manifest if start_pos <= item["position"] <= end_pos]
    if not segment_cards:
        return []

    events_q = await db.execute(
        select(PlacementEvent)
        .where(PlacementEvent.session_id == session_id, PlacementEvent.card_id.in_(segment_cards))
        .order_by(PlacementEvent.created_at, PlacementEvent.id)
    )
    card_events: dict[str, list[PlacementEvent]] = {}
    for event in events_q.scalars().all():
        card_events.setdefault(event.card_id, []).append(event)

    knowns = []
    for card_id in segment_cards:
        if effective_placement_result(card_events.get(card_id, [])) == PlacementProjectionResult.KNOWN:
            knowns.append(card_id)
    return knowns


async def _upsert_projection_items(
    db: AsyncSession,
    session: PlacementSession,
    manifest: list,
    effective_answers: dict[int, PlacementEvent],
) -> None:
    items_q = await db.execute(select(PlacementItem).where(PlacementItem.placement_session_id == session.id))
    items_by_position = {item.position: item for item in items_q.scalars().all()}

    for position in range(len(manifest)):
        existing_item = items_by_position.get(position)
        effective_event = effective_answers.get(position)
        if effective_event:
            _apply_projection_event(db, session.id, position, existing_item, effective_event)
        elif existing_item:
            existing_item.undone = True


def _apply_projection_event(
    db: AsyncSession,
    session_id: str,
    position: int,
    existing_item: PlacementItem | None,
    effective_event: PlacementEvent,
) -> None:
    if existing_item:
        existing_item.undone = False
        existing_item.placement_result = effective_event.result
        existing_item.card_id = effective_event.card_id
        existing_item.problematic_reason = effective_event.problematic_reason
        existing_item.idempotency_key = effective_event.idempotency_key
        existing_item.answered_at = effective_event.created_at
        return

    db.add(
        PlacementItem(
            id=str(uuid.uuid4()),
            placement_session_id=session_id,
            position=position,
            card_id=effective_event.card_id,
            placement_result=effective_event.result,
            problematic_reason=effective_event.problematic_reason,
            idempotency_key=effective_event.idempotency_key,
            answered_at=effective_event.created_at,
            undone=False,
        )
    )


async def _apply_problematic_audit_result(
    db: AsyncSession,
    session_id: str,
    checkpoint: int,
    audit_item_id: str,
    item: PlacementAuditItem,
) -> None:
    p_item_q = await db.execute(
        select(PlacementItem).where(
            PlacementItem.placement_session_id == session_id,
            PlacementItem.card_id == item.card_id,
        )
    )
    p_item = p_item_q.scalars().first()
    if p_item:
        p_item.placement_result = PlacementAnswer.PROBLEMATIC
        p_item.problematic_reason = "audit_flagged"

    await add_placement_event_once(
        db,
        PlacementEvent(
            id=str(uuid.uuid4()),
            session_id=session_id,
            event_type=PlacementEventType.ANSWER,
            position=p_item.position if p_item else 0,
            card_id=item.card_id,
            result=PlacementAnswer.PROBLEMATIC,
            idempotency_key=f"audit_problematic_{audit_item_id}",
            created_at=datetime.now(timezone.utc),
        ),
    )
    await _add_audit_issue_if_missing(db, item.card_id, checkpoint)


async def _apply_incorrect_audit_result(
    db: AsyncSession,
    session_id: str,
    audit_item_id: str,
    item: PlacementAuditItem,
) -> None:
    p_item_q = await db.execute(
        select(PlacementItem).where(
            PlacementItem.placement_session_id == session_id,
            PlacementItem.card_id == item.card_id,
        )
    )
    p_item = p_item_q.scalars().first()
    if not p_item:
        return

    p_item.audit_reclassified = True
    orig_ev_q = await db.execute(
        select(PlacementEvent)
        .where(
            PlacementEvent.session_id == session_id,
            PlacementEvent.card_id == item.card_id,
            PlacementEvent.event_type == PlacementEventType.ANSWER,
        )
        .order_by(desc(PlacementEvent.created_at))
        .limit(1)
    )
    orig_ev = orig_ev_q.scalars().first()

    await add_placement_event_once(
        db,
        PlacementEvent(
            id=str(uuid.uuid4()),
            session_id=session_id,
            event_type=PlacementEventType.AUDIT_RECLASSIFY,
            position=p_item.position,
            card_id=item.card_id,
            result=PlacementAnswer.FUZZY,
            target_event_id=orig_ev.id if orig_ev else None,
            idempotency_key=f"audit_reclass_{audit_item_id}",
            created_at=datetime.now(timezone.utc),
        ),
    )


async def _add_audit_issue_if_missing(db: AsyncSession, card_id: str, checkpoint: int) -> None:
    issue_q = await db.execute(
        select(DataQualityIssue).where(
            DataQualityIssue.card_id == card_id,
            DataQualityIssue.source == "audit_user_flag",
            DataQualityIssue.issue_type == "potential_ambiguity",
        )
    )
    if issue_q.scalars().first():
        return

    db.add(
        DataQualityIssue(
            id=str(uuid.uuid4()),
            card_id=card_id,
            source="audit_user_flag",
            issue_type="potential_ambiguity",
            note=f"User marked audited card as problematic during checkpoint {checkpoint} audit.",
            status=DataQualityStatus.OPEN,
        )
    )
