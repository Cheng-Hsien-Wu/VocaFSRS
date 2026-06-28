from dataclasses import dataclass

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import (
    ACTIVE_PLACEMENT_STATUSES,
    CardQualityStatus,
    PlacementGateStatus,
    PlacementSessionStatus,
)
from app.models import Card, DeckCard, PlacementSession
from app.services.placement_candidates import placement_candidate_query


@dataclass(frozen=True)
class PlacementGate:
    total_eligible_count: int
    remaining_count: int
    active_session_id: str | None
    active_session_status: PlacementSessionStatus | None

    @property
    def is_complete(self) -> bool:
        return (
            self.total_eligible_count > 0
            and self.remaining_count == 0
            and self.active_session_id is None
        )

    @property
    def status(self) -> PlacementGateStatus:
        if self.total_eligible_count == 0:
            return PlacementGateStatus.NO_CARDS
        if self.active_session_id:
            return PlacementGateStatus.IN_PROGRESS
        if self.remaining_count > 0:
            return PlacementGateStatus.REQUIRED
        return PlacementGateStatus.COMPLETE

    def to_response(self) -> dict:
        return {
            "status": self.status,
            "complete": self.is_complete,
            "total_eligible_count": self.total_eligible_count,
            "remaining_count": self.remaining_count,
            "active_session_id": self.active_session_id,
            "active_session_status": self.active_session_status,
        }


async def get_placement_gate(db: AsyncSession, deck_ids: list[str] | None = None) -> PlacementGate:
    active_q = await db.execute(
        select(PlacementSession.id, PlacementSession.status)
        .where(PlacementSession.status.in_(ACTIVE_PLACEMENT_STATUSES))
        .order_by(PlacementSession.created_at.desc())
        .limit(1)
    )
    active = active_q.first()

    total_stmt = select(func.count(func.distinct(Card.id))).where(
        Card.active == True,
        Card.study_eligible == True,
        or_(Card.data_quality_status.is_(None), Card.data_quality_status != CardQualityStatus.PROBLEMATIC),
    )
    if deck_ids:
        total_stmt = total_stmt.join(DeckCard, DeckCard.card_id == Card.id).where(DeckCard.deck_id.in_(deck_ids))
    total_q = await db.execute(total_stmt)

    remaining_q = await db.execute(select(func.count()).select_from(placement_candidate_query(deck_ids).subquery()))

    return PlacementGate(
        total_eligible_count=int(total_q.scalar() or 0),
        remaining_count=int(remaining_q.scalar() or 0),
        active_session_id=active.id if active else None,
        active_session_status=active.status if active else None,
    )
