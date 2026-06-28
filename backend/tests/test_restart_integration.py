import os
import uuid
import pytest
import json
from httpx import AsyncClient, ASGITransport
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.pool import NullPool
from sqlalchemy import select

from app.database import Base, get_db
from app.models import Card, Deck, DeckCard, ImportJob, ImportRowResult, PlacementSession, PlacementEvent, PlacementAudit, PlacementAuditItem
from main import app

# Create a temporary file database path
TEMP_DB_PATH = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "data", "temp_test_restart.db"))

@pytest.mark.asyncio
async def test_restart_integration():
    # Ensure temp file does not exist
    if os.path.exists(TEMP_DB_PATH):
        try:
            os.remove(TEMP_DB_PATH)
        except Exception:
            pass
        
    db_url = f"sqlite+aiosqlite:///{TEMP_DB_PATH}"
    
    # 1. Initialize DB structure
    engine = create_async_engine(db_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    
    # Define db dependency override
    async def override_get_db_temp():
        async with session_factory() as session:
            yield session
            
    app.dependency_overrides[get_db] = override_get_db_temp
    
    # Create AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Create a preview
        csv_data = b"english,chinese,pos,hint\napple,\xE8\x98\x8B\xE6\x9E\x9C,noun,red fruit\nbanana,\xE9\xA6\x99\xE8\x95\x89,noun,yellow fruit"
        files = {"file": ("restart_test.csv", csv_data, "text/csv")}
        res = await client.post("/api/v1/imports/upload", files=files)
        assert res.status_code == 200
        job_id = res.json()["import_job_id"]
        
        mapping = {
            "english": "english",
            "chinese_meaning": "chinese",
            "part_of_speech": "pos",
            "sense_hint": "hint"
        }
        res_analyze = await client.post(
            f"/api/v1/imports/{job_id}/analyze",
            json={"field_mapping": mapping, "deck_selection": "Fruit Deck"}
        )
        assert res_analyze.status_code == 200
        
    # Close / restart the database engine
    await engine.dispose()
    
    # Re-initialize engine and session factory to simulate restart
    engine = create_async_engine(db_url, poolclass=NullPool)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    app.dependency_overrides[get_db] = override_get_db_temp
    
    # Verify the preview survives
    async with session_factory() as session:
        job_q = await session.execute(select(ImportJob).where(ImportJob.id == job_id))
        job = job_q.scalars().first()
        assert job is not None
        assert job.status == "pending"
        
        rows_q = await session.execute(select(ImportRowResult).where(ImportRowResult.import_job_id == job_id))
        rows = rows_q.scalars().all()
        assert len(rows) == 2
        
    # Commit the import
    idemp_key = str(uuid.uuid4())
    req_hash = "some_hash"
    
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res_commit = await client.post(
            f"/api/v1/imports/{job_id}/commit",
            json={"idempotency_key": idemp_key, "request_hash": req_hash}
        )
        assert res_commit.status_code == 200
        
    # Close / restart the engine again
    await engine.dispose()
    
    engine = create_async_engine(db_url, poolclass=NullPool)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    app.dependency_overrides[get_db] = override_get_db_temp
    
    # Verify imported data survives
    async with session_factory() as session:
        cards_q = await session.execute(select(Card))
        cards = cards_q.scalars().all()
        assert len(cards) == 2
        
        decks_q = await session.execute(select(Deck))
        decks = decks_q.scalars().all()
        assert len(decks) == 1
        assert decks[0].name == "Fruit Deck"
        
        links_q = await session.execute(select(DeckCard))
        links = links_q.scalars().all()
        assert len(links) == 2
        
    # Retry the same commit
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        res_commit_retry = await client.post(
            f"/api/v1/imports/{job_id}/commit",
            json={"idempotency_key": idemp_key, "request_hash": req_hash}
        )
        assert res_commit_retry.status_code == 200
        
    # Verify no duplicate cards or deck relationships
    async with session_factory() as session:
        cards_q = await session.execute(select(Card))
        cards = cards_q.scalars().all()
        assert len(cards) == 2
        
        links_q = await session.execute(select(DeckCard))
        links = links_q.scalars().all()
        assert len(links) == 2
        
    # Cleanup database file
    await engine.dispose()
    if os.path.exists(TEMP_DB_PATH):
        try:
            os.remove(TEMP_DB_PATH)
        except Exception:
            pass
        
    # Restore overrides
    from tests.conftest import override_get_db
    app.dependency_overrides[get_db] = override_get_db

@pytest.mark.asyncio
async def test_placement_restart_persistence():
    # Ensure temp file does not exist
    if os.path.exists(TEMP_DB_PATH):
        try:
            os.remove(TEMP_DB_PATH)
        except Exception:
            pass
        
    db_url = f"sqlite+aiosqlite:///{TEMP_DB_PATH}"
    
    # 1. Initialize DB structure
    engine = create_async_engine(db_url, poolclass=NullPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    
    # Define db dependency override
    async def override_get_db_temp():
        async with session_factory() as session:
            yield session
            
    app.dependency_overrides[get_db] = override_get_db_temp
    
    # 2. Add some cards and a deck
    async with session_factory() as session:
        deck = Deck(id="restart-deck", name="Restart Deck", enabled=True)
        session.add(deck)
        cards = []
        for i in range(100):
            card_id = f"rc{i:03d}"
            card = Card(
                id=card_id,
                english=f"word{i}",
                english_normalized=f"word{i}",
                chinese_meaning=f"meaning{i}",
                chinese_normalized=f"meaning{i}",
                part_of_speech="noun",
                active=True,
                study_eligible=True
            )
            session.add(card)
            cards.append(card)
            # link to deck
            dc = DeckCard(deck_id="restart-deck", card_id=card_id)
            session.add(dc)
        await session.commit()

    # Create AsyncClient
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Start a placement session
        res = await client.post("/api/v1/placement-sessions", json={
            "requested_count": 100,
            "deck_ids": ["restart-deck"]
        })
        assert res.status_code == 200
        session_data = res.json()
        session_id = session_data["id"]
        manifest_original = json.loads(session_data["manifest_json"])
        
        # Submit the first checkpoint so audit generation is legal.
        res_batch = await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json={
            "events": [
                {
                    "idempotency_key": f"rc-ans-{index}",
                    "position": item["position"],
                    "card_id": item["card_id"],
                    "result": "known",
                    "answered_at": f"2024-01-01T00:{index % 60:02d}:00Z"
                }
                for index, item in enumerate(manifest_original[:100])
            ]
        })
        assert res_batch.status_code == 200

        # Trigger audit questions generation to persist audit/options in SQLite
        res_audit = await client.get(f"/api/v1/placement-sessions/{session_id}/audit/100")
        assert res_audit.status_code == 200
        audit_original = res_audit.json()
        
    # Close / restart the database engine (simulate backend restart)
    await engine.dispose()
    
    engine = create_async_engine(db_url, poolclass=NullPool)
    session_factory = async_sessionmaker(bind=engine, class_=AsyncSession, expire_on_commit=False)
    app.dependency_overrides[get_db] = override_get_db_temp
    
    # 3. Verify consistency after restart
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # Check active session details
        res_sess = await client.get(f"/api/v1/placement-sessions/active")
        assert res_sess.status_code == 200
        sess_data = res_sess.json()
        assert sess_data["id"] == session_id
        
        manifest_after = json.loads(sess_data["manifest_json"])
        # Check manifest order is identical
        assert manifest_after == manifest_original
        
        # Check audit questions and option order remain identical
        res_audit_after = await client.get(f"/api/v1/placement-sessions/{session_id}/audit/100")
        assert res_audit_after.status_code == 200
        audit_after = res_audit_after.json()
        assert audit_after["status"] == audit_original["status"]
        assert len(audit_after["questions"]) == len(audit_original["questions"])
        
        q_orig = audit_original["questions"][0]
        q_after = audit_after["questions"][0]
        assert q_orig["card_id"] == q_after["card_id"]
        assert q_orig["audit_item_id"] == q_after["audit_item_id"]
        assert q_orig["options"] == q_after["options"]
        
    # Cleanup database file
    await engine.dispose()
    if os.path.exists(TEMP_DB_PATH):
        try:
            os.remove(TEMP_DB_PATH)
        except Exception:
            pass
        
    # Restore overrides
    from tests.conftest import override_get_db
    app.dependency_overrides[get_db] = override_get_db
