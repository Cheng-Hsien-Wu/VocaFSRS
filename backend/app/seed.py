import asyncio
import os
import sys
from sqlalchemy import select
from app.database import AsyncSessionLocal
from app.models import Card, Deck, DeckCard, ActivationQueue
from app.constants import QueueStatus
from app.utils import normalize_text, get_card_fingerprint

# Safety check: ensure seed is only run in dev or test environments
env = os.getenv("VOCAB_ENV", "development")
if env == "production":
    print("CRITICAL: Cannot run seed script in production environment.")
    sys.exit(1)

async def seed_data():
    async with AsyncSessionLocal() as session:
        # Seed sample cards to support tests and local development
        mock_data = [
            ("apple", "蘋果", "noun"),
            ("banana", "香蕉", "noun"),
            ("cat", "貓", "noun"),
            ("dog", "狗", "noun"),
            ("elephant", "大象", "noun"),
            ("fish", "魚", "noun"),
            ("grape", "葡萄", "noun"),
            ("house", "房子", "noun"),
            ("ice", "冰", "noun"),
            ("juice", "果汁", "noun"),
            ("kite", "風箏", "noun"),
            ("lion", "獅子", "noun"),
            ("monkey", "猴子", "noun"),
            ("nest", "鳥巢", "noun"),
            ("orange", "橘子", "noun"),
            ("pig", "豬", "noun"),
            ("queen", "女王", "noun"),
            ("rabbit", "兔子", "noun"),
            ("sun", "太陽", "noun"),
            ("tree", "樹", "noun"),
            ("umbrella", "雨傘", "noun"),
            ("van", "箱型車", "noun"),
            ("water", "水", "noun"),
            ("xylophone", "木琴", "noun"),
            ("yacht", "遊艇", "noun"),
            ("bite the bullet", "咬緊牙關", "idiom"),
        ]
        
        deck = await session.get(Deck, "study-test-deck")
        if not deck:
            deck = Deck(id="study-test-deck", name="Study Test Deck", enabled=True)
            session.add(deck)

        seeded_count = 0
        for i, (eng, chi, pos) in enumerate(mock_data):
            card_id = f"c{i+1:03d}"
            fp = get_card_fingerprint(eng, chi, pos)
            
            # 1. Ensure Card exists
            existing_fp = await session.execute(select(Card).where(Card.fingerprint == fp))
            card = existing_fp.scalars().first()
            if not card:
                existing_id = await session.execute(select(Card).where(Card.id == card_id))
                card = existing_id.scalars().first()
                
            if not card:
                card = Card(
                    id=card_id,
                    english=eng,
                    english_normalized=normalize_text(eng).lower(),
                    chinese_meaning=chi,
                    chinese_normalized=normalize_text(chi),
                    part_of_speech=pos,
                    fingerprint=fp,
                    fingerprint_version=1,
                    active=True,
                    study_eligible=True
                )
                session.add(card)
                seeded_count += 1
            else:
                card.active = True
                card.study_eligible = True
        
        # Flush first pass to ensure card rows are created before foreign key checks run
        await session.flush()

        # 2. Ensure DeckCard and ActivationQueue items exist
        for i, (eng, chi, pos) in enumerate(mock_data):
            card_id = f"c{i+1:03d}"
            
            existing_dc = await session.execute(select(DeckCard).where(DeckCard.deck_id == "study-test-deck", DeckCard.card_id == card_id))
            if not existing_dc.scalars().first():
                dc = DeckCard(deck_id="study-test-deck", card_id=card_id)
                session.add(dc)

            existing_aq = await session.execute(select(ActivationQueue).where(ActivationQueue.card_id == card_id))
            if not existing_aq.scalars().first():
                aq = ActivationQueue(
                    id=f"aq_{card_id}",
                    card_id=card_id,
                    activation_type="learn_unknown",
                    priority=3,
                    status=QueueStatus.PENDING
                )
                session.add(aq)
        
        await session.commit()
        print("Idempotent seed complete: Seeding checked and updated all cards, decks, and activations")

if __name__ == "__main__":
    asyncio.run(seed_data())
