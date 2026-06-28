from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import DEFAULT_DECK_NAME
from app.models import Deck


class DeckScopeError(Exception):
    def __init__(self, message: str, status_code: int = 409):
        super().__init__(message)
        self.message = message
        self.status_code = status_code


async def default_deck_ids(db: AsyncSession) -> list[str]:
    result = await db.execute(
        select(Deck.id)
        .where(
            Deck.name == DEFAULT_DECK_NAME,
            Deck.enabled == True,
            Deck.deck_type == "imported",
        )
        .order_by(Deck.created_at.asc(), Deck.id.asc())
    )
    return [row[0] for row in result.all()]


async def enabled_imported_deck_ids(db: AsyncSession) -> list[str]:
    result = await db.execute(
        select(Deck.id)
        .where(
            Deck.enabled == True,
            Deck.deck_type == "imported",
        )
        .order_by(Deck.created_at.asc(), Deck.id.asc())
    )
    return [row[0] for row in result.all()]


async def resolve_deck_ids(db: AsyncSession, requested_deck_ids: list[str] | None) -> list[str] | None:
    if requested_deck_ids:
        requested_unique = list(dict.fromkeys(requested_deck_ids))
        result = await db.execute(
            select(Deck.id)
            .where(
                Deck.id.in_(requested_unique),
                Deck.enabled == True,
            )
        )
        enabled_ids = {row[0] for row in result.all()}
        missing_ids = [deck_id for deck_id in requested_unique if deck_id not in enabled_ids]
        if missing_ids:
            raise DeckScopeError(
                f"Deck scope includes missing or disabled deck ids: {', '.join(missing_ids)}",
                status_code=400,
            )
        return requested_unique

    ids = await default_deck_ids(db)
    if ids:
        return ids

    imported_ids = await enabled_imported_deck_ids(db)
    if len(imported_ids) <= 1:
        return imported_ids or None

    raise DeckScopeError(
        f"Multiple imported decks exist and no default deck named '{DEFAULT_DECK_NAME}' was found. Select a deck explicitly."
    )


async def resolve_single_default_deck_id(db: AsyncSession) -> str:
    ids = await default_deck_ids(db)
    if len(ids) == 1:
        return ids[0]
    if len(ids) > 1:
        raise DeckScopeError(f"Multiple default decks named '{DEFAULT_DECK_NAME}' were found")

    imported_ids = await enabled_imported_deck_ids(db)
    if len(imported_ids) == 1:
        return imported_ids[0]
    if not imported_ids:
        raise DeckScopeError(f"Default deck '{DEFAULT_DECK_NAME}' was not found", status_code=404)
    raise DeckScopeError(
        f"Multiple imported decks exist and no default deck named '{DEFAULT_DECK_NAME}' was found. Select a deck explicitly."
    )
