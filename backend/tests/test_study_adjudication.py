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

async def test_typed_answers_wait_for_llm_before_fsrs(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("LLM_TEST_MODE", "mock")
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

    res = await client.post("/api/v1/study-sessions", json={
        "requested_size": 1,
        "mode": "fixed",
        "deck_ids": ["deck-study"]
    })
    assert res.status_code == 200
    session_id = res.json()["id"]
    items = (await client.get(f"/api/v1/study-sessions/{session_id}/items")).json()
    item = items[0]

    answer_payload = {
        "answers": [{
            "idempotency_key": "typed-ev-1",
            "session_item_id": item["id"],
            "card_id": item["target_card_id"],
            "typed_answer": "字0",
            "answered_at": datetime.now(timezone.utc).isoformat()
        }]
    }
    res_answer = await client.post(f"/api/v1/study-sessions/{session_id}/typed-answers/batch", json=answer_payload)
    assert res_answer.status_code == 200
    assert "typed-ev-1" in res_answer.json()["accepted"]

    async for db in get_test_db():
        assert (await db.execute(select(ReviewState).where(ReviewState.card_id == item["target_card_id"]))).scalars().first() is None
        assert (await db.execute(select(ReviewLog).where(ReviewLog.idempotency_key == "typed-ev-1"))).scalars().first() is None

    res_finish = await client.post(f"/api/v1/study-sessions/{session_id}/finish")
    assert res_finish.status_code == 200

    res_adj = await client.post(f"/api/v1/study-sessions/{session_id}/adjudicate")
    assert res_adj.status_code == 200
    body = res_adj.json()
    assert body["succeeded"] == 1
    assert body["results"][0]["rating"] == "Good"
    assert body["results"][0]["next_due"] is not None

    async for db in get_test_db():
        state = (await db.execute(select(ReviewState).where(ReviewState.card_id == item["target_card_id"]))).scalars().first()
        assert state is not None
        log = (await db.execute(select(ReviewLog).where(ReviewLog.idempotency_key == "typed-ev-1"))).scalars().first()
        assert log is not None
        typed = (await db.execute(select(TypedStudyAnswer).where(TypedStudyAnswer.idempotency_key == "typed-ev-1"))).scalars().first()
        assert typed.adjudication_status == "succeeded"
        session = await db.get(StudySession, session_id)
        assert session.cards_answered == 1
        assert session.good_count == 1
        assert session.again_count == 0
        assert session.hard_count == 0


async def test_completed_session_rejects_new_answers(client: AsyncClient):
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

    res_finish = await client.post(f"/api/v1/study-sessions/{session_id}/finish")
    assert res_finish.status_code == 200

    res_answer = await client.post(f"/api/v1/study-sessions/{session_id}/typed-answers/batch", json={
        "answers": [{
            "idempotency_key": "typed-after-finish",
            "session_item_id": item["id"],
            "card_id": item["target_card_id"],
            "typed_answer": "字",
            "answered_at": datetime.now(timezone.utc).isoformat()
        }]
    })
    assert res_answer.status_code == 409
    assert res_answer.json()["detail"]["error"] == "session_closed"

async def test_abandoned_session_rejects_adjudication(client: AsyncClient):
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

    res_answer = await client.post(f"/api/v1/study-sessions/{session_id}/typed-answers/batch", json={
        "answers": [{
            "idempotency_key": "typed-before-abandon",
            "session_item_id": item["id"],
            "card_id": item["target_card_id"],
            "typed_answer": "字",
            "answered_at": datetime.now(timezone.utc).isoformat()
        }]
    })
    assert res_answer.status_code == 200

    res_abandon = await client.post(f"/api/v1/study-sessions/{session_id}/abandon")
    assert res_abandon.status_code == 200

    res_adj = await client.post(f"/api/v1/study-sessions/{session_id}/adjudicate")
    assert res_adj.status_code == 409
    assert res_adj.json()["detail"]["error"] == "session_abandoned"

    async for db in get_test_db():
        typed = (await db.execute(select(TypedStudyAnswer).where(TypedStudyAnswer.study_session_id == session_id))).scalars().first()
        assert typed.adjudication_status == "failed"
        assert typed.error_message == "Session abandoned before LLM grading completed."

    res_plan = await client.get("/api/v1/study-sessions/plan")
    assert res_plan.status_code == 200
    assert res_plan.json()["pending_adjudication_count"] == 0


async def test_typed_answer_timestamp_is_normalized_to_utc(client: AsyncClient):
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
    taipei_answered_at = datetime(2026, 6, 21, 10, 0, tzinfo=timezone(timedelta(hours=8)))

    res_answer = await client.post(f"/api/v1/study-sessions/{session_id}/typed-answers/batch", json={
        "answers": [{
            "idempotency_key": "typed-taipei-time",
            "session_item_id": item["id"],
            "card_id": item["target_card_id"],
            "typed_answer": "字",
            "answered_at": taipei_answered_at.isoformat()
        }]
    })
    assert res_answer.status_code == 200

    async for db in get_test_db():
        typed = (await db.execute(select(TypedStudyAnswer).where(TypedStudyAnswer.idempotency_key == "typed-taipei-time"))).scalars().first()
        assert typed.answered_at == datetime(2026, 6, 21, 2, 0)

async def test_typed_adjudication_batches_session_answers_once(client: AsyncClient, monkeypatch):
    from app.llm_adjudicator import AdjudicationResult
    import app.services.study_answers as study_answers

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

    calls = []

    async def fake_adjudicate_answers(items):
        calls.append([item.id for item in items])
        return {
            item.id: AdjudicationResult("correct", "Good", "batch mock", 1.0, "mock", "batch")
            for item in items
        }

    monkeypatch.setattr(study_answers, "adjudicate_answers", fake_adjudicate_answers)

    res = await client.post("/api/v1/study-sessions", json={
        "requested_size": 2,
        "mode": "fixed",
        "deck_ids": ["deck-study"]
    })
    assert res.status_code == 200
    session_id = res.json()["id"]
    items = (await client.get(f"/api/v1/study-sessions/{session_id}/items")).json()

    answer_payload = {
        "answers": [
            {
                "idempotency_key": f"typed-batch-{idx}",
                "session_item_id": item["id"],
                "card_id": item["target_card_id"],
                "typed_answer": "字",
                "answered_at": datetime.now(timezone.utc).isoformat()
            }
            for idx, item in enumerate(items)
        ]
    }
    res_answer = await client.post(f"/api/v1/study-sessions/{session_id}/typed-answers/batch", json=answer_payload)
    assert res_answer.status_code == 200

    res_adj = await client.post(f"/api/v1/study-sessions/{session_id}/adjudicate")
    assert res_adj.status_code == 200
    assert res_adj.json()["succeeded"] == 2
    assert len(calls) == 1
    assert len(calls[0]) == 2


async def test_processing_typed_answers_are_not_reclaimed_for_adjudication(client: AsyncClient, monkeypatch):
    from app.llm_adjudicator import AdjudicationResult
    import app.services.study_answers as study_answers
    from app.constants import AdjudicationStatus

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

    calls = []

    async def fake_adjudicate_answers(items):
        calls.append([item.id for item in items])
        return {
            item.id: AdjudicationResult("correct", "Good", "batch mock", 1.0, "mock", "batch")
            for item in items
        }

    monkeypatch.setattr(study_answers, "adjudicate_answers", fake_adjudicate_answers)

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
            "idempotency_key": "typed-processing",
            "session_item_id": item["id"],
            "card_id": item["target_card_id"],
            "typed_answer": "字",
            "answered_at": datetime.now(timezone.utc).isoformat()
        }]
    })
    assert res_answer.status_code == 200

    async for db in get_test_db():
        typed = (await db.execute(select(TypedStudyAnswer).where(TypedStudyAnswer.idempotency_key == "typed-processing"))).scalars().first()
        typed.adjudication_status = AdjudicationStatus.PROCESSING
        typed.adjudication_claim_token = "active-claim"
        typed.adjudication_claimed_at = datetime.now(timezone.utc).replace(tzinfo=None)
        await db.commit()

    res_adj = await client.post(f"/api/v1/study-sessions/{session_id}/adjudicate")
    assert res_adj.status_code == 200
    assert res_adj.json()["processing"] == 1
    assert calls == []


async def test_stale_processing_typed_answers_are_reclaimed(client: AsyncClient, monkeypatch):
    from app.constants import AdjudicationStatus
    from app.llm_adjudicator import AdjudicationResult
    import app.services.study_answers as study_answers

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

    session_response = await client.post("/api/v1/study-sessions", json={
        "requested_size": 1,
        "mode": "fixed",
        "deck_ids": ["deck-study"],
    })
    session_id = session_response.json()["id"]
    item = (await client.get(f"/api/v1/study-sessions/{session_id}/items")).json()[0]
    await client.post(f"/api/v1/study-sessions/{session_id}/typed-answers/batch", json={
        "answers": [{
            "idempotency_key": "typed-stale-processing",
            "session_item_id": item["id"],
            "card_id": item["target_card_id"],
            "typed_answer": "字",
            "answered_at": datetime.now(timezone.utc).isoformat(),
        }],
    })

    async for db in get_test_db():
        typed = (
            await db.execute(
                select(TypedStudyAnswer).where(
                    TypedStudyAnswer.idempotency_key == "typed-stale-processing",
                )
            )
        ).scalars().one()
        typed.adjudication_status = AdjudicationStatus.PROCESSING
        typed.adjudication_claim_token = "abandoned-claim"
        typed.adjudication_claimed_at = (
            datetime.now(timezone.utc) - timedelta(minutes=16)
        ).replace(tzinfo=None)
        await db.commit()

    async def fake_adjudicate_answers(items):
        return {
            answer.id: AdjudicationResult(
                "correct",
                "Good",
                "reclaimed",
                1.0,
                "mock",
                "lease-test",
            )
            for answer in items
        }

    monkeypatch.setattr(study_answers, "adjudicate_answers", fake_adjudicate_answers)
    result = await client.post(f"/api/v1/study-sessions/{session_id}/adjudicate")

    assert result.status_code == 200
    assert result.json()["succeeded"] == 1
    assert result.json()["processing"] == 0


async def test_unexpected_adjudication_failure_marks_claimed_answers_retryable(
    client: AsyncClient,
    monkeypatch,
):
    import app.services.study_answers as study_answers
    from app.constants import AdjudicationStatus
    from app.llm_adjudicator import AdjudicationResult

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

    session_response = await client.post("/api/v1/study-sessions", json={
        "requested_size": 1,
        "mode": "fixed",
        "deck_ids": ["deck-study"],
    })
    session_id = session_response.json()["id"]
    item = (await client.get(f"/api/v1/study-sessions/{session_id}/items")).json()[0]
    await client.post(f"/api/v1/study-sessions/{session_id}/typed-answers/batch", json={
        "answers": [{
            "idempotency_key": "typed-unexpected-failure",
            "session_item_id": item["id"],
            "card_id": item["target_card_id"],
            "typed_answer": "字",
            "answered_at": datetime.now(timezone.utc).isoformat(),
        }],
    })

    async def fail_unexpectedly(_items):
        raise RuntimeError("provider payload was malformed")

    monkeypatch.setattr(study_answers, "adjudicate_answers", fail_unexpectedly)

    async for db in get_test_db():
        with pytest.raises(RuntimeError, match="provider payload was malformed"):
            await study_answers.adjudicate_pending_answers(db, session_id)

    async for db in get_test_db():
        answer = (
            await db.execute(
                select(TypedStudyAnswer).where(
                    TypedStudyAnswer.idempotency_key == "typed-unexpected-failure",
                )
            )
        ).scalars().one()
        assert answer.adjudication_status == AdjudicationStatus.FAILED
        assert answer.error_message == "provider payload was malformed"

    async def succeed_on_retry(items):
        return {
            item.id: AdjudicationResult(
                "correct",
                "Good",
                "retry succeeded",
                1.0,
                "mock",
                "retry",
            )
            for item in items
        }

    monkeypatch.setattr(study_answers, "adjudicate_answers", succeed_on_retry)
    async for db in get_test_db():
        retry_result = await study_answers.retry_failed_adjudication(db, session_id)
        assert retry_result["succeeded"] == 1
        assert retry_result["failed"] == 0


async def test_partial_adjudication_persists_success_and_retries_only_failed_answer(
    client: AsyncClient,
    monkeypatch,
):
    import app.services.study_answers as study_answers
    from app.llm_adjudicator import (
        AdjudicationResult,
        PartialAdjudicationUnavailable,
    )

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

    session_response = await client.post("/api/v1/study-sessions", json={
        "requested_size": 2,
        "mode": "fixed",
        "deck_ids": ["deck-study"],
    })
    session_id = session_response.json()["id"]
    items = (await client.get(f"/api/v1/study-sessions/{session_id}/items")).json()
    await client.post(f"/api/v1/study-sessions/{session_id}/typed-answers/batch", json={
        "answers": [
            {
                "idempotency_key": f"partial-{index}",
                "session_item_id": item["id"],
                "card_id": item["target_card_id"],
                "typed_answer": "字",
                "answered_at": datetime.now(timezone.utc).isoformat(),
            }
            for index, item in enumerate(items)
        ],
    })

    first_call_ids: list[str] = []
    failed_id = ""

    async def partially_fail(batch_items):
        nonlocal failed_id
        first_call_ids.extend(item.id for item in batch_items)
        failed_id = batch_items[1].id
        success = batch_items[0]
        raise PartialAdjudicationUnavailable(
            results={
                success.id: AdjudicationResult(
                    "correct", "Good", "first batch succeeded", 1.0, "mock", "partial"
                )
            },
            errors_by_id={failed_id: "provider timeout"},
        )

    monkeypatch.setattr(study_answers, "adjudicate_answers", partially_fail)
    first_result = await client.post(f"/api/v1/study-sessions/{session_id}/adjudicate")

    assert first_result.status_code == 200
    assert first_result.json()["succeeded"] == 1
    assert first_result.json()["failed"] == 1
    assert len(first_call_ids) == 2

    retry_call_ids: list[str] = []

    async def succeed_on_retry(batch_items):
        retry_call_ids.extend(item.id for item in batch_items)
        return {
            item.id: AdjudicationResult(
                "correct", "Good", "retry succeeded", 1.0, "mock", "retry"
            )
            for item in batch_items
        }

    monkeypatch.setattr(study_answers, "adjudicate_answers", succeed_on_retry)
    retry_result = await client.post(f"/api/v1/study-sessions/{session_id}/adjudication-retry")

    assert retry_result.status_code == 200
    assert retry_result.json()["succeeded"] == 2
    assert retry_result.json()["failed"] == 0
    assert retry_call_ids == [failed_id]

    async for db in get_test_db():
        review_logs = (
            await db.execute(
                select(ReviewLog).where(ReviewLog.study_session_id == session_id)
            )
        ).scalars().all()
        assert len(review_logs) == 2
