import json
from datetime import datetime, timezone

from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import (
    PlacementAnswer,
    PlacementSessionStatus,
    QueueStatus,
)
from app.database import get_db
from app.models import (
    ActivationQueue,
    Deck,
    DeckCard,
    PlacementItem,
    PlacementSession,
)
from main import app


async def get_test_db():
    if get_db not in app.dependency_overrides:
        from tests.conftest import override_get_db
        app.dependency_overrides[get_db] = override_get_db
    async for db in app.dependency_overrides[get_db]():
        yield db


async def setup_study_data(db: AsyncSession):
    fixture_id = "placement-complete-study-fixture"
    await db.execute(
        delete(PlacementItem).where(
            PlacementItem.placement_session_id == fixture_id,
        )
    )
    await db.execute(
        delete(PlacementSession).where(PlacementSession.id == fixture_id)
    )

    db.add(Deck(id="deck-study", name="Study Deck", enabled=True))
    await db.commit()

    for index in range(5):
        db.add(DeckCard(deck_id="deck-study", card_id=f"c00{index}"))
    await db.commit()

    db.add_all([
        ActivationQueue(
            id="aq-0",
            card_id="c000",
            activation_type="learn_unknown",
            priority=3,
            status=QueueStatus.PENDING,
        ),
        ActivationQueue(
            id="aq-1",
            card_id="c001",
            activation_type="learn_unknown",
            priority=3,
            status=QueueStatus.PENDING,
        ),
        ActivationQueue(
            id="aq-2",
            card_id="c002",
            activation_type="learn_fuzzy",
            priority=2,
            status=QueueStatus.PENDING,
        ),
        ActivationQueue(
            id="aq-3",
            card_id="c003",
            activation_type="verify_known",
            priority=1,
            status=QueueStatus.PENDING,
        ),
    ])
    db.add(PlacementSession(
        id=fixture_id,
        requested_count=5,
        current_position=5,
        status=PlacementSessionStatus.COMPLETED,
        manifest_json=json.dumps([
            {"position": index, "card_id": f"c00{index}"}
            for index in range(5)
        ]),
    ))
    db.add_all([
        PlacementItem(
            id=f"placement-item-study-fixture-{index}",
            placement_session_id=fixture_id,
            position=index,
            card_id=f"c00{index}",
            placement_result=PlacementAnswer.KNOWN,
            answered_at=datetime.now(timezone.utc).replace(tzinfo=None),
            undone=False,
        )
        for index in range(5)
    ])
    await db.commit()
