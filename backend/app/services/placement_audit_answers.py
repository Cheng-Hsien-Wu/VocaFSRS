import json
import math
import random
import uuid
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import (
    PlacementAuditResult,
    PlacementAuditStatus,
    AuditSpecialOption,
    TERMINAL_PLACEMENT_STATUSES,
)
from app.models import (
    PlacementAudit,
    PlacementAuditEvent,
    PlacementAuditItem,
    PlacementSession,
)
from app.services.placement_audit_items import build_audit_items_with_replacements
from app.services.placement_activation import sync_activation_queue_for_card
from app.services.placement_projection import (
    apply_audit_side_effects,
    evaluate_session_status,
    get_effective_segment_knowns,
    placement_deck_ids_for_session,
)


async def answer_placement_audit_question(
    db: AsyncSession,
    session_id: str,
    checkpoint: int,
    audit_item_id: str,
    selected_option_id: str,
    idempotency_key: str,
    answered_at: datetime,
) -> dict:
    session = await _load_open_session(db, session_id)
    item, audit = await _load_audit_item(db, session_id, checkpoint, audit_item_id)

    if session.current_position < checkpoint:
        raise HTTPException(status_code=409, detail="Checkpoint is not ready for audit")

    existing_response = await _apply_existing_answer_if_present(
        db=db,
        session=session,
        session_id=session_id,
        checkpoint=checkpoint,
        audit=audit,
        audit_item_id=audit_item_id,
        item=item,
        idempotency_key=idempotency_key,
    )
    if existing_response:
        return existing_response

    resolved_result, is_correct = _resolve_audit_answer(selected_option_id, item.correct_option_id)
    audit_event = PlacementAuditEvent(
        id=str(uuid.uuid4()),
        placement_audit_item_id=audit_item_id,
        selected_option_id=selected_option_id,
        is_correct=is_correct,
        idempotency_key=idempotency_key,
        created_at=answered_at,
    )
    try:
        async with db.begin_nested():
            db.add(audit_event)
            item.resolved_result = resolved_result
            await db.flush()
    except IntegrityError:
        return {"status": "success", "message": "Idempotent answer recorded"}

    await apply_audit_side_effects(db, session_id, checkpoint, audit_item_id, item, resolved_result)
    await db.flush()
    await sync_activation_queue_for_card(db, session_id, item.card_id)
    await _complete_batch_if_ready(db, session, checkpoint, audit, item.sample_batch)
    await db.commit()
    return {"status": "success", "resolved_result": resolved_result}


async def _load_open_session(db: AsyncSession, session_id: str) -> PlacementSession:
    session_q = await db.execute(select(PlacementSession).where(PlacementSession.id == session_id))
    session = session_q.scalars().first()
    if not session:
        raise HTTPException(status_code=404, detail="Placement session not found")
    if session.status in TERMINAL_PLACEMENT_STATUSES:
        raise HTTPException(status_code=409, detail="Placement session is closed")
    return session


async def _load_audit_item(
    db: AsyncSession,
    session_id: str,
    checkpoint: int,
    audit_item_id: str,
) -> tuple[PlacementAuditItem, PlacementAudit]:
    item_q = await db.execute(select(PlacementAuditItem).where(PlacementAuditItem.id == audit_item_id))
    item = item_q.scalars().first()
    if not item:
        raise HTTPException(status_code=404, detail="Audit item not found")

    audit_q = await db.execute(select(PlacementAudit).where(PlacementAudit.id == item.placement_audit_id))
    audit = audit_q.scalars().first()
    if not audit or audit.placement_session_id != session_id or audit.checkpoint != checkpoint:
        raise HTTPException(status_code=404, detail="Audit item not found for this checkpoint")
    return item, audit


async def _apply_existing_answer_if_present(
    db: AsyncSession,
    session: PlacementSession,
    session_id: str,
    checkpoint: int,
    audit: PlacementAudit,
    audit_item_id: str,
    item: PlacementAuditItem,
    idempotency_key: str,
) -> dict | None:
    existing_ev = await db.execute(
        select(PlacementAuditEvent).where(PlacementAuditEvent.idempotency_key == idempotency_key)
    )
    if existing_ev.scalars().first():
        await _reapply_audit_side_effects(db, session_id, checkpoint, audit_item_id, item)
        await _complete_batch_if_ready(db, session, checkpoint, audit, item.sample_batch)
        await db.commit()
        return {"status": "success", "message": "Idempotent answer recorded"}

    existing_item_ev = await db.execute(
        select(PlacementAuditEvent).where(PlacementAuditEvent.placement_audit_item_id == audit_item_id)
    )
    if existing_item_ev.scalars().first():
        await _reapply_audit_side_effects(db, session_id, checkpoint, audit_item_id, item)
        await _complete_batch_if_ready(db, session, checkpoint, audit, item.sample_batch)
        await db.commit()
        return {"status": "success", "message": "Audit item already answered"}
    return None


async def _reapply_audit_side_effects(
    db: AsyncSession,
    session_id: str,
    checkpoint: int,
    audit_item_id: str,
    item: PlacementAuditItem,
) -> None:
    await apply_audit_side_effects(
        db,
        session_id,
        checkpoint,
        audit_item_id,
        item,
        item.resolved_result or PlacementAuditResult.CORRECT,
    )
    await db.flush()
    await sync_activation_queue_for_card(db, session_id, item.card_id)
    await db.flush()


def _resolve_audit_answer(selected_option_id: str, correct_option_id: str) -> tuple[PlacementAuditResult, bool | None]:
    if selected_option_id == AuditSpecialOption.PROBLEMATIC:
        return PlacementAuditResult.PROBLEMATIC, None
    if selected_option_id == AuditSpecialOption.UNKNOWN or selected_option_id != correct_option_id:
        return PlacementAuditResult.INCORRECT, False
    return PlacementAuditResult.CORRECT, True


async def _complete_batch_if_ready(
    db: AsyncSession,
    session: PlacementSession,
    checkpoint: int,
    audit: PlacementAudit,
    sample_batch: int,
) -> None:
    batch_items_q = await db.execute(
        select(PlacementAuditItem).where(
            PlacementAuditItem.placement_audit_id == audit.id,
            PlacementAuditItem.sample_batch == sample_batch,
        )
    )
    batch_items = batch_items_q.scalars().all()
    batch_item_ids = [item.id for item in batch_items]

    answered_q = await db.execute(
        select(func.count()).select_from(PlacementAuditEvent).where(PlacementAuditEvent.placement_audit_item_id.in_(batch_item_ids))
    )
    if (answered_q.scalar() or 0) < len(batch_items):
        return

    error_rate = await _refresh_audit_error_rate(db, audit)
    if sample_batch == 1 and error_rate >= 0.20:
        second_batch_created = await _maybe_create_second_batch(db, session, checkpoint, audit, batch_items)
        if second_batch_created:
            return

    audit.status = PlacementAuditStatus.COMPLETED
    await db.flush()
    await evaluate_session_status(db, session)


async def _refresh_audit_error_rate(db: AsyncSession, audit: PlacementAudit) -> float:
    all_items_q = await db.execute(
        select(PlacementAuditItem).where(PlacementAuditItem.placement_audit_id == audit.id)
    )
    all_items = all_items_q.scalars().all()
    incorrect_count = sum(1 for item in all_items if item.resolved_result == PlacementAuditResult.INCORRECT)
    correct_count = sum(1 for item in all_items if item.resolved_result == PlacementAuditResult.CORRECT)
    total_valid = incorrect_count + correct_count
    audit.error_rate = (incorrect_count / total_valid) if total_valid > 0 else 0.0
    await db.flush()
    return audit.error_rate


async def _maybe_create_second_batch(
    db: AsyncSession,
    session: PlacementSession,
    checkpoint: int,
    audit: PlacementAudit,
    first_batch_items: list[PlacementAuditItem],
) -> bool:
    manifest = json.loads(session.manifest_json)
    knowns = await get_effective_segment_knowns(db, session.id, checkpoint, manifest)
    first_batch_card_ids = {item.card_id for item in first_batch_items}
    remaining_knowns = [card_id for card_id in knowns if card_id not in first_batch_card_ids]
    if not remaining_knowns:
        return False

    second_sample_size = max(1, math.ceil(len(knowns) * 0.10))
    rng = random.Random(f"{session.id}_{checkpoint}_audit2")
    second_candidates = rng.sample(remaining_knowns, min(second_sample_size, len(remaining_knowns)))
    deck_ids = await placement_deck_ids_for_session(db, session.id)
    await build_audit_items_with_replacements(
        db=db,
        audit_id=audit.id,
        session_id=session.id,
        deck_ids=deck_ids,
        selected_card_ids=second_candidates,
        replacement_card_ids=remaining_knowns,
        sample_batch=2,
        issue_source="audit_generator_batch2",
        rng=rng,
    )
    return True
