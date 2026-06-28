import pytest
import json
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, delete, desc
from main import app
from app.database import get_db
from app.models import (
    Card, Deck, DeckCard, ActivationQueue, ReviewState, ReviewLog,
    StudySession, SessionItem, ConfusionCount, PlacementSession,
    PlacementItem, StudyPlan, TypedStudyAnswer
)
from app.services.review_scheduler import apply_fsrs_rating
from tests.study_support import get_test_db, setup_study_data

async def test_study_session_creation_clamping(client: AsyncClient):
    async for db in get_test_db():
        # Clean existing deck/activations if any
        await db.execute(delete(DeckCard))
        await db.execute(delete(Deck))
        await db.execute(delete(ActivationQueue))
        await db.commit()
        await setup_study_data(db)

    # We have cards c000-c004 (5 cards), but only c000-c003 (4 cards) are in ActivationQueue.
    # Requesting 5 cards should fail with insufficient_cards clamping error.
    res = await client.post("/api/v1/study-sessions", json={
        "requested_size": 5,
        "mode": "fixed",
        "deck_ids": ["deck-study"]
    })
    assert res.status_code == 400
    err = res.json()["detail"]
    assert err["error"] == "insufficient_cards"
    assert err["available_count"] == 4

    # Confirmation with available count works
    res_retry = await client.post("/api/v1/study-sessions", json={
        "requested_size": 4,
        "mode": "fixed",
        "deck_ids": ["deck-study"]
    })
    assert res_retry.status_code == 200
    assert res_retry.json()["requested_size"] == 4


async def test_study_session_rejects_missing_deck_scope(client: AsyncClient):
    res = await client.post("/api/v1/study-sessions", json={
        "requested_size": 1,
        "mode": "fixed",
        "deck_ids": ["missing-deck"]
    })
    assert res.status_code == 400
    assert "missing or disabled deck ids" in res.json()["detail"]

async def test_study_plan_starts_on_first_study_not_placement(client: AsyncClient):
    res_plan_initial = await client.get("/api/v1/study-sessions/plan")
    assert res_plan_initial.status_code == 200
    assert res_plan_initial.json()["started"] is False

    res_placement = await client.post("/api/v1/placement-sessions", json={"requested_count": 1})
    assert res_placement.status_code == 200
    res_plan_after_placement = await client.get("/api/v1/study-sessions/plan")
    assert res_plan_after_placement.json()["started"] is False

    async for db in get_test_db():
        await db.execute(delete(DeckCard))
        await db.execute(delete(Deck))
        await db.execute(delete(ActivationQueue))
        await db.execute(delete(PlacementItem))
        await db.execute(delete(PlacementSession))
        await db.commit()
        await setup_study_data(db)

    res_study = await client.post("/api/v1/study-sessions", json={
        "requested_size": 1,
        "mode": "fixed",
        "deck_ids": ["deck-study"]
    })
    assert res_study.status_code == 200
    res_plan_after_study = await client.get("/api/v1/study-sessions/plan")
    assert res_plan_after_study.json()["started"] is True
    assert res_plan_after_study.json()["target_days"] == 30


async def test_partial_placement_batch_does_not_unlock_study(client: AsyncClient):
    async for db in get_test_db():
        await db.execute(delete(DeckCard))
        await db.execute(delete(Deck))
        await db.execute(delete(ActivationQueue))
        await db.execute(delete(PlacementItem))
        await db.execute(delete(PlacementSession))
        await db.execute(delete(StudyPlan))
        deck = Deck(id="partial-placement-deck", name="Partial Placement Deck", enabled=True)
        db.add(deck)
        for i in range(5):
            db.add(DeckCard(deck_id=deck.id, card_id=f"c00{i}"))
        await db.commit()

    placement_res = await client.post("/api/v1/placement-sessions", json={
        "requested_count": 1,
        "deck_ids": ["partial-placement-deck"],
    })
    assert placement_res.status_code == 200
    placement = placement_res.json()
    card_id = json.loads(placement["manifest_json"])[0]["card_id"]

    answer_res = await client.post(f"/api/v1/placement-sessions/{placement['id']}/events/batch", json={
        "events": [{
            "idempotency_key": "partial-placement-answer-1",
            "event_type": "answer",
            "position": 0,
            "card_id": card_id,
            "result": "known",
            "answered_at": datetime.now(timezone.utc).isoformat(),
        }]
    })
    assert answer_res.status_code == 200

    plan_res = await client.get("/api/v1/study-sessions/plan")
    assert plan_res.status_code == 200
    plan = plan_res.json()
    assert plan["started"] is False
    assert plan["availability_state"] == "placement_required"
    assert plan["placement_status"]["remaining_count"] == 4

    study_res = await client.post("/api/v1/study-sessions", json={
        "requested_size": 1,
        "mode": "fixed",
        "deck_ids": ["partial-placement-deck"],
    })
    assert study_res.status_code == 409
    assert study_res.json()["detail"]["error"] == "placement_required"

async def test_study_plan_reports_due_now_without_future_due_as_available(client: AsyncClient):
    async for db in get_test_db():
        await db.execute(delete(DeckCard))
        await db.execute(delete(Deck))
        await db.execute(delete(ActivationQueue))
        await db.execute(delete(ReviewState))
        await db.execute(delete(StudySession))
        await db.execute(delete(SessionItem))
        await db.execute(delete(StudyPlan))
        await db.commit()
        await setup_study_data(db)
        await db.execute(delete(ActivationQueue))
        db.add(StudyPlan(
            id="default",
            started_at=datetime.now(timezone.utc).replace(tzinfo=None),
            target_days=30,
            target_end_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        ))
        db.add(ReviewState(
            card_id="c000",
            state=1,
            due=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(hours=1),
            stability=1.0,
            difficulty=1.0,
            elapsed_days=0,
            scheduled_days=0,
            reps=1,
            lapses=0,
        ))
        await db.commit()

    res = await client.get("/api/v1/study-sessions/plan")
    assert res.status_code == 200
    body = res.json()
    assert body["due_count"] == 1
    assert body["next_due"] is None
    assert body["next_review_due_at"] is None
    assert body["availability_state"] == "available_due"
    assert body["available_now_count"] == 1

async def test_study_plan_reports_activation_only_as_available_new(client: AsyncClient):
    async for db in get_test_db():
        await db.execute(delete(DeckCard))
        await db.execute(delete(Deck))
        await db.execute(delete(ActivationQueue))
        await db.execute(delete(ReviewState))
        await db.execute(delete(StudySession))
        await db.execute(delete(SessionItem))
        await db.execute(delete(StudyPlan))
        await db.commit()
        await setup_study_data(db)

    res = await client.get("/api/v1/study-sessions/plan")
    assert res.status_code == 200
    body = res.json()
    assert body["availability_state"] == "available_new"
    assert body["pending_new_count"] == 4
    assert body["available_now_count"] == 4

async def test_study_plan_exposes_pending_typed_adjudication(client: AsyncClient):
    async for db in get_test_db():
        await db.execute(delete(DeckCard))
        await db.execute(delete(Deck))
        await db.execute(delete(ActivationQueue))
        await db.execute(delete(ReviewState))
        await db.execute(delete(ReviewLog))
        await db.execute(delete(StudySession))
        await db.execute(delete(SessionItem))
        await db.execute(delete(TypedStudyAnswer))
        await db.execute(delete(StudyPlan))
        await db.commit()
        await setup_study_data(db)

    res = await client.post("/api/v1/study-sessions", json={
        "requested_size": 1,
        "mode": "fixed",
        "deck_ids": ["deck-study"]
    })
    assert res.status_code == 200
    session_id = res.json()["id"]
    item = (await client.get(f"/api/v1/study-sessions/{session_id}/items")).json()[0]

    res_answer = await client.post(f"/api/v1/study-sessions/{session_id}/typed-answers/batch", json={
        "answers": [{
            "idempotency_key": "typed-pending-plan",
            "session_item_id": item["id"],
            "card_id": item["target_card_id"],
            "typed_answer": "字",
            "answered_at": datetime.now(timezone.utc).isoformat()
        }]
    })
    assert res_answer.status_code == 200

    res_plan = await client.get("/api/v1/study-sessions/plan")
    assert res_plan.status_code == 200
    body = res_plan.json()
    assert body["pending_adjudication_count"] == 1
    assert body["pending_new_count"] == 3
    assert body["availability_state"] == "pending_adjudication"

    res_finish = await client.post(f"/api/v1/study-sessions/{session_id}/finish")
    assert res_finish.status_code == 200

    res_next = await client.post("/api/v1/study-sessions", json={
        "requested_size": 1,
        "mode": "fixed",
        "deck_ids": ["deck-study"]
    })
    assert res_next.status_code == 409
    assert res_next.json()["detail"]["error"] == "pending_adjudication"

async def test_activation_queue_does_not_reintroduce_reviewed_cards(client: AsyncClient):
    async for db in get_test_db():
        await db.execute(delete(DeckCard))
        await db.execute(delete(Deck))
        await db.execute(delete(ActivationQueue))
        await db.execute(delete(ReviewState))
        await db.execute(delete(StudySession))
        await db.execute(delete(SessionItem))
        await db.execute(delete(StudyPlan))
        await db.commit()
        await setup_study_data(db)
        await db.execute(delete(ActivationQueue))
        db.add(StudyPlan(
            id="default",
            started_at=datetime.now(timezone.utc).replace(tzinfo=None),
            target_days=30,
            target_end_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        ))
        db.add(ReviewState(
            card_id="c000",
            state=2,
            step=None,
            due=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=2),
            stability=2.0,
            difficulty=2.0,
            elapsed_days=0,
            scheduled_days=2,
            reps=2,
            lapses=0,
            last_review=datetime.now(timezone.utc).replace(tzinfo=None),
        ))
        db.add(ActivationQueue(id="aq-reviewed", card_id="c000", activation_type="learn_unknown", priority=9, status="pending"))
        await db.commit()

    res = await client.get("/api/v1/study-sessions/plan")
    assert res.status_code == 200
    body = res.json()
    assert body["availability_state"] == "waiting"
    assert body["pending_new_count"] == 0
    assert body["next_review_due_at"] is not None

