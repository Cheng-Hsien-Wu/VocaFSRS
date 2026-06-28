import os
import sys
import asyncio
from datetime import datetime, timezone, timedelta
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession

env = os.getenv("VOCAB_ENV", "development")
if env == "production":
    print("CRITICAL: Cannot run seed_mistakes_export.py in production.")
    sys.exit(1)

db_url = os.getenv("DATABASE_URL")
if not db_url or "vocab.db" in db_url:
    print("CRITICAL: seed_mistakes_export.py must be run with a dedicated temporary DATABASE_URL.")
    sys.exit(1)

from app.database import Base
from app.models import Card, Deck, DeckCard, ReviewLog, ReviewState, ConfusionCount
from app.constants import DEFAULT_DECK_NAME
from app.utils import normalize_text, get_card_fingerprint

async def seed_mistakes_export():
    engine = create_async_engine(db_url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    
    async with session_factory() as session:
        # Clear existing data first
        await session.execute(delete(ConfusionCount))
        await session.execute(delete(ReviewLog))
        await session.execute(delete(ReviewState))
        await session.execute(delete(DeckCard))
        await session.execute(delete(Card))
        await session.execute(delete(Deck))
        await session.commit()

        # 1. Seed Deck
        deck = Deck(id="imported-deck", name=DEFAULT_DECK_NAME, enabled=True, deck_type="imported")
        session.add(deck)
        
        # 2. Seed Cards
        card_data = [
            ("c_f_00", "preclude", "排除、阻止、妨礙", "This is a long example sentence that precludes any ambiguity.\nIt contains multiple lines to test multiline handling in CSV exports.", "這是一個排除任何歧義的長例句。\n它包含多行，以測試 CSV 匯出中的多行處理。"),
            ("c_f_01", "precedent", "先例、前例、慣例", "We must examine the legal precedent before making a final decision.", "在做出最終決定之前，我們必須審查法律先例。"),
            ("c_f_02", "precarious", "不穩定的、危險的", "The ladder was in a precarious position on the wet grass.", "梯子在潮濕的草地上處於不穩定的位置。"),
            ("c_f_03", "precaution", "預防措施、防備", "Taking proper precautions can prevent accidents in the laboratory.", "採取適當的預防措施可以防止實驗室發生事故。"),
            ("c_f_04", "precision", "精確、精準、清晰", "The machine cuts the metal sheets with high precision.", "該機器以高精度切割金屬板。")
        ]
        
        cards = []
        for cid, eng, chi, ex, ex_tr in card_data:
            fp = get_card_fingerprint(eng, chi, "noun")
            card = Card(
                id=cid,
                english=eng,
                english_normalized=normalize_text(eng).lower(),
                chinese_meaning=chi,
                chinese_normalized=normalize_text(chi),
                part_of_speech="noun",
                fingerprint=fp,
                fingerprint_version=1,
                active=True,
                study_eligible=True,
                example_sentence=ex,
                example_translation=ex_tr
            )
            session.add(card)
            cards.append(card)
            
            # Link to deck
            dc = DeckCard(deck_id="imported-deck", card_id=cid)
            session.add(dc)
            
        await session.commit()
        
        # 3. Seed ReviewState for card c_f_00 (preclude)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        state_0 = ReviewState(
            card_id="c_f_00",
            state=1, # Learning
            due=now + timedelta(days=1),
            stability=2.0,
            difficulty=3.0,
            elapsed_days=1,
            scheduled_days=2,
            reps=5,
            lapses=2,
            last_review=now
        )
        session.add(state_0)
        
        # 4. Seed ReviewLogs for preclude to generate Again/Hard counts
        logs = [
            # Again (rating=1) 2 days ago, confused with precedent
            ReviewLog(
                id="log_f_0",
                card_id="c_f_00",
                selected_option_card_id="c_f_01",
                correct_option_card_id="c_f_00",
                was_correct=False,
                rating=1,
                reviewed_at=now - timedelta(days=2),
                idempotency_key="idemp_f_0"
            ),
            # Again (rating=1) 1 day ago, confused with precarious
            ReviewLog(
                id="log_f_1",
                card_id="c_f_00",
                selected_option_card_id="c_f_02",
                correct_option_card_id="c_f_00",
                was_correct=False,
                rating=1,
                reviewed_at=now - timedelta(days=1),
                idempotency_key="idemp_f_1"
            ),
            # Again (rating=1) in the current local day, "不知道" selection
            ReviewLog(
                id="log_f_2",
                card_id="c_f_00",
                selected_option_card_id="unknown",
                correct_option_card_id="c_f_00",
                was_correct=False,
                rating=1,
                reviewed_at=now - timedelta(minutes=5),
                idempotency_key="idemp_f_2"
            ),
            # Hard (rating=2) before the latest Again result. Mistake filters
            # use the latest rating, while counts still include history.
            ReviewLog(
                id="log_f_3",
                card_id="c_f_00",
                selected_option_card_id="c_f_00",
                correct_option_card_id="c_f_00",
                was_correct=True,
                rating=2,
                reviewed_at=now - timedelta(hours=18),
                idempotency_key="idemp_f_3"
            )
        ]
        session.add_all(logs)
        
        # 5. Seed Confusion Counts
        confusions = [
            ConfusionCount(target_card_id="c_f_00", selected_wrong_card_id="c_f_01", occurrence_count=5, last_occurred_at=now - timedelta(days=2)),
            ConfusionCount(target_card_id="c_f_00", selected_wrong_card_id="c_f_02", occurrence_count=3, last_occurred_at=now - timedelta(days=1)),
            ConfusionCount(target_card_id="c_f_00", selected_wrong_card_id="unknown", occurrence_count=2, last_occurred_at=now - timedelta(minutes=5))
        ]
        session.add_all(confusions)
        
        await session.commit()
        print(f"Mistakes export seed complete on database: {db_url}")

    await engine.dispose()

if __name__ == "__main__":
    asyncio.run(seed_mistakes_export())
