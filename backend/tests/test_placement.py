import pytest
import json
from datetime import datetime, timezone
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from main import app
from app.database import get_db
from app.models import Deck, DeckCard, PlacementSession, PlacementEvent, PlacementItem, ActivationQueue, ReviewState, ReviewLog, Card

pytestmark = pytest.mark.asyncio

async def test_create_placement_session(client: AsyncClient):
    response = await client.post("/api/v1/placement-sessions", json={"requested_count": 3})
    assert response.status_code == 200
    data = response.json()
    assert data["requested_count"] == 3
    assert data["status"] == "active"
    
    manifest = json.loads(data["manifest_json"])
    assert len(manifest) == 3
    assert manifest[0]["position"] == 0


async def test_abandoned_placement_session_discards_handled_cards(client: AsyncClient):
    first_res = await client.post("/api/v1/placement-sessions", json={"requested_count": 2})
    first_data = first_res.json()
    session_id = first_data["id"]
    first_card_id = json.loads(first_data["manifest_json"])[0]["card_id"]

    answer_res = await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json={
        "events": [{
            "idempotency_key": "handled-card-answer",
            "position": 0,
            "card_id": first_card_id,
            "result": "unknown",
            "answered_at": "2024-01-01T00:00:00Z"
        }]
    })
    assert answer_res.status_code == 200

    abandon_res = await client.post(f"/api/v1/placement-sessions/{session_id}/abandon")
    assert abandon_res.status_code == 200

    late_res = await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json={
        "events": [{
            "idempotency_key": "late-after-abandon",
            "position": 1,
            "card_id": json.loads(first_data["manifest_json"])[1]["card_id"],
            "result": "unknown",
            "answered_at": "2024-01-01T00:01:00Z"
        }]
    })
    assert late_res.status_code == 409

    async for db in app.dependency_overrides[get_db]():
        item_q = await db.execute(select(PlacementItem).where(PlacementItem.card_id == first_card_id))
        item = item_q.scalars().first()
        assert item is not None
        assert item.undone is True

        aq_q = await db.execute(select(ActivationQueue).where(ActivationQueue.card_id == first_card_id))
        assert aq_q.scalars().first() is None

        db.add(ActivationQueue(
            id="legacy-stale-abandoned-activation",
            card_id=first_card_id,
            activation_type="learn_unknown",
            priority=3,
            status="pending",
        ))
        await db.commit()

    plan_res = await client.get("/api/v1/study-sessions/plan")
    assert plan_res.status_code == 200
    plan = plan_res.json()
    assert plan["availability_state"] == "placement_required"
    assert plan["placement_status"]["remaining_count"] == 5

async def test_audit_cannot_be_created_before_server_checkpoint(client: AsyncClient):
    first_res = await client.post("/api/v1/placement-sessions", json={"requested_count": 3})
    session_id = first_res.json()["id"]

    audit_res = await client.get(f"/api/v1/placement-sessions/{session_id}/audit/100")
    assert audit_res.status_code == 409
    assert audit_res.json()["detail"] == "Checkpoint is not ready for audit"

async def test_placement_batch_cannot_cross_unresolved_checkpoint(client: AsyncClient):
    async for db in app.dependency_overrides[get_db]():
        for i in range(5, 105):
            db.add(Card(
                id=f"boundary-extra-{i:03d}",
                english=f"boundary_word_{i}",
                english_normalized=f"boundary_word_{i}",
                chinese_meaning=f"邊界字{i}",
                chinese_normalized=f"邊界字{i}",
                active=True,
                study_eligible=True,
            ))
        await db.commit()

    first_res = await client.post("/api/v1/placement-sessions", json={"requested_count": 105})
    session = first_res.json()
    session_id = session["id"]
    manifest = json.loads(session["manifest_json"])

    crossing_res = await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json={
        "events": [
            {
                "idempotency_key": f"crossing-{index}",
                "position": item["position"],
                "card_id": item["card_id"],
                "result": "known",
                "answered_at": f"2024-01-01T00:{index % 60:02d}:00Z"
            }
            for index, item in enumerate(manifest[:101])
        ]
    })
    assert crossing_res.status_code == 409
    assert crossing_res.json()["detail"] == "Placement batch crosses checkpoint boundary"

    checkpoint_res = await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json={
        "events": [
            {
                "idempotency_key": f"checkpoint-boundary-{index}",
                "position": item["position"],
                "card_id": item["card_id"],
                "result": "known",
                "answered_at": f"2024-01-01T01:{index % 60:02d}:00Z"
            }
            for index, item in enumerate(manifest[:100])
        ]
    })
    assert checkpoint_res.status_code == 200

    after_checkpoint_res = await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json={
        "events": [{
            "idempotency_key": "after-unresolved-checkpoint",
            "position": manifest[100]["position"],
            "card_id": manifest[100]["card_id"],
            "result": "known",
            "answered_at": "2024-01-01T02:00:00Z"
        }]
    })
    assert after_checkpoint_res.status_code == 409
    assert after_checkpoint_res.json()["detail"] == "Checkpoint audit required before continuing placement"

async def test_get_active_session(client: AsyncClient):
    # Should be 404 initially
    res = await client.get("/api/v1/placement-sessions/active")
    assert res.status_code == 404
    
    # Create one
    res = await client.post("/api/v1/placement-sessions", json={"requested_count": 2})
    assert res.status_code == 200
    session_id = res.json()["id"]
    
    # Get active
    res = await client.get("/api/v1/placement-sessions/active")
    assert res.status_code == 200
    assert res.json()["id"] == session_id

async def test_batch_events_idempotent(client: AsyncClient):
    res = await client.post("/api/v1/placement-sessions", json={"requested_count": 2})
    session = res.json()
    session_id = session["id"]
    first_manifest_item = json.loads(session["manifest_json"])[0]
    
    # Submit an event
    payload = {
        "events": [
            {
                "idempotency_key": "event-1",
                "position": first_manifest_item["position"],
                "card_id": first_manifest_item["card_id"],
                "result": "known",
                "answered_at": "2024-01-01T00:00:00Z"
            }
        ]
    }
    
    res = await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert "event-1" in data["accepted"]
    assert len(data["duplicates"]) == 0
    
    # Submit again
    res = await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json=payload)
    assert res.status_code == 200
    data = res.json()
    assert len(data["accepted"]) == 0
    assert "event-1" in data["duplicates"]

async def test_invalid_session_id(client: AsyncClient):
    res = await client.post("/api/v1/placement-sessions/invalid-id/events/batch", json={"events": []})
    assert res.status_code == 404

async def test_multideck_session_creation(client: AsyncClient):
    # Retrieve DB session from overrides to inject decks
    async for db in app.dependency_overrides[get_db]():
        deck1 = Deck(id="deck-1", name="Deck 1", enabled=True)
        deck2 = Deck(id="deck-2", name="Deck 2", enabled=True)
        db.add_all([deck1, deck2])
        
        # Link card c000 to deck-1, c001 to deck-2
        db.add(DeckCard(deck_id="deck-1", card_id="c000"))
        db.add(DeckCard(deck_id="deck-2", card_id="c001"))
        await db.commit()

    # Request a session filtering by these decks with too high count
    res = await client.post("/api/v1/placement-sessions", json={
        "requested_count": 10,  # exceeds available matching cards (2)
        "deck_ids": ["deck-1", "deck-2"]
    })
    assert res.status_code == 400
    err_data = res.json()
    assert err_data["detail"]["error"] == "insufficient_cards"
    assert err_data["detail"]["available_count"] == 2

    # Request again with correct available count
    res_retry = await client.post("/api/v1/placement-sessions", json={
        "requested_count": 2,
        "deck_ids": ["deck-1", "deck-2"]
    })
    assert res_retry.status_code == 200
    data = res_retry.json()
    assert data["requested_count"] == 2
    manifest = json.loads(data["manifest_json"])
    assert len(manifest) == 2
    card_ids = {m["card_id"] for m in manifest}
    assert card_ids == {"c000", "c001"}

    # Abandon the active session so the next request can create a fresh session.
    res_abandon = await client.post(f"/api/v1/placement-sessions/{data['id']}/abandon")
    assert res_abandon.status_code == 200

    # Request with zero available cards
    res_zero = await client.post("/api/v1/placement-sessions", json={
        "requested_count": 5,
        "deck_ids": ["non-existent-deck"]
    })
    assert res_zero.status_code == 400
    assert "missing or disabled deck ids" in res_zero.json()["detail"]

async def test_compensating_undo_chronological(client: AsyncClient):
    res = await client.post("/api/v1/placement-sessions", json={"requested_count": 3})
    session = res.json()
    session_id = session["id"]
    first_manifest_item = json.loads(session["manifest_json"])[0]
    
    # 1. Answer card at pos 0
    res = await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json={
        "events": [{
            "idempotency_key": "ans-0",
            "position": first_manifest_item["position"],
            "card_id": first_manifest_item["card_id"],
            "result": "known",
            "answered_at": "2024-01-01T00:00:00Z"
        }]
    })
    assert res.status_code == 200
    
    # Verify pos 0 is projection-active, session position advanced to 1
    res = await client.get(f"/api/v1/placement-sessions/{session_id}")
    assert res.json()["current_position"] == 1
    
    # 2. Undo pos 0 (referencing the answer's idempotency key)
    res = await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json={
        "events": [{
            "idempotency_key": "undo-0",
            "event_type": "undo",
            "position": first_manifest_item["position"],
            "card_id": first_manifest_item["card_id"],
            "target_event_id": "ans-0",
            "answered_at": "2024-01-01T00:01:00Z"
        }]
    })
    assert res.status_code == 200
    
    # Verify current_position rollbacked to 0, item marked undone
    res = await client.get(f"/api/v1/placement-sessions/{session_id}")
    assert res.json()["current_position"] == 0

async def test_known_card_audit_and_reclassify(client: AsyncClient):
    async for db in app.dependency_overrides[get_db]():
        for i in range(5, 100):
            db.add(Card(
                id=f"audit-extra-{i:03d}",
                english=f"audit_word_{i}",
                english_normalized=f"audit_word_{i}",
                chinese_meaning=f"抽查字{i}",
                chinese_normalized=f"抽查字{i}",
                active=True,
                study_eligible=True,
            ))
        await db.commit()

    res = await client.post("/api/v1/placement-sessions", json={"requested_count": 100})
    session_data = res.json()
    session_id = session_data["id"]
    manifest = json.loads(session_data["manifest_json"])
    
    # Answer the first 100-card checkpoint as known.
    res = await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json={
        "events": [
            {
                "idempotency_key": f"ans-a-{index}",
                "position": item["position"],
                "card_id": item["card_id"],
                "result": "known",
                "answered_at": f"2024-01-01T00:{index % 60:02d}:00Z"
            }
            for index, item in enumerate(manifest[:100])
        ]
    })
    assert res.status_code == 200
    
    # 1. Fetch Audit Questions for Checkpoint 100 (which spans pos 0-99)
    res = await client.get(f"/api/v1/placement-sessions/{session_id}/audit/100")
    assert res.status_code == 200
    audit_data = res.json()
    assert audit_data["status"] == "active"
    assert len(audit_data["questions"]) > 0
    question = audit_data["questions"][0]
    card_id = question["card_id"]
    
    # 2. Answer audit question incorrectly ("unknown") to trigger reclassification
    audit_item_id = question["audit_item_id"]
    wrong_checkpoint_res = await client.post(f"/api/v1/placement-sessions/{session_id}/audit/200/answer/{audit_item_id}", json={
        "selected_option_id": "unknown",
        "idempotency_key": "audit-wrong-checkpoint",
        "answered_at": "2024-01-01T00:01:00Z"
    })
    assert wrong_checkpoint_res.status_code == 404

    res = await client.post(f"/api/v1/placement-sessions/{session_id}/audit/100/answer/{audit_item_id}", json={
        "selected_option_id": "unknown",
        "idempotency_key": "audit-ans-key",
        "answered_at": "2024-01-01T00:02:00Z"
    })
    assert res.status_code == 200
    
    # 3. Check ActivationQueue matches learn_fuzzy (Priority 2) due to audit failure
    # 3. Check ActivationQueue matches learn_fuzzy (Priority 2) due to audit failure
    async for db in app.dependency_overrides[get_db]():
        aq = await db.execute(select(ActivationQueue).where(ActivationQueue.card_id == card_id))
        aq_item = aq.scalars().first()
        assert aq_item is not None
        assert aq_item.activation_type == "learn_fuzzy"
        assert aq_item.priority == 2


async def test_completed_checkpoint_audit_reopens_placement(client: AsyncClient):
    async for db in app.dependency_overrides[get_db]():
        for i in range(5, 101):
            db.add(Card(
                id=f"resume-extra-{i:03d}",
                english=f"resume_word_{i}",
                english_normalized=f"resume_word_{i}",
                chinese_meaning=f"繼續字{i}",
                chinese_normalized=f"繼續字{i}",
                active=True,
                study_eligible=True,
            ))
        await db.commit()

    session_response = await client.post(
        "/api/v1/placement-sessions",
        json={"requested_count": 101},
    )
    session = session_response.json()
    manifest = json.loads(session["manifest_json"])
    await client.post(
        f"/api/v1/placement-sessions/{session['id']}/events/batch",
        json={
            "events": [
                {
                    "idempotency_key": f"resume-answer-{index}",
                    "position": item["position"],
                    "card_id": item["card_id"],
                    "result": "known",
                    "answered_at": f"2024-01-01T00:{index % 60:02d}:00Z",
                }
                for index, item in enumerate(manifest[:100])
            ],
        },
    )
    audit = (
        await client.get(f"/api/v1/placement-sessions/{session['id']}/audit/100")
    ).json()

    for index, question in enumerate(audit["questions"]):
        response = await client.post(
            f"/api/v1/placement-sessions/{session['id']}/audit/100/answer/{question['audit_item_id']}",
            json={
                "selected_option_id": question["card_id"],
                "idempotency_key": f"resume-audit-{index}",
                "answered_at": f"2024-01-01T01:{index:02d}:00Z",
            },
        )
        assert response.status_code == 200

    completed_audit = (
        await client.get(f"/api/v1/placement-sessions/{session['id']}/audit/100")
    ).json()
    refreshed_session = (
        await client.get(f"/api/v1/placement-sessions/{session['id']}")
    ).json()
    assert completed_audit["status"] == "completed"
    assert refreshed_session["status"] == "active"
    assert refreshed_session["current_position"] == 100


async def test_fsrs_isolation(client: AsyncClient):
    res = await client.post("/api/v1/placement-sessions", json={"requested_count": 3})
    session_data = res.json()
    session_id = session_data["id"]
    manifest = json.loads(session_data["manifest_json"])
    card_id = manifest[0]["card_id"]
    
    # 1. Answer card_id as known
    await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json={
        "events": [{
            "idempotency_key": "iso-ans-1",
            "position": 0,
            "card_id": card_id,
            "result": "known",
            "answered_at": "2024-01-01T00:00:00Z"
        }]
    })

    # 2. Undo it
    await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json={
        "events": [{
            "idempotency_key": "iso-undo-1",
            "event_type": "undo",
            "position": 0,
            "card_id": card_id,
            "target_event_id": "iso-ans-1",
            "answered_at": "2024-01-01T00:01:00Z"
        }]
    })

    # 3. Answer it again as known
    await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json={
        "events": [{
            "idempotency_key": "iso-ans-2",
            "position": 0,
            "card_id": card_id,
            "result": "known",
            "answered_at": "2024-01-01T00:02:00Z"
        }]
    })

    # 4. Trigger checkpoint audit and answer it
    audit_res = await client.get(f"/api/v1/placement-sessions/{session_id}/audit/100")
    if audit_res.status_code == 200:
        audit_data = audit_res.json()
        if audit_data.get("questions"):
            q = audit_data["questions"][0]
            await client.post(f"/api/v1/placement-sessions/{session_id}/audit/100/answer/{q['audit_item_id']}", json={
                "selected_option_id": "unknown",
                "idempotency_key": "iso-audit-ans",
                "answered_at": "2024-01-01T00:03:00Z"
            })
            
    # Assert zero review states and logs
    async for db in app.dependency_overrides[get_db]():
        states = await db.execute(select(ReviewState))
        assert len(states.scalars().all()) == 0
        
        logs = await db.execute(select(ReviewLog))
        assert len(logs.scalars().all()) == 0

async def test_activation_queue_operations(client: AsyncClient):
    res = await client.post("/api/v1/placement-sessions", json={"requested_count": 5})
    session_data = res.json()
    session_id = session_data["id"]
    manifest = json.loads(session_data["manifest_json"])
    c0 = manifest[0]["card_id"]
    
    # 1. Answer c0 as unknown (priority 3, learn_unknown)
    res_ans = await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json={
        "events": [{
            "idempotency_key": "aq-ans-c0",
            "position": 0,
            "card_id": c0,
            "result": "unknown",
            "answered_at": "2024-01-01T00:00:00Z"
        }]
    })
    assert res_ans.status_code == 200
    
    # Verify c0 activation queue is learn_unknown priority 3
    async for db in app.dependency_overrides[get_db]():
        aq = await db.execute(select(ActivationQueue).where(ActivationQueue.card_id == c0))
        aq_item = aq.scalars().first()
        assert aq_item is not None
        assert aq_item.activation_type == "learn_unknown"
        assert aq_item.priority == 3
        assert aq_item.status == "pending"

    # 2. Answer c0 as problematic (skipped) - verify status changes to skipped, no active activation entry
    res_prob = await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json={
        "events": [{
            "idempotency_key": "aq-prob-c0",
            "position": 0,
            "card_id": c0,
            "result": "problematic",
            "problematic_reason": "typo",
            "answered_at": "2024-01-01T00:01:00Z"
        }]
    })
    assert res_prob.status_code == 200
    
    async for db in app.dependency_overrides[get_db]():
        aq = await db.execute(select(ActivationQueue).where(ActivationQueue.card_id == c0))
        aq_item = aq.scalars().first()
        assert aq_item is not None
        assert aq_item.status == "skipped"  # excluded
        item_q = await db.execute(
            select(PlacementItem).where(
                PlacementItem.placement_session_id == session_id,
                PlacementItem.position == 0,
            )
        )
        item = item_q.scalars().first()
        assert item is not None
        assert item.problematic_reason == "typo"

    # 3. Undo problematic answer - verify priority/activation type are restored to learn_unknown priority 3
    res_undo = await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json={
        "events": [{
            "idempotency_key": "aq-undo-c0",
            "event_type": "undo",
            "position": 0,
            "card_id": c0,
            "target_event_id": "aq-prob-c0",
            "answered_at": "2024-01-01T00:02:00Z"
        }]
    })
    assert res_undo.status_code == 200
    
    async for db in app.dependency_overrides[get_db]():
        aq = await db.execute(select(ActivationQueue).where(ActivationQueue.card_id == c0))
        aq_item = aq.scalars().first()
        assert aq_item is not None
        assert aq_item.activation_type == "learn_unknown"
        assert aq_item.priority == 3
        assert aq_item.status == "pending"

    # 4. Idempotency: retry batch submit and verify no duplicate records are created
    res_retry = await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json={
        "events": [{
            "idempotency_key": "aq-ans-c0",
            "position": 0,
            "card_id": c0,
            "result": "unknown",
            "answered_at": "2024-01-01T00:00:00Z"
        }]
    })
    assert res_retry.status_code == 200
    assert "aq-ans-c0" in res_retry.json()["duplicates"]

    async for db in app.dependency_overrides[get_db]():
        aq_list = (await db.execute(select(ActivationQueue).where(ActivationQueue.card_id == c0))).scalars().all()
        assert len(aq_list) == 1
