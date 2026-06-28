from sqlalchemy import or_, select

from app.constants import CardQualityStatus, PlacementSessionStatus
from app.models import Card, DeckCard, PlacementItem, PlacementSession, ReviewState


def placement_candidate_query(deck_ids: list[str] | None = None):
    query = select(Card).where(
        Card.active == True,
        Card.study_eligible == True,
        or_(Card.data_quality_status.is_(None), Card.data_quality_status != CardQualityStatus.PROBLEMATIC),
        ~select(PlacementItem.id)
        .join(PlacementSession, PlacementSession.id == PlacementItem.placement_session_id)
        .where(
            PlacementItem.card_id == Card.id,
            PlacementItem.answered_at.is_not(None),
            PlacementItem.undone == False,
            PlacementSession.status != PlacementSessionStatus.ABANDONED,
        )
        .exists(),
        ~select(ReviewState.card_id)
        .where(ReviewState.card_id == Card.id)
        .exists(),
    )
    if deck_ids:
        query = query.join(DeckCard, DeckCard.card_id == Card.id).where(DeckCard.deck_id.in_(deck_ids))
    return query.distinct()
