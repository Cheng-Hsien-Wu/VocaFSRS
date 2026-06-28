from datetime import datetime, timezone, timedelta
from typing import List, Optional
from fastapi import APIRouter, Depends, Query, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc, and_, or_
from sqlalchemy.orm import aliased
from sqlalchemy.sql.expression import case

from app.database import get_db
from app.models import Card, DeckCard, ReviewState, ReviewLog, ConfusionCount
from app.services.mistake_exports import MistakeQuery, fetch_mistakes, recent_start

router = APIRouter(prefix="/api/v1", tags=["review_data"])

@router.get("/mistakes")
async def get_mistakes(
    days: Optional[int] = Query(None, description="Filter by reviews in the last N days (7 or 30)"),
    deck_id: Optional[str] = Query(None, description="Filter by deck ID"),
    rating: Optional[str] = Query(None, description="Filter by rating: 'Again' or 'Hard'"),
    repeated_lapses: Optional[bool] = Query(None, description="Filter by repeated lapses (lapses >= 2)"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    start_date = None
    if days:
        start_date = recent_start(days)

    total_count, items = await fetch_mistakes(
        db,
        MistakeQuery(
            start_date=start_date,
            deck_id=deck_id,
            rating=rating,
            repeated_lapses=bool(repeated_lapses),
            page=page,
            limit=limit,
            sort_by="recent",
        ),
    )

    return {
        "total": total_count,
        "page": page,
        "limit": limit,
        "items": items
    }

@router.get("/confusions")
async def get_confusions(
    order_by: str = Query("count", description="Order by: 'count' (occurrence_count) or 'activity' (last_occurred_at)"),
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    db: AsyncSession = Depends(get_db)
):
    TargetCard = aliased(Card)
    WrongCard = aliased(Card)

    # Exclude 「不知道」
    query = (
        select(
            ConfusionCount.occurrence_count,
            ConfusionCount.last_occurred_at,
            TargetCard.id.label("target_id"),
            TargetCard.english.label("target_english"),
            TargetCard.chinese_meaning.label("target_chinese"),
            TargetCard.part_of_speech.label("target_pos"),
            TargetCard.sense_hint.label("target_hint"),
            TargetCard.example_sentence.label("target_example"),
            TargetCard.example_translation.label("target_translation"),
            WrongCard.id.label("wrong_id"),
            WrongCard.english.label("wrong_english"),
            WrongCard.chinese_meaning.label("wrong_chinese"),
            WrongCard.part_of_speech.label("wrong_pos"),
            WrongCard.sense_hint.label("wrong_hint"),
            WrongCard.example_sentence.label("wrong_example"),
            WrongCard.example_translation.label("wrong_translation")
        )
        .select_from(ConfusionCount)
        .join(TargetCard, TargetCard.id == ConfusionCount.target_card_id)
        .join(WrongCard, WrongCard.id == ConfusionCount.selected_wrong_card_id)
        .where(ConfusionCount.selected_wrong_card_id != "unknown")
    )

    if order_by == "activity":
        query = query.order_by(desc(ConfusionCount.last_occurred_at), desc(ConfusionCount.occurrence_count))
    else: # default to count
        query = query.order_by(desc(ConfusionCount.occurrence_count), desc(ConfusionCount.last_occurred_at))

    # Total count
    count_stmt = select(func.count()).select_from(query.subquery())
    total_count = (await db.execute(count_stmt)).scalar() or 0

    # Paginate
    offset = (page - 1) * limit
    paginated_stmt = query.offset(offset).limit(limit)
    res = await db.execute(paginated_stmt)
    rows = res.all()

    items = []
    for r in rows:
        items.append({
            "occurrence_count": r.occurrence_count,
            "last_occurred_at": r.last_occurred_at.isoformat() if r.last_occurred_at else None,
            "target_card": {
                "id": r.target_id,
                "english": r.target_english,
                "chinese_meaning": r.target_chinese,
                "part_of_speech": r.target_pos,
                "sense_hint": r.target_hint,
                "example_sentence": r.target_example,
                "example_translation": r.target_translation
            },
            "confused_card": {
                "id": r.wrong_id,
                "english": r.wrong_english,
                "chinese_meaning": r.wrong_chinese,
                "part_of_speech": r.wrong_pos,
                "sense_hint": r.wrong_hint,
                "example_sentence": r.wrong_example,
                "example_translation": r.wrong_translation
            }
        })

    return {
        "total": total_count,
        "page": page,
        "limit": limit,
        "items": items
    }
