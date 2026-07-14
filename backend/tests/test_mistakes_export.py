import pytest
import csv
import io
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient
from sqlalchemy import select, delete, func
from main import app
from app.database import get_db
from app.models import (
    Card, Deck, DeckCard, ReviewState, ReviewLog,
    ActivationQueue, StudySession, SessionItem, ConfusionCount, TypedStudyAnswer,
    StudyPlan
)
from app.constants import DEFAULT_DECK_NAME
from app.utils import get_card_fingerprint

pytestmark = pytest.mark.asyncio

async def get_test_db():
    if get_db not in app.dependency_overrides:
        from tests.conftest import override_get_db
        app.dependency_overrides[get_db] = override_get_db
    async for db in app.dependency_overrides[get_db]():
        yield db

async def setup_mistakes_export_data(db):
    # Clear tables
    await db.execute(delete(ConfusionCount))
    await db.execute(delete(TypedStudyAnswer))
    await db.execute(delete(ReviewLog))
    await db.execute(delete(SessionItem))
    await db.execute(delete(StudySession))
    await db.execute(delete(ReviewState))
    await db.execute(delete(ActivationQueue))
    await db.execute(delete(DeckCard))
    await db.execute(delete(Card))
    await db.execute(delete(Deck))
    await db.commit()

    # Create decks
    deck_a = Deck(id="deck-a", name=DEFAULT_DECK_NAME, enabled=True, deck_type="imported")
    deck_b = Deck(id="deck-b", name="Secondary Deck", enabled=True, deck_type="imported")
    db.add(deck_a)
    db.add(deck_b)
    await db.commit()

    # Create cards
    cards = []
    for i in range(10):
        c_id = f"card-{i}"
        card = Card(
            id=c_id,
            english=f"word-{i}",
            english_normalized=f"word-{i}",
            chinese_meaning=f"繁體中文-{i}",
            chinese_normalized=f"繁體中文-{i}",
            part_of_speech="n.",
            sense_hint=f"hint-{i}",
            example_sentence=f"This is an example sentence for word-{i}.\nIt has multiple lines.",
            example_translation=f"這是 word-{i} 的例句。\n它有多行。",
            fingerprint=get_card_fingerprint(f"word-{i}", f"繁體中文-{i}", "n."),
            fingerprint_version=1,
            active=True,
            study_eligible=True
        )
        db.add(card)
        cards.append(card)
        # Link card-0 to card-7 to deck-a, card-8 and card-9 to deck-b
        target_deck = deck_a.id if i < 8 else deck_b.id
        db.add(DeckCard(deck_id=target_deck, card_id=c_id))
    await db.commit()
    return cards

# ─── 1. Mistakes endpoint tests ───────────────────────────
async def test_mistake_filters_and_pagination(client: AsyncClient):
    async for db in get_test_db():
        cards = await setup_mistakes_export_data(db)
        
        # Add review states and logs
        # card-0: 2 Agains (rating=1), 1 Hard (rating=2), lapses=2
        db.add(ReviewState(
            card_id="card-0", state=1, stability=1.0, difficulty=1.0, elapsed_days=0, scheduled_days=0, reps=3, lapses=2,
            due=datetime.now(timezone.utc).replace(tzinfo=None)
        ))
        db.add_all([
            ReviewLog(id="log-0-1", card_id="card-0", rating=1, was_correct=False, reviewed_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2), selected_option_card_id="card-1"),
            ReviewLog(id="log-0-2", card_id="card-0", rating=1, was_correct=False, reviewed_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1), selected_option_card_id="card-2"),
            ReviewLog(id="log-0-3", card_id="card-0", rating=2, was_correct=True, reviewed_at=datetime.now(timezone.utc).replace(tzinfo=None))
        ])
        
        # card-1: 1 Hard (rating=2), lapses=0
        db.add(ReviewState(
            card_id="card-1", state=1, stability=1.0, difficulty=1.0, elapsed_days=0, scheduled_days=0, reps=1, lapses=0,
            due=datetime.now(timezone.utc).replace(tzinfo=None)
        ))
        db.add(ReviewLog(id="log-1-1", card_id="card-1", rating=2, was_correct=True, reviewed_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=8)))
        
        await db.commit()

    # Test GET /api/v1/mistakes (all)
    res = await client.get("/api/v1/mistakes")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2
    assert len(data["items"]) == 2
    
    # Assert card-0 fields
    card_0_item = [item for item in data["items"] if item["english"] == "word-0"][0]
    assert card_0_item["again_count"] == 2
    assert card_0_item["hard_count"] == 1
    assert card_0_item["lapses"] == 2
    assert card_0_item["confused_word"] == "word-2"  # card-2 was the most recent incorrect choice
    assert card_0_item["selected_wrong_meaning"] == "繁體中文-2"
    assert "multiple lines" in card_0_item["example_sentence"]

    # Filter by last 7 days (should exclude card-1 because its log is 8 days ago)
    res_7d = await client.get("/api/v1/mistakes?days=7")
    assert res_7d.status_code == 200
    assert res_7d.json()["total"] == 1
    assert res_7d.json()["items"][0]["english"] == "word-0"

    # Filter by rating=Again
    res_again = await client.get("/api/v1/mistakes?rating=Again")
    assert res_again.status_code == 200
    assert res_again.json()["total"] == 0

    # Filter by rating=Hard uses the latest review result, not historical counts.
    res_hard = await client.get("/api/v1/mistakes?rating=Hard")
    assert res_hard.status_code == 200
    assert res_hard.json()["total"] == 2

    # Filter by repeated lapses
    res_lapses = await client.get("/api/v1/mistakes?repeated_lapses=true")
    assert res_lapses.status_code == 200
    assert res_lapses.json()["total"] == 1
    assert res_lapses.json()["items"][0]["english"] == "word-0"

    # Pagination test
    res_page = await client.get("/api/v1/mistakes?page=1&limit=1")
    assert res_page.status_code == 200
    assert len(res_page.json()["items"]) == 1
    assert res_page.json()["total"] == 2

async def test_mistakes_use_latest_review_not_historical_error(client: AsyncClient):
    async for db in get_test_db():
        await setup_mistakes_export_data(db)
        session = StudySession(id="latest-session", requested_size=2, mode="fixed", sync_status="completed", cards_answered=2)
        db.add(session)
        db.add_all([
            SessionItem(
                id="latest-item-corrected",
                study_session_id=session.id,
                position=0,
                target_card_id="card-0",
                correct_option_card_id="card-0",
                option_card_ids_json="[]",
            ),
            SessionItem(
                id="latest-item-hard",
                study_session_id=session.id,
                position=1,
                target_card_id="card-1",
                correct_option_card_id="card-1",
                option_card_ids_json="[]",
            ),
        ])
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add_all([
            ReviewLog(id="corrected-hard", card_id="card-0", study_session_id=session.id, session_item_id="latest-item-corrected", rating=2, was_correct=True, reviewed_at=now - timedelta(minutes=10)),
            ReviewLog(id="corrected-good", card_id="card-0", study_session_id=session.id, session_item_id="latest-item-corrected", rating=3, was_correct=True, reviewed_at=now),
            ReviewLog(id="still-hard", card_id="card-1", study_session_id=session.id, session_item_id="latest-item-hard", rating=2, was_correct=True, reviewed_at=now - timedelta(minutes=1)),
        ])
        db.add_all([
            TypedStudyAnswer(
                id="typed-corrected",
                study_session_id=session.id,
                session_item_id="latest-item-corrected",
                card_id="card-0",
                typed_answer="繁體中文-0",
                expected_answer="繁體中文-0",
                answered_at=now,
                adjudication_status="succeeded",
                verdict="correct",
                rating="Good",
                confidence=0.99,
                idempotency_key="typed-corrected-key",
            ),
            TypedStudyAnswer(
                id="typed-hard",
                study_session_id=session.id,
                session_item_id="latest-item-hard",
                card_id="card-1",
                typed_answer="不完整",
                expected_answer="繁體中文-1",
                answered_at=now - timedelta(minutes=1),
                adjudication_status="succeeded",
                verdict="partial",
                rating="Hard",
                confidence=0.7,
                idempotency_key="typed-hard-key",
            ),
        ])
        await db.commit()

    res = await client.get("/api/v1/mistakes?days=7")
    assert res.status_code == 200
    words = [item["english"] for item in res.json()["items"]]
    assert "word-0" not in words
    assert "word-1" in words

    res_podcast = await client.post("/api/v1/exports", json={
        "filter_type": "today",
        "format": "notebooklm"
    })
    assert res_podcast.status_code == 200
    podcast = res_podcast.json()["content"]
    assert "word-0" not in podcast
    assert "word-1" in podcast


async def test_mistakes_pick_one_latest_row_when_timestamps_tie(client: AsyncClient):
    async for db in get_test_db():
        await setup_mistakes_export_data(db)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(ReviewState(
            card_id="card-0",
            state=1,
            stability=1.0,
            difficulty=1.0,
            elapsed_days=0,
            scheduled_days=0,
            reps=2,
            lapses=1,
            due=now,
        ))
        db.add(StudySession(id="tie-session", requested_size=1, mode="fixed", sync_status="completed", cards_answered=1))
        db.add(SessionItem(
            id="tie-item",
            study_session_id="tie-session",
            position=0,
            target_card_id="card-0",
            correct_option_card_id="card-0",
            option_card_ids_json="[]",
        ))
        db.add(SessionItem(
            id="tie-item-2",
            study_session_id="tie-session",
            position=1,
            target_card_id="card-0",
            correct_option_card_id="card-0",
            option_card_ids_json="[]",
        ))
        db.add_all([
            ReviewLog(id="tie-again-a", card_id="card-0", study_session_id="tie-session", session_item_id="tie-item", rating=1, was_correct=False, reviewed_at=now, selected_option_card_id="card-1"),
            ReviewLog(id="tie-hard-z", card_id="card-0", study_session_id="tie-session", session_item_id="tie-item", rating=2, was_correct=True, reviewed_at=now),
        ])
        db.add_all([
            TypedStudyAnswer(
                id="typed-tie-a",
                study_session_id="tie-session",
                session_item_id="tie-item",
                card_id="card-0",
                typed_answer="繁體中文-0",
                expected_answer="繁體中文-0",
                answered_at=now,
                adjudication_status="succeeded",
                verdict="correct",
                rating="Good",
                confidence=0.99,
                idempotency_key="typed-tie-a-key",
            ),
            TypedStudyAnswer(
                id="typed-tie-z",
                study_session_id="tie-session",
                session_item_id="tie-item-2",
                card_id="card-0",
                typed_answer="不完整",
                expected_answer="繁體中文-0",
                answered_at=now,
                adjudication_status="succeeded",
                verdict="partial",
                rating="Hard",
                confidence=0.7,
                idempotency_key="typed-tie-z-key",
            ),
        ])
        await db.commit()

    res = await client.get("/api/v1/mistakes?days=7")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["english"] == "word-0"
    assert item["llm_rating"] == "Hard"
    assert item["typed_answer"] == "不完整"

# ─── 2. Confusion Pairs tests ──────────────────────────────
async def test_confusion_pairs_aggregation_and_exclusion(client: AsyncClient):
    async for db in get_test_db():
        cards = await setup_mistakes_export_data(db)
        
        # Seed confusion counts manually first
        db.add(ConfusionCount(target_card_id="card-0", selected_wrong_card_id="card-1", occurrence_count=5, last_occurred_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1)))
        db.add(ConfusionCount(target_card_id="card-0", selected_wrong_card_id="card-2", occurrence_count=8, last_occurred_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        
        # Seed a confusion involving "unknown" (which should be excluded/not queried)
        db.add(ConfusionCount(target_card_id="card-0", selected_wrong_card_id="unknown", occurrence_count=3, last_occurred_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        await db.commit()

    # GET /api/v1/confusions (default by count desc)
    res = await client.get("/api/v1/confusions")
    assert res.status_code == 200
    data = res.json()
    assert data["total"] == 2  # Excludes "unknown"
    assert len(data["items"]) == 2
    
    # First item should be card-0 -> card-2 (count = 8)
    assert data["items"][0]["target_card"]["english"] == "word-0"
    assert data["items"][0]["confused_card"]["english"] == "word-2"
    assert data["items"][0]["occurrence_count"] == 8

    # Order by activity
    res_act = await client.get("/api/v1/confusions?order_by=activity")
    assert res_act.status_code == 200
    # First item is card-0 -> card-2 because last_occurred_at is more recent
    assert res_act.json()["items"][0]["occurrence_count"] == 8

# ─── 3. Export tests ───────────────────────────────────────
async def test_podcast_export_filters_historical_again_count_within_selected_period(client: AsyncClient):
    async for db in get_test_db():
        await setup_mistakes_export_data(db)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add_all([
            ReviewLog(id="historical-old-again", card_id="card-0", rating=1, was_correct=False, reviewed_at=now - timedelta(days=8)),
            ReviewLog(id="historical-again-a", card_id="card-0", rating=1, was_correct=False, reviewed_at=now - timedelta(days=2)),
            ReviewLog(id="historical-again-b", card_id="card-0", rating=1, was_correct=False, reviewed_at=now - timedelta(days=1)),
            ReviewLog(id="historical-latest-good", card_id="card-0", rating=3, was_correct=True, reviewed_at=now),
            ReviewLog(id="single-again", card_id="card-1", rating=1, was_correct=False, reviewed_at=now - timedelta(days=1)),
        ])
        await db.commit()

    filtered = await client.post("/api/v1/exports", json={
        "filter_type": "recent_7_days",
        "minimum_again_count": 2,
        "format": "notebooklm",
    })
    assert filtered.status_code == 200
    assert "word-0" in filtered.json()["content"]
    assert "word-1" not in filtered.json()["content"]

    stricter = await client.post("/api/v1/exports", json={
        "filter_type": "recent_7_days",
        "minimum_again_count": 3,
        "format": "notebooklm",
    })
    assert stricter.status_code == 200
    assert "word-0" not in stricter.json()["content"]

    legacy = await client.post("/api/v1/exports", json={
        "filter_type": "recent_7_days",
        "format": "notebooklm",
    })
    assert legacy.status_code == 200
    assert "word-0" not in legacy.json()["content"]
    assert "word-1" in legacy.json()["content"]

    for invalid_threshold in (0, 1001):
        invalid = await client.post("/api/v1/exports", json={
            "filter_type": "recent_7_days",
            "minimum_again_count": invalid_threshold,
            "format": "notebooklm",
        })
        assert invalid.status_code == 422


async def test_exports_formats_and_quoting(client: AsyncClient):
    async for db in get_test_db():
        await setup_mistakes_export_data(db)
        db.add(ReviewState(
            card_id="card-0", state=1, stability=1.0, difficulty=1.0, elapsed_days=0, scheduled_days=0, reps=1, lapses=1,
            due=datetime.now(timezone.utc).replace(tzinfo=None)
        ))
        db.add(StudySession(id="session-export", requested_size=1, mode="fixed", sync_status="complete", cards_answered=1))
        db.add(SessionItem(
            id="item-export",
            study_session_id="session-export",
            position=0,
            target_card_id="card-0",
            correct_option_card_id="card-0",
            option_card_ids_json="[]",
            source_type="fsrs_due",
        ))
        db.add(ReviewLog(id="log-export-1", card_id="card-0", rating=1, was_correct=False, reviewed_at=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2), selected_option_card_id="card-1"))
        db.add(ReviewLog(id="log-export-today", card_id="card-0", rating=2, was_correct=True, reviewed_at=datetime.now(timezone.utc).replace(tzinfo=None), selected_option_card_id=None))
        db.add(TypedStudyAnswer(
            id="typed-export-today",
            study_session_id="session-export",
            session_item_id="item-export",
            card_id="card-0",
            typed_answer="錯的中文",
            expected_answer="繁體中文-0",
            answered_at=datetime.now(timezone.utc).replace(tzinfo=None),
            adjudication_status="succeeded",
            verdict="partial",
            rating="Hard",
            reason="The learner answer is related but incomplete.",
            confidence=0.6,
            idempotency_key="typed-export-key",
        ))
        db.add(ConfusionCount(target_card_id="card-0", selected_wrong_card_id="card-1", occurrence_count=2, last_occurred_at=datetime.now(timezone.utc).replace(tzinfo=None)))
        await db.commit()

    # Export mistakes as Markdown
    res_md = await client.post("/api/v1/exports", json={
        "filter_type": "deck",
        "format": "markdown"
    })
    assert res_md.status_code == 200
    md_content = res_md.json()["content"]
    assert "# Vocabulary Mistakes Study Export" in md_content
    assert "Term: word-0" in md_content
    assert "Traditional Chinese Meaning" in md_content
    assert "繁體中文-0" in md_content
    assert "Again Count" in md_content
    assert "Confused Word" in md_content
    assert "word-1" in md_content

    # Export as CSV (quoting and multiline check)
    res_csv = await client.post("/api/v1/exports", json={
        "filter_type": "deck",
        "format": "csv"
    })
    assert res_csv.status_code == 200
    csv_content = res_csv.json()["content"]
    
    # Parse CSV to verify multiline support and quoting
    reader = csv.reader(io.StringIO(csv_content))
    rows = list(reader)
    assert len(rows) == 2 # Header + 1 row
    assert rows[0] == ["English", "Traditional Chinese Meaning", "Again Count", "Hard Count", "Confused Word", "Selected Wrong Meaning", "Example Sentence", "Example Translation"]
    assert rows[1][0] == "word-0"
    assert rows[1][4] == "word-1"
    # Multiline check: example sentence must retain newlines
    assert "\nIt has multiple lines." in rows[1][6]

    res_podcast = await client.post("/api/v1/exports", json={
        "filter_type": "today",
        "format": "notebooklm"
    })
    assert res_podcast.status_code == 200
    podcast_content = res_podcast.json()["content"]
    assert not podcast_content.startswith("# Today's Vocabulary Mistakes")
    assert "Use this source with the following NotebookLM prompt" not in podcast_content
    assert "Create an English vocabulary review podcast" in podcast_content
    assert "Target vocabulary" in podcast_content
    assert "- My answer: 錯的中文" in podcast_content
    assert "- Correct meaning: 繁體中文-0" in podcast_content

    # Verify export does not change FSRS state
    async for db in get_test_db():
        state = (await db.execute(select(ReviewState).where(ReviewState.card_id == "card-0"))).scalars().first()
        assert state.lapses == 1
        assert state.reps == 1

async def test_reset_progress_clears_fsrs_and_typed_progress(client: AsyncClient):
    async for db in get_test_db():
        await setup_mistakes_export_data(db)
        now = datetime.now(timezone.utc).replace(tzinfo=None)
        db.add(StudySession(id="reset-session", requested_size=1, mode="fixed", sync_status="complete", cards_answered=1))
        db.add(SessionItem(
            id="reset-item",
            study_session_id="reset-session",
            position=0,
            target_card_id="card-0",
            correct_option_card_id="card-0",
            option_card_ids_json="[]",
            source_type="fsrs_due",
        ))
        db.add(TypedStudyAnswer(
            id="reset-typed",
            study_session_id="reset-session",
            session_item_id="reset-item",
            card_id="card-0",
            typed_answer="錯",
            expected_answer="繁體中文-0",
            answered_at=now,
            adjudication_status="succeeded",
            idempotency_key="reset-typed-key",
        ))
        db.add(ReviewState(
            card_id="card-0",
            state=2,
            stability=2.0,
            difficulty=2.0,
            elapsed_days=1,
            scheduled_days=2,
            reps=1,
            lapses=0,
            due=now,
        ))
        db.add(ReviewLog(id="reset-log", card_id="card-0", rating=2, was_correct=True, reviewed_at=now))
        db.add(StudyPlan(id="default", started_at=now, target_days=30, target_end_at=now + timedelta(days=30)))
        await db.commit()

    res_missing_confirm = await client.post("/api/v1/maintenance/reset-progress", json={"confirm": "NOPE"})
    assert res_missing_confirm.status_code == 400

    res = await client.post("/api/v1/maintenance/reset-progress", json={"confirm": "RESET"})
    assert res.status_code == 200

    async for db in get_test_db():
        for model in [TypedStudyAnswer, ReviewLog, ReviewState, SessionItem, StudySession, StudyPlan]:
            count = (await db.execute(select(func.count()).select_from(model))).scalar()
            assert count == 0
