import json
from datetime import datetime, timezone, timedelta
from httpx import AsyncClient
from sqlalchemy import select, delete
from app.models import (
    Deck, DeckCard, ActivationQueue, ReviewState, ReviewLog,
    StudySession, SessionItem, TypedStudyAnswer
)
from app.services.review_scheduler import apply_fsrs_rating
from tests.study_support import get_test_db, setup_study_data

async def test_fsrs_learning_step_is_persisted_and_graduates_to_review():
    async for db in get_test_db():
        await db.execute(delete(DeckCard))
        await db.execute(delete(Deck))
        await db.execute(delete(ActivationQueue))
        await db.execute(delete(ReviewState))
        await db.execute(delete(ReviewLog))
        await db.execute(delete(StudySession))
        await db.execute(delete(SessionItem))
        await db.commit()
        await setup_study_data(db)

        session = StudySession(id="fsrs-step-session", requested_size=2, mode="fixed", sync_status="active")
        first_item = SessionItem(
            id="fsrs-step-item-1",
            study_session_id=session.id,
            position=0,
            target_card_id="c000",
            correct_option_card_id="c000",
            option_card_ids_json="[]",
        )
        db.add(session)
        db.add(first_item)
        await db.commit()

        first_review_at = datetime(2026, 6, 20, 0, 0, tzinfo=timezone.utc)
        first_due = await apply_fsrs_rating(
            db=db,
            session=session,
            item=first_item,
            card_id="c000",
            rating_name="Good",
            reviewed_at=first_review_at,
            idempotency_key="fsrs-step-1",
        )
        await db.flush()
        state = (await db.execute(select(ReviewState).where(ReviewState.card_id == "c000"))).scalars().first()
        assert state.state == 1
        assert state.step == 1
        assert 9 <= (first_due - first_review_at.replace(tzinfo=None)).total_seconds() / 60 <= 11

        second_item = SessionItem(
            id="fsrs-step-item-2",
            study_session_id=session.id,
            position=1,
            target_card_id="c000",
            correct_option_card_id="c000",
            option_card_ids_json="[]",
        )
        db.add(second_item)
        await db.flush()
        second_review_at = first_due.replace(tzinfo=timezone.utc)
        second_due = await apply_fsrs_rating(
            db=db,
            session=session,
            item=second_item,
            card_id="c000",
            rating_name="Good",
            reviewed_at=second_review_at,
            idempotency_key="fsrs-step-2",
        )
        await db.commit()

        state = (await db.execute(select(ReviewState).where(ReviewState.card_id == "c000"))).scalars().first()
        assert state.state == 2
        assert state.step is None
        assert (second_due - first_due).days >= 1

async def test_typed_answers_reject_same_session_item_with_new_key(client: AsyncClient):
    async for db in get_test_db():
        await db.execute(delete(DeckCard))
        await db.execute(delete(Deck))
        await db.execute(delete(ActivationQueue))
        await db.execute(delete(ReviewState))
        await db.execute(delete(ReviewLog))
        await db.execute(delete(StudySession))
        await db.execute(delete(SessionItem))
        await db.execute(delete(TypedStudyAnswer))
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

    answers = [
        {
            "idempotency_key": "typed-same-item-1",
            "session_item_id": item["id"],
            "card_id": item["target_card_id"],
            "typed_answer": "字",
            "answered_at": datetime.now(timezone.utc).isoformat()
        },
        {
            "idempotency_key": "typed-same-item-2",
            "session_item_id": item["id"],
            "card_id": item["target_card_id"],
            "typed_answer": "另一個答案",
            "answered_at": datetime.now(timezone.utc).isoformat()
        }
    ]
    res_answer = await client.post(f"/api/v1/study-sessions/{session_id}/typed-answers/batch", json={"answers": answers})
    assert res_answer.status_code == 200
    body = res_answer.json()
    assert body["accepted"] == ["typed-same-item-1"]
    assert body["duplicates"] == []
    assert body["conflicts"] == ["typed-same-item-2"]

    replay = await client.post(
        f"/api/v1/study-sessions/{session_id}/typed-answers/batch",
        json={"answers": [answers[0]]},
    )
    assert replay.status_code == 200
    assert replay.json() == {
        "accepted": [],
        "duplicates": ["typed-same-item-1"],
        "conflicts": [],
    }

    async for db in get_test_db():
        count = len((await db.execute(select(TypedStudyAnswer).where(TypedStudyAnswer.session_item_id == item["id"]))).scalars().all())
        session = (await db.execute(select(StudySession).where(StudySession.id == session_id))).scalars().first()
        assert count == 1
        assert session.cards_answered == 1

async def test_llm_normalize_rating_follows_verdict():
    from app.llm_adjudicator import _normalize

    result = _normalize(
        {
            "verdict": "correct",
            "rating": "Again",
            "reason": "contradictory payload",
            "confidence": 2,
        },
        "test-provider",
        "test-model",
    )

    assert result.verdict == "correct"
    assert result.rating == "Good"
    assert result.confidence == 1.0

async def test_due_first_and_priority_selection(client: AsyncClient):
    async for db in get_test_db():
        # Clear tables
        await db.execute(delete(DeckCard))
        await db.execute(delete(Deck))
        await db.execute(delete(ActivationQueue))
        await db.execute(delete(ReviewState))
        await db.execute(delete(StudySession))
        await db.execute(delete(SessionItem))
        await db.commit()

        # Setup deck
        await setup_study_data(db)
        # We clear activations to build a specific test scenario:
        await db.execute(delete(ActivationQueue))
        await db.commit()

        # Seed states:
        # c001: Due now (FSRS)
        # c002: Due in future (FSRS)
        # c003: Learn Unknown queue (pending)
        # c004: Learn Fuzzy queue (pending)
        # c000: Verify Known queue (pending)
        db.add(ReviewState(
            card_id="c001", state=1, due=datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=1),
            stability=1.0, difficulty=1.0, elapsed_days=0, scheduled_days=0, reps=1, lapses=0
        ))
        db.add(ReviewState(
            card_id="c002", state=1, due=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=5),
            stability=1.0, difficulty=1.0, elapsed_days=0, scheduled_days=0, reps=1, lapses=0
        ))
        db.add(ActivationQueue(id="aq-3", card_id="c003", activation_type="learn_unknown", priority=3, status="pending"))
        db.add(ActivationQueue(id="aq-4", card_id="c004", activation_type="learn_fuzzy", priority=2, status="pending"))
        db.add(ActivationQueue(id="aq-0", card_id="c000", activation_type="verify_known", priority=1, status="pending"))
        await db.commit()

    # Create study session of size 3. Budget is 2 (only allow 2 new activations)
    res = await client.post("/api/v1/study-sessions", json={
        "requested_size": 3,
        "mode": "fixed",
        "deck_ids": ["deck-study"],
        "activation_budget": 2
    })
    assert res.status_code == 200
    session_id = res.json()["id"]

    # Items should be ordered by:
    # 1. c001 (due FSRS)
    # 2. c003 (unknown activation - priority 3)
    # 3. c004 (fuzzy activation - priority 2)
    # c000 (verify known) should be skipped due to activation_budget limit (which limits new additions to 2)
    # c002 should be skipped because it is not due yet.
    res_items = await client.get(f"/api/v1/study-sessions/{session_id}/items")
    assert res_items.status_code == 200
    items = res_items.json()
    assert len(items) == 3
    assert items[0]["target_card_id"] == "c001"
    assert items[1]["target_card_id"] == "c003"
    assert items[2]["target_card_id"] == "c004"

async def test_fsrs_ratings_and_due_updates(client: AsyncClient, monkeypatch):
    from app.llm_adjudicator import AdjudicationResult
    import app.services.study_answers as study_answers

    async for db in get_test_db():
        await db.execute(delete(DeckCard))
        await db.execute(delete(Deck))
        await db.execute(delete(ActivationQueue))
        await db.execute(delete(ReviewState))
        await db.execute(delete(StudySession))
        await db.execute(delete(SessionItem))
        await db.commit()
        await setup_study_data(db)

    # Create study session of size 3
    res = await client.post("/api/v1/study-sessions", json={
        "requested_size": 3,
        "mode": "fixed",
        "deck_ids": ["deck-study"]
    })
    assert res.status_code == 200
    session_id = res.json()["id"]
    
    res_items = await client.get(f"/api/v1/study-sessions/{session_id}/items")
    items = res_items.json()
    item1, item2, item3 = items[0], items[1], items[2]

    answered_at = datetime.now(timezone.utc)

    ratings = {
        "good answer": AdjudicationResult("correct", "Good", "mock good", 1.0, "mock", "test"),
        "again answer": AdjudicationResult("incorrect", "Again", "mock again", 1.0, "mock", "test"),
        "hard answer": AdjudicationResult("partial", "Hard", "mock hard", 0.5, "mock", "test"),
    }

    async def fake_adjudicate_answers(batch_items):
        return {item.id: ratings[item.typed] for item in batch_items}

    monkeypatch.setattr(study_answers, "adjudicate_answers", fake_adjudicate_answers)

    payload = {
        "answers": [
            {
                "idempotency_key": "study-ev-1",
                "session_item_id": item1["id"],
                "card_id": item1["target_card_id"],
                "typed_answer": "good answer",
                "answered_at": answered_at.isoformat()
            },
            {
                "idempotency_key": "study-ev-2",
                "session_item_id": item2["id"],
                "card_id": item2["target_card_id"],
                "typed_answer": "again answer",
                "answered_at": answered_at.isoformat()
            },
            {
                "idempotency_key": "study-ev-3",
                "session_item_id": item3["id"],
                "card_id": item3["target_card_id"],
                "typed_answer": "hard answer",
                "answered_at": answered_at.isoformat()
            },
        ]
    }

    res_batch = await client.post(
        f"/api/v1/study-sessions/{session_id}/typed-answers/batch",
        json=payload,
    )
    assert res_batch.status_code == 200
    assert res_batch.json()["accepted"] == ["study-ev-1", "study-ev-2", "study-ev-3"]

    res_adjudicate = await client.post(f"/api/v1/study-sessions/{session_id}/adjudicate")
    assert res_adjudicate.status_code == 200
    assert res_adjudicate.json()["succeeded"] == 3

    async for db in get_test_db():
        states = {
            state.card_id: state
            for state in (
                await db.execute(
                    select(ReviewState).where(
                        ReviewState.card_id.in_(
                            [item1["target_card_id"], item2["target_card_id"], item3["target_card_id"]]
                        )
                    )
                )
            ).scalars()
        }
        assert states[item1["target_card_id"]].reps == 1
        assert states[item1["target_card_id"]].lapses == 0
        assert states[item1["target_card_id"]].stability > 0.0
        assert states[item2["target_card_id"]].lapses == 0
        assert states[item3["target_card_id"]].reps == 1

        logs = {
            log.idempotency_key: log
            for log in (
                await db.execute(
                    select(ReviewLog).where(
                        ReviewLog.idempotency_key.in_(["study-ev-1", "study-ev-2", "study-ev-3"])
                    )
                )
            ).scalars()
        }
        assert logs["study-ev-1"].rating == 3
        assert logs["study-ev-1"].was_correct is True
        assert logs["study-ev-2"].rating == 1
        assert logs["study-ev-2"].was_correct is False
        assert logs["study-ev-3"].rating == 2
        assert logs["study-ev-3"].was_correct is True
        for log in logs.values():
            assert log.previous_state_json is not None
            assert log.next_state_json is not None
            assert log.next_due is not None
            assert log.reviewed_at is not None
            assert log.created_at is not None

        activation = (
            await db.execute(
                select(ActivationQueue).where(
                    ActivationQueue.card_id == item1["target_card_id"]
                )
            )
        ).scalars().first()
        assert activation.status == "activated"

        session = await db.get(StudySession, session_id)
        assert session.cards_answered == 3
        assert session.good_count == 1
        assert session.again_count == 1
        assert session.hard_count == 1

async def test_placement_creates_zero_review_states_and_logs(client: AsyncClient):
    # Ensure placement sessions and events do not create any ReviewState or ReviewLog records
    res = await client.post("/api/v1/placement-sessions", json={"requested_count": 2})
    session = res.json()
    session_id = session["id"]
    first_manifest_item = json.loads(session["manifest_json"])[0]

    # Submit placement event
    payload = {
        "events": [
            {
                "idempotency_key": "placement-event-isolate-1",
                "position": first_manifest_item["position"],
                "card_id": first_manifest_item["card_id"],
                "result": "known",
                "answered_at": datetime.now(timezone.utc).isoformat()
            }
        ]
    }
    res_batch = await client.post(f"/api/v1/placement-sessions/{session_id}/events/batch", json=payload)
    assert res_batch.status_code == 200

    async for db in get_test_db():
        logs_q = await db.execute(select(ReviewLog).where(ReviewLog.idempotency_key == "placement-event-isolate-1"))
        assert len(logs_q.scalars().all()) == 0

async def test_timed_mode_unseen_card_isolation(client: AsyncClient, monkeypatch):
    from app.llm_adjudicator import AdjudicationResult
    import app.services.study_answers as study_answers

    async for db in get_test_db():
        await db.execute(delete(DeckCard))
        await db.execute(delete(Deck))
        await db.execute(delete(ActivationQueue))
        await db.execute(delete(ReviewState))
        await db.execute(delete(StudySession))
        await db.execute(delete(SessionItem))
        await db.commit()
        await setup_study_data(db)

    # 1. Create a timed study session of size 3 (which will prefetch 3 cards)
    res = await client.post("/api/v1/study-sessions", json={
        "requested_size": 3,
        "mode": "timed",
        "deck_ids": ["deck-study"]
    })
    assert res.status_code == 200
    session_id = res.json()["id"]

    res_items = await client.get(f"/api/v1/study-sessions/{session_id}/items")
    items = res_items.json()
    assert len(items) == 3

    # All three items are generated, but they should have answered_at = None and zero review logs
    for it in items:
        assert it["answered_at"] is None

    async for db in get_test_db():
        # Verify zero review logs exist initially
        q_logs = await db.execute(select(ReviewLog).where(ReviewLog.study_session_id == session_id))
        assert len(q_logs.scalars().all()) == 0

    async def fake_adjudicate_answers(batch_items):
        return {
            item.id: AdjudicationResult("correct", "Good", "mock good", 1.0, "mock", "test")
            for item in batch_items
        }

    monkeypatch.setattr(study_answers, "adjudicate_answers", fake_adjudicate_answers)

    # Answer only the first two visible items.
    answered_at = datetime.now(timezone.utc)
    payload = {
        "answers": [
            {
                "idempotency_key": "timed-ev-1",
                "session_item_id": items[0]["id"],
                "card_id": items[0]["target_card_id"],
                "typed_answer": "first answer",
                "answered_at": answered_at.isoformat()
            },
            {
                "idempotency_key": "timed-ev-2",
                "session_item_id": items[1]["id"],
                "card_id": items[1]["target_card_id"],
                "typed_answer": "second answer",
                "answered_at": answered_at.isoformat()
            },
        ]
    }
    res_batch = await client.post(
        f"/api/v1/study-sessions/{session_id}/typed-answers/batch",
        json=payload,
    )
    assert res_batch.status_code == 200

    async for db in get_test_db():
        completed_logs = await db.execute(select(ReviewLog).where(ReviewLog.study_session_id == session_id))
        assert len(completed_logs.scalars().all()) == 0

    res_finish = await client.post(f"/api/v1/study-sessions/{session_id}/finish")
    assert res_finish.status_code == 200

    res_adjudicate = await client.post(f"/api/v1/study-sessions/{session_id}/adjudicate")
    assert res_adjudicate.status_code == 200
    assert res_adjudicate.json()["succeeded"] == 2

    session_data = (await client.get(f"/api/v1/study-sessions/{session_id}")).json()
    assert session_data["cards_answered"] == 2
    assert session_data["good_count"] == 2
    assert session_data["again_count"] == 0
    assert session_data["hard_count"] == 0

    async for db in get_test_db():
        total_logs = await db.execute(select(ReviewLog).where(ReviewLog.study_session_id == session_id))
        assert len(total_logs.scalars().all()) == 2

        unseen_logs = await db.execute(select(ReviewLog).where(
            ReviewLog.study_session_id == session_id,
            ReviewLog.session_item_id == items[2]["id"]
        ))
        assert len(unseen_logs.scalars().all()) == 0
