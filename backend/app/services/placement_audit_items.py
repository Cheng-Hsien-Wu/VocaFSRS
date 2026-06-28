import json
import random
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import DataQualityStatus
from app.models import Card, DataQualityIssue, PlacementAuditItem
from app.services.distractors import UnsafeOptionsException, generate_distractors_for_card


def audit_generator_issue(card: Card, source: str) -> DataQualityIssue:
    return DataQualityIssue(
        id=str(uuid.uuid4()),
        card_id=card.id,
        source=source,
        issue_type="unsafe_options",
        note=f"Failed to generate 3 safe distractors for card {card.english}",
        status=DataQualityStatus.OPEN,
    )


async def load_card(db: AsyncSession, card_id: str) -> Card | None:
    card_q = await db.execute(select(Card).where(Card.id == card_id))
    return card_q.scalars().first()


async def build_audit_item(
    db: AsyncSession,
    audit_id: str,
    card_id: str,
    sample_batch: int,
    session_id: str,
    deck_ids: list[str],
    issue_source: str,
) -> PlacementAuditItem | None:
    card = await load_card(db, card_id)
    if not card:
        return None

    try:
        options = await generate_distractors_for_card(db, card, session_id, deck_ids)
    except UnsafeOptionsException:
        db.add(audit_generator_issue(card, issue_source))
        return None

    return PlacementAuditItem(
        id=str(uuid.uuid4()),
        placement_audit_id=audit_id,
        card_id=card.id,
        sample_batch=sample_batch,
        options_json=json.dumps(options),
        correct_option_id=card.id,
    )


async def build_audit_items_with_replacements(
    db: AsyncSession,
    audit_id: str,
    session_id: str,
    deck_ids: list[str],
    selected_card_ids: list[str],
    replacement_card_ids: list[str],
    sample_batch: int,
    issue_source: str,
    rng: random.Random,
) -> list[PlacementAuditItem]:
    used_card_ids = set(selected_card_ids)
    audit_items: list[PlacementAuditItem] = []

    for card_id in selected_card_ids:
        item = await build_audit_item(db, audit_id, card_id, sample_batch, session_id, deck_ids, issue_source)
        if not item:
            item = await _build_replacement_item(
                db=db,
                audit_id=audit_id,
                session_id=session_id,
                deck_ids=deck_ids,
                card_id=card_id,
                replacement_card_ids=replacement_card_ids,
                used_card_ids=used_card_ids,
                sample_batch=sample_batch,
                issue_source=issue_source,
                rng=rng,
            )

        if item:
            used_card_ids.add(item.card_id)
            db.add(item)
            audit_items.append(item)

    return audit_items


async def _build_replacement_item(
    db: AsyncSession,
    audit_id: str,
    session_id: str,
    deck_ids: list[str],
    card_id: str,
    replacement_card_ids: list[str],
    used_card_ids: set[str],
    sample_batch: int,
    issue_source: str,
    rng: random.Random,
) -> PlacementAuditItem | None:
    remaining_replacements = [cid for cid in replacement_card_ids if cid not in used_card_ids and cid != card_id]
    while remaining_replacements:
        replacement_card_id = rng.choice(remaining_replacements)
        item = await build_audit_item(
            db,
            audit_id,
            replacement_card_id,
            sample_batch,
            session_id,
            deck_ids,
            issue_source,
        )
        if item:
            return item
        remaining_replacements.remove(replacement_card_id)
    return None
