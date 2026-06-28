import uuid

from sqlalchemy import select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import (
    ActivationType,
    PlacementProjectionResult,
    QueueStatus,
)
from app.models import ActivationQueue, PlacementEvent
from app.services.placement_event_state import effective_placement_result


async def sync_activation_queue_for_card(
    db: AsyncSession,
    session_id: str,
    card_id: str,
) -> None:
    await sync_activation_queue_for_cards(db, session_id, {card_id})


async def sync_activation_queue_for_cards(
    db: AsyncSession,
    session_id: str,
    card_ids: set[str],
) -> None:
    if not card_ids:
        return

    result = await db.execute(
        select(PlacementEvent)
        .where(
            PlacementEvent.session_id == session_id,
            PlacementEvent.card_id.in_(card_ids),
        )
        .order_by(PlacementEvent.created_at, PlacementEvent.id)
    )
    events_by_card: dict[str, list[PlacementEvent]] = {
        card_id: [] for card_id in card_ids
    }
    for event in result.scalars().all():
        events_by_card.setdefault(event.card_id, []).append(event)

    skipped_card_ids = []
    activation_rows = []
    for card_id in card_ids:
        effective_result = effective_placement_result(events_by_card.get(card_id, []))
        activation = _activation_for_result(effective_result)
        if activation is None:
            skipped_card_ids.append(card_id)
            continue
        activation_type, priority = activation
        activation_rows.append({
            "id": str(uuid.uuid4()),
            "card_id": card_id,
            "activation_type": activation_type,
            "priority": priority,
            "status": QueueStatus.PENDING,
            "activated_at": None,
        })

    if skipped_card_ids:
        await db.execute(
            update(ActivationQueue)
            .where(ActivationQueue.card_id.in_(skipped_card_ids))
            .values(status=QueueStatus.SKIPPED)
        )
    if activation_rows:
        statement = sqlite_insert(ActivationQueue).values(activation_rows)
        statement = statement.on_conflict_do_update(
            index_elements=[ActivationQueue.card_id],
            set_={
                "activation_type": statement.excluded.activation_type,
                "priority": statement.excluded.priority,
                "status": QueueStatus.PENDING,
                "activated_at": None,
            },
        )
        await db.execute(statement)


def _activation_for_result(
    result: PlacementProjectionResult,
) -> tuple[ActivationType, int] | None:
    if result == PlacementProjectionResult.UNKNOWN:
        return ActivationType.LEARN_UNKNOWN, 3
    if result == PlacementProjectionResult.FUZZY:
        return ActivationType.LEARN_FUZZY, 2
    if result == PlacementProjectionResult.KNOWN:
        return ActivationType.VERIFY_KNOWN, 1
    return None
