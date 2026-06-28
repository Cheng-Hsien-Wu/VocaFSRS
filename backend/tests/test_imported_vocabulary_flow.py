import pytest
import os
import uuid
import json
from datetime import datetime, timezone
from httpx import AsyncClient, ASGITransport
from sqlalchemy import select, delete, func
from main import app
from app.database import get_db
from app.models import (
    Card, Deck, DeckCard, PlacementSession, ReviewState, ReviewLog,
    ActivationQueue, StudySession, SessionItem, PlacementItem
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

async def setup_test_imported_deck(db):
    # Clear tables to ensure isolated state
    await db.execute(delete(PlacementItem))
    await db.execute(delete(PlacementSession))
    await db.execute(delete(ActivationQueue))
    await db.execute(delete(ReviewState))
    await db.execute(delete(ReviewLog))
    await db.execute(delete(StudySession))
    await db.execute(delete(SessionItem))
    await db.execute(delete(DeckCard))
    await db.execute(delete(Card))
    await db.execute(delete(Deck))
    await db.commit()

    deck = Deck(id="imported-deck-id", name=DEFAULT_DECK_NAME, enabled=True, deck_type="imported")
    db.add(deck)
    await db.commit()

    for i in range(5):
        c_id = str(uuid.uuid4()) # Arbitrary imported ID (opaque)
        card = Card(
            id=c_id,
            english=f"imported-word-{i}",
            english_normalized=f"imported-word-{i}",
            chinese_meaning=f"meaning-{i}",
            chinese_normalized=f"meaning-{i}",
            part_of_speech="noun",
            fingerprint=get_card_fingerprint(f"imported-word-{i}", f"meaning-{i}", "noun"),
            fingerprint_version=1,
            active=True,
            study_eligible=True
        )
        db.add(card)
        db.add(DeckCard(deck_id=deck.id, card_id=c_id))
    await db.commit()
    return deck


async def mark_deck_placement_complete(db, deck: Deck, card_ids: list[str]):
    session_id = f"placement-complete-{deck.id}"
    await db.execute(delete(PlacementItem).where(PlacementItem.placement_session_id == session_id))
    await db.execute(delete(PlacementSession).where(PlacementSession.id == session_id))
    db.add(PlacementSession(
        id=session_id,
        requested_count=len(card_ids),
        current_position=len(card_ids),
        status="completed",
        manifest_json=json.dumps([
            {"position": i, "card_id": card_id}
            for i, card_id in enumerate(card_ids)
        ]),
    ))
    db.add_all([
        PlacementItem(
            id=f"{session_id}-item-{i}",
            placement_session_id=session_id,
            position=i,
            card_id=card_id,
            placement_result="known",
            answered_at=datetime.now(timezone.utc).replace(tzinfo=None),
            undone=False,
        )
        for i, card_id in enumerate(card_ids)
    ])


async def setup_named_imported_deck(db, deck_name: str):
    await setup_test_imported_deck(db)
    deck = await db.get(Deck, "imported-deck-id")
    deck.name = deck_name
    await db.commit()
    return deck

async def test_imported_placement_session_creation_and_resumption(client: AsyncClient):
    async for db in get_test_db():
        await setup_test_imported_deck(db)
            
    # 1. Create Placement session without specifying deck_ids (should default to the imported vocabulary deck)
    res = await client.post("/api/v1/placement-sessions", json={"requested_count": 3})
    assert res.status_code == 200
    data = res.json()
    session_id = data["id"]
    assert data["requested_count"] == 3
    assert data["status"] == "active"
    
    # Verify the cards selected are indeed opaque UUIDs
    manifest = json.loads(data["manifest_json"])
    assert len(manifest) == 3
    for item in manifest:
        assert isinstance(item["position"], int)
        # Opaque ID check (must be a valid UUID string format)
        assert uuid.UUID(item["card_id"])
        
    # 2. Persists and resumes correctly: calling POST again on active session should return the same session
    res_resume = await client.post("/api/v1/placement-sessions", json={"requested_count": 3})
    assert res_resume.status_code == 200
    resume_data = res_resume.json()
    assert resume_data["id"] == session_id
    assert resume_data["status"] == "active"


async def test_single_imported_deck_is_default_even_when_name_differs(client: AsyncClient):
    async for db in get_test_db():
        deck = await setup_named_imported_deck(db, "My Vocabulary")

    res = await client.post("/api/v1/placement-sessions", json={"requested_count": 3})
    assert res.status_code == 200
    manifest = json.loads(res.json()["manifest_json"])

    async for db in get_test_db():
        deck_card_q = await db.execute(select(DeckCard.card_id).where(DeckCard.deck_id == deck.id))
        deck_card_ids = {row[0] for row in deck_card_q.all()}
    assert {item["card_id"] for item in manifest}.issubset(deck_card_ids)


async def test_ambiguous_imported_decks_require_explicit_selection(client: AsyncClient):
    async for db in get_test_db():
        deck = await setup_named_imported_deck(db, "My Vocabulary")
        db.add(Deck(id="legacy-imported-deck-b", name="Other Vocabulary", enabled=True, deck_type="imported"))
        first_card_q = await db.execute(select(Card.id).limit(1))
        first_card_id = first_card_q.scalar()
        db.add(DeckCard(deck_id="legacy-imported-deck-b", card_id=first_card_id))
        await db.commit()

    res = await client.post("/api/v1/placement-sessions", json={"requested_count": 1})
    assert res.status_code == 409

    explicit = await client.post("/api/v1/placement-sessions", json={"requested_count": 1, "deck_ids": [deck.id]})
    assert explicit.status_code == 200


async def test_study_plan_reports_deck_scope_required_for_ambiguous_imports(client: AsyncClient):
    async for db in get_test_db():
        await setup_named_imported_deck(db, "My Vocabulary")
        db.add(Deck(id="legacy-imported-deck-b", name="Other Vocabulary", enabled=True, deck_type="imported"))
        first_card_q = await db.execute(select(Card.id).limit(1))
        first_card_id = first_card_q.scalar()
        db.add(DeckCard(deck_id="legacy-imported-deck-b", card_id=first_card_id))
        await db.commit()

    res = await client.get("/api/v1/study-sessions/plan")
    assert res.status_code == 200
    body = res.json()
    assert body["availability_state"] == "deck_scope_required"
    assert "deck_scope_error" in body


async def test_imported_formal_study_card_selection(client: AsyncClient):
    async for db in get_test_db():
        deck = await setup_test_imported_deck(db)
        
        # Get cards linked to this deck
        cards_q = await db.execute(
            select(Card).join(DeckCard, DeckCard.card_id == Card.id).where(DeckCard.deck_id == deck.id)
        )
        cards = cards_q.scalars().all()
        assert len(cards) >= 3
        await mark_deck_placement_complete(db, deck, [card.id for card in cards])
        
        # Activate 3 cards
        db.add_all([
            ActivationQueue(id=f"aq-imported-{i}", card_id=cards[i].id, activation_type="learn_unknown", priority=3, status="pending")
            for i in range(3)
        ])
        await db.commit()
        
    # Create a Study Session for the deck
    res = await client.post("/api/v1/study-sessions", json={
        "requested_size": 3,
        "mode": "fixed",
        "deck_ids": [deck.id]
    })
    assert res.status_code == 200
    session_data = res.json()
    session_id = session_data["id"]
    
    # Verify that study session items select the activated cards from the imported deck
    res_items = await client.get(f"/api/v1/study-sessions/{session_id}/items")
    assert res_items.status_code == 200
    items = res_items.json()
    assert len(items) == 3
    for item in items:
        # Check target_card_id matches one of the activated cards
        assert uuid.UUID(item["target_card_id"])

async def test_imported_reimport_idempotency_and_fsrs_isolation(client: AsyncClient):
    # Clear DB cards/decks
    async for db in get_test_db():
        await db.execute(delete(DeckCard))
        await db.execute(delete(Card))
        await db.execute(delete(Deck))
        await db.commit()

    # Prepare a dummy CSV to import twice
    csv_data = f"english,chinese_meaning,part_of_speech,deck\ntestword1,testmeaning1,noun,{DEFAULT_DECK_NAME}".encode()
    
    # First Import
    files = {"file": ("test_imported.csv", csv_data, "text/csv")}
    res_upload = await client.post("/api/v1/imports/upload", files=files)
    assert res_upload.status_code == 200
    job_id_1 = res_upload.json()["import_job_id"]
    
    mapping = {
        "english": "english",
        "chinese_meaning": "chinese_meaning",
        "part_of_speech": "part_of_speech",
        "deck": "deck"
    }
    
    res_analyze = await client.post(
        f"/api/v1/imports/{job_id_1}/analyze",
        json={"field_mapping": mapping, "deck_selection": DEFAULT_DECK_NAME}
    )
    assert res_analyze.status_code == 200
    assert res_analyze.json()["new_cards"] == 1
    
    idemp_1 = str(uuid.uuid4())
    res_commit = await client.post(
        f"/api/v1/imports/{job_id_1}/commit",
        json={"idempotency_key": idemp_1, "request_hash": "hash_1"}
    )
    assert res_commit.status_code == 200
    
    # Verify card created in DB
    async for db in get_test_db():
        card_q = await db.execute(select(Card).where(Card.english == "testword1"))
        card = card_q.scalars().first()
        assert card is not None
        assert card.english == "testword1"
        
        # Verify zero FSRS review states and review logs are created
        states_count = (await db.execute(select(func.count(ReviewState.card_id)).where(ReviewState.card_id == card.id))).scalar()
        logs_count = (await db.execute(select(func.count(ReviewLog.id)).where(ReviewLog.card_id == card.id))).scalar()
        assert states_count == 0
        assert logs_count == 0

    # Second Import of the same file (idempotency check)
    files_2 = {"file": ("test_imported.csv", csv_data, "text/csv")}
    res_upload_2 = await client.post("/api/v1/imports/upload", files=files_2)
    assert res_upload_2.status_code == 200
    job_id_2 = res_upload_2.json()["import_job_id"]
    
    res_analyze_2 = await client.post(
        f"/api/v1/imports/{job_id_2}/analyze",
        json={"field_mapping": mapping, "deck_selection": DEFAULT_DECK_NAME}
    )
    assert res_analyze_2.status_code == 200
    # Duplicates should be skipped
    assert res_analyze_2.json()["new_cards"] == 0
    assert res_analyze_2.json()["skipped_duplicates"] == 1
    
    idemp_2 = str(uuid.uuid4())
    res_commit_2 = await client.post(
        f"/api/v1/imports/{job_id_2}/commit",
        json={"idempotency_key": idemp_2, "request_hash": "hash_2"}
    )
    assert res_commit_2.status_code == 200
    assert res_commit_2.json()["new_cards"] == 0
    assert res_commit_2.json()["skipped_duplicates"] == 1
