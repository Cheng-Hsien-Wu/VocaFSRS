import random

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Card, ConfusionCount, DeckCard
from app.utils import normalize_text


class UnsafeOptionsException(Exception):
    pass


async def generate_distractors_for_card(
    db: AsyncSession,
    target_card: Card,
    session_id: str,
    deck_ids: list[str],
) -> list[dict]:
    distractors = []
    seen_ids = {target_card.id}

    conf_stmt = (
        select(Card)
        .join(ConfusionCount, ConfusionCount.selected_wrong_card_id == Card.id)
        .where(
            ConfusionCount.target_card_id == target_card.id,
            Card.active == True,
            Card.study_eligible == True,
        )
        .limit(20)
    )
    if deck_ids:
        conf_stmt = conf_stmt.join(DeckCard, DeckCard.card_id == Card.id).where(DeckCard.deck_id.in_(deck_ids))
    conf_res = await db.execute(conf_stmt)
    for card in conf_res.scalars().all():
        if card.id not in seen_ids:
            seen_ids.add(card.id)
            distractors.append(card)

    if len(distractors) < 20 and target_card.part_of_speech:
        pos_stmt = (
            select(Card)
            .where(
                Card.part_of_speech == target_card.part_of_speech,
                Card.id != target_card.id,
                Card.active == True,
                Card.study_eligible == True,
            )
            .limit(50)
        )
        if deck_ids:
            pos_stmt = pos_stmt.join(DeckCard, DeckCard.card_id == Card.id).where(DeckCard.deck_id.in_(deck_ids))
        pos_res = await db.execute(pos_stmt)
        for card in pos_res.scalars().all():
            if card.id not in seen_ids:
                seen_ids.add(card.id)
                distractors.append(card)
                if len(distractors) >= 20:
                    break

    if len(distractors) < 30 and deck_ids:
        deck_stmt = (
            select(Card)
            .join(DeckCard, DeckCard.card_id == Card.id)
            .where(
                DeckCard.deck_id.in_(deck_ids),
                Card.id != target_card.id,
                Card.active == True,
                Card.study_eligible == True,
            )
            .limit(60)
        )
        deck_res = await db.execute(deck_stmt)
        for card in deck_res.scalars().all():
            if card.id not in seen_ids:
                seen_ids.add(card.id)
                distractors.append(card)
                if len(distractors) >= 30:
                    break

    if len(distractors) < 40:
        target_len = len(target_card.chinese_meaning or "")
        len_stmt = select(Card).where(Card.id != target_card.id, Card.active == True, Card.study_eligible == True)
        if deck_ids:
            len_stmt = len_stmt.join(DeckCard, DeckCard.card_id == Card.id).where(DeckCard.deck_id.in_(deck_ids))
        len_stmt = len_stmt.limit(200)
        len_res = await db.execute(len_stmt)
        all_eligible = list(len_res.scalars().all())
        all_eligible.sort(key=lambda card: abs(len(card.chinese_meaning or "") - target_len))
        for card in all_eligible:
            if card.id not in seen_ids:
                seen_ids.add(card.id)
                distractors.append(card)
                if len(distractors) >= 50:
                    break

    if len(distractors) < 5:
        fallback_stmt = select(Card).where(Card.id != target_card.id, Card.active == True, Card.study_eligible == True)
        if deck_ids:
            fallback_stmt = fallback_stmt.join(DeckCard, DeckCard.card_id == Card.id).where(DeckCard.deck_id.in_(deck_ids))
        fallback_stmt = fallback_stmt.limit(20)
        fallback_res = await db.execute(fallback_stmt)
        for card in fallback_res.scalars().all():
            if card.id not in seen_ids:
                seen_ids.add(card.id)
                distractors.append(card)

    final_distractors = []
    seen_meanings = {normalize_text(target_card.chinese_meaning).lower()}
    for card in distractors:
        norm_meaning = normalize_text(card.chinese_meaning).lower()
        if norm_meaning not in seen_meanings:
            seen_meanings.add(norm_meaning)
            final_distractors.append(card)
            if len(final_distractors) == 3:
                break

    if len(final_distractors) < 3:
        raise UnsafeOptionsException(f"Could not generate 3 safe distractors for card {target_card.english}")

    options = [
        {"card_id": target_card.id, "chinese": target_card.chinese_meaning},
        {"card_id": final_distractors[0].id, "chinese": final_distractors[0].chinese_meaning},
        {"card_id": final_distractors[1].id, "chinese": final_distractors[1].chinese_meaning},
        {"card_id": final_distractors[2].id, "chinese": final_distractors[2].chinese_meaning},
    ]

    rng = random.Random(f"{session_id}_{target_card.id}_options")
    rng.shuffle(options)
    return options
