import os
import sys
import asyncio
from datetime import datetime, timedelta, timezone
from sqlalchemy import select
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

env = os.getenv("VOCAB_ENV", "development")
if env == "production":
    print("CRITICAL: Cannot run seed_study.py in production.")
    sys.exit(1)

db_url = os.getenv("DATABASE_URL")
if not db_url or "vocab.db" in db_url:
    print("CRITICAL: seed_study.py must be run with a dedicated temporary DATABASE_URL.")
    sys.exit(1)

from app.database import Base
from app.constants import QueueStatus
from app.models import ActivationQueue, Card, Deck, DeckCard, ReviewState
from app.utils import normalize_text, get_card_fingerprint

async def seed_study():
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    
    async with session_factory() as session:
        deck = await session.get(Deck, "study-test-deck")
        if not deck:
            deck = Deck(id="study-test-deck", name="Study Test Deck", enabled=True)
            session.add(deck)
            
        seeded_count = 0
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        for i in range(50):
            card_id = f"c_study_{i:02d}"
            eng = f"word_{i}"
            chi = f"測試中文_{i}"
            pos = "verb"
            fp = get_card_fingerprint(eng, chi, pos)
            
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
                study_eligible=True,
                example_sentence=f"This is an example sentence for {eng}.",
                example_translation=f"這是 {eng} 的測試翻譯例句。"
            )
            session.add(card)
            
            dc = DeckCard(deck_id="study-test-deck", card_id=card_id)
            session.add(dc)
            
            # Put in Activation Queue so they are study-ready
            # Mix the types: 20 unknown, 20 fuzzy, 10 verify_known
            if i < 20:
                act_type = "learn_unknown"
                priority = 3
            elif i < 40:
                act_type = "learn_fuzzy"
                priority = 2
            else:
                act_type = "verify_known"
                priority = 1
                
            aq = ActivationQueue(
                id=f"aq_study_{i:02d}",
                card_id=card_id,
                activation_type=act_type,
                priority=priority,
                status=QueueStatus.PENDING
            )
            session.add(aq)

            session.add(ReviewState(
                card_id=card_id,
                state=2,
                due=now - timedelta(minutes=1),
                stability=2.0,
                difficulty=5.0,
                elapsed_days=1,
                scheduled_days=1,
                reps=1,
                lapses=0,
                last_review=now - timedelta(days=1),
                scheduler_name="test-seed",
                scheduler_version="test",
                parameters_version="test",
            ))
            
            seeded_count += 1
            
        if seeded_count > 0:
            await session.commit()
            print(f"Study seed complete: Added {seeded_count} study cards to {db_url}")
        else:
            print("Study seed complete: No new cards added")
            
    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(seed_study())
