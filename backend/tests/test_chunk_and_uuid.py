import pytest
import uuid
import json
from httpx import AsyncClient
from sqlalchemy import delete
from app.models import Card, PlacementSession
from conftest import TestingSessionLocal

pytestmark = pytest.mark.asyncio

async def test_get_placement_chunk_endpoint(client: AsyncClient):
    # 1. Start a placement session
    res = await client.post("/api/v1/placement-sessions", json={"requested_count": 3})
    assert res.status_code == 200
    data = res.json()
    session_id = data["id"]

    # 2. Get chunk 0
    chunk_res = await client.get(f"/api/v1/placement-sessions/{session_id}/chunks/0")
    assert chunk_res.status_code == 200
    chunk_data = chunk_res.json()
    assert len(chunk_data) == 3
    assert "english" in chunk_data[0]
    assert "chinese_meaning" in chunk_data[0]

async def test_arbitrary_uuid_card_ids_decoupling(client: AsyncClient):
    # 1. Clear existing seeded cards and insert cards with random UUIDs into the database
    uuid1 = str(uuid.uuid4())
    uuid2 = str(uuid.uuid4())
    
    async with TestingSessionLocal() as session:
        await session.execute(delete(Card))
        card1 = Card(
            id=uuid1,
            english="arbitrary-one",
            english_normalized="arbitrary-one",
            chinese_meaning="任意一",
            chinese_normalized="任意一",
            fingerprint="fp1",
            fingerprint_version=1,
            active=True
        )
        card2 = Card(
            id=uuid2,
            english="arbitrary-two",
            english_normalized="arbitrary-two",
            chinese_meaning="任意二",
            chinese_normalized="任意二",
            fingerprint="fp2",
            fingerprint_version=1,
            active=True
        )
        session.add(card1)
        session.add(card2)
        await session.commit()

    # 2. Create placement session requesting 2 cards
    res = await client.post("/api/v1/placement-sessions", json={"requested_count": 2})
    assert res.status_code == 200
    data = res.json()
    session_id = data["id"]
    manifest = json.loads(data["manifest_json"])
    
    # 3. Check that the manifest contains our arbitrary UUIDs
    card_ids = [m["card_id"] for m in manifest]
    assert uuid1 in card_ids and uuid2 in card_ids

    # 4. Fetch the chunk and verify card details are returned correctly for these UUIDs
    chunk_res = await client.get(f"/api/v1/placement-sessions/{session_id}/chunks/0")
    assert chunk_res.status_code == 200
    chunk_data = chunk_res.json()
    assert any(c["id"] == uuid1 for c in chunk_data) and any(c["id"] == uuid2 for c in chunk_data)
