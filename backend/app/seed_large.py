import os
import sys
import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

# Safety: enforce dev/test env only
env = os.getenv("VOCAB_ENV", "development")
if env == "production":
    print("CRITICAL: Cannot run seed_large.py in production.")
    sys.exit(1)

# Enforce isolated database path. Never allow modifying the default vocab.db!
db_url = os.getenv("DATABASE_URL")
if not db_url or "vocab.db" in db_url:
    print("CRITICAL: seed_large.py must be run with a dedicated temporary DATABASE_URL.")
    print("Example: DATABASE_URL=sqlite+aiosqlite:///backend/data/temp_scale_test.db")
    sys.exit(1)

from app.database import Base
from app.models import Card, Deck, DeckCard
from app.utils import normalize_text, get_card_fingerprint

async def seed_large():
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    
    async with session_factory() as session:
        # Create a test deck
        deck = await session.get(Deck, "scale-test-deck")
        if not deck:
            deck = Deck(id="scale-test-deck", name="Scale Test Deck", enabled=True)
            session.add(deck)
            
        seeded_count = 0
        # Seed 300 cards
        for i in range(300):
            card_id = f"scale_{i:04d}"
            eng = f"scale_{i}"
            chi = f"測試中文_{i}"
            pos = "noun"
            fp = get_card_fingerprint(eng, chi, pos)
            
            # Idempotency checks
            existing_fp = await session.execute(select(Card).where(Card.fingerprint == fp))
            if existing_fp.scalars().first():
                continue
                
            existing_id = await session.execute(select(Card).where(Card.id == card_id))
            if existing_id.scalars().first():
                continue
                
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
            
            # Link card to deck
            dc = DeckCard(deck_id="scale-test-deck", card_id=card_id)
            session.add(dc)
            
            seeded_count += 1
            
        if seeded_count > 0:
            await session.commit()
            print(f"Idempotent large seed complete: Added {seeded_count} cards to {db_url}")
        else:
            print("Idempotent large seed complete: No new cards added")
            
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(seed_large())
