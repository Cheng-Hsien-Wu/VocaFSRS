from dataclasses import replace
from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import (
    Card,
    Deck,
    DeckCard,
    ReviewReminderState,
    ReviewState,
    SessionItem,
    StudyPlan,
    StudySession,
    TypedStudyAnswer,
)
from app.services import notifications

pytestmark = pytest.mark.asyncio


async def setup_notification_deck(db: AsyncSession, due_at: datetime) -> None:
    from app.database import Base
    from tests.conftest import engine

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    await db.execute(delete(ReviewReminderState))
    await db.execute(delete(TypedStudyAnswer))
    await db.execute(delete(SessionItem))
    await db.execute(delete(StudySession))
    await db.execute(delete(ReviewState))
    await db.execute(delete(DeckCard))
    await db.execute(delete(Deck))
    await db.execute(delete(StudyPlan))
    await db.commit()

    db.add(Deck(id="deck-notify", name="Notify Deck", deck_type="imported", enabled=True))
    card = await db.get(Card, "c000")
    if card:
        card.active = True
        card.study_eligible = True
        card.data_quality_status = "clean"
    else:
        db.add(Card(
            id="c000",
            english="word0",
            english_normalized="word0",
            chinese_meaning="字0",
            chinese_normalized="字0",
            active=True,
            study_eligible=True,
            data_quality_status="clean",
        ))
    db.add(DeckCard(deck_id="deck-notify", card_id="c000"))
    db.add(StudyPlan(
        id="default",
        started_at=due_at - timedelta(days=1),
        target_days=30,
        target_end_at=due_at + timedelta(days=29),
    ))
    db.add(ReviewState(
        card_id="c000",
        state=2,
        due=due_at,
        stability=1.0,
        difficulty=1.0,
        elapsed_days=0,
        scheduled_days=1,
        reps=1,
        lapses=0,
    ))
    await db.commit()


async def test_next_review_notification_target_uses_existing_fsrs_due(client):
    from tests.conftest import TestingSessionLocal

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    future_due = now + timedelta(hours=6)
    async with TestingSessionLocal() as db:
        await setup_notification_deck(db, future_due)

        target = await notifications.next_review_notification_target(db, now)

    assert target == future_due


async def test_notification_target_excludes_pending_adjudication_card(client):
    from tests.conftest import TestingSessionLocal

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with TestingSessionLocal() as db:
        await setup_notification_deck(db, now - timedelta(minutes=5))
        db.add(StudySession(id="notify-pending-session", requested_size=1, mode="fixed", sync_status="completed"))
        db.add(SessionItem(
            id="notify-pending-item",
            study_session_id="notify-pending-session",
            position=0,
            target_card_id="c000",
            correct_option_card_id="c000",
            option_card_ids_json=[],
            answered_at=now,
            sync_status="pending_adjudication",
            idempotency_key="notify-pending-item-key",
        ))
        db.add(TypedStudyAnswer(
            id="notify-pending-answer",
            study_session_id="notify-pending-session",
            session_item_id="notify-pending-item",
            card_id="c000",
            typed_answer="字0",
            expected_answer="字0",
            answered_at=now,
            adjudication_status="pending",
            idempotency_key="notify-pending-answer-key",
        ))
        await db.commit()

        target = await notifications.next_review_notification_target(db, now)

    assert target is None


async def test_notification_target_excludes_future_pending_adjudication_card(client):
    from tests.conftest import TestingSessionLocal

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    async with TestingSessionLocal() as db:
        await setup_notification_deck(db, now + timedelta(hours=2))
        db.add(StudySession(id="notify-future-pending-session", requested_size=1, mode="fixed", sync_status="completed"))
        db.add(SessionItem(
            id="notify-future-pending-item",
            study_session_id="notify-future-pending-session",
            position=0,
            target_card_id="c000",
            correct_option_card_id="c000",
            option_card_ids_json=[],
            answered_at=now,
            sync_status="pending_adjudication",
            idempotency_key="notify-future-pending-item-key",
        ))
        db.add(TypedStudyAnswer(
            id="notify-future-pending-answer",
            study_session_id="notify-future-pending-session",
            session_item_id="notify-future-pending-item",
            card_id="c000",
            typed_answer="字0",
            expected_answer="字0",
            answered_at=now,
            adjudication_status="pending",
            idempotency_key="notify-future-pending-answer-key",
        ))
        await db.commit()

        target = await notifications.next_review_notification_target(db, now)

    assert target is None


async def test_process_due_notifications_skips_when_discord_is_not_configured(client, monkeypatch):
    from tests.conftest import TestingSessionLocal

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    monkeypatch.setattr(notifications, "notifications_configured", lambda: False)

    async with TestingSessionLocal() as db:
        await setup_notification_deck(db, now - timedelta(minutes=5))

        sent_count = await notifications.process_due_notifications(db, now)

    assert sent_count == 0


async def test_due_notification_sends_once_per_due_target(client, monkeypatch):
    from tests.conftest import TestingSessionLocal

    now = datetime.now(timezone.utc).replace(tzinfo=None)
    sent: list[datetime] = []

    async def fake_send(target_at: datetime) -> None:
        sent.append(target_at)

    monkeypatch.setattr(notifications, "notifications_configured", lambda: True)
    monkeypatch.setattr(notifications, "_send_discord_review_reminder", fake_send)

    async with TestingSessionLocal() as db:
        due_at = now - timedelta(minutes=5)
        await setup_notification_deck(db, due_at)

        first_count = await notifications.process_due_notifications(db, now)
        second_count = await notifications.process_due_notifications(db, now)
        state = await db.get(ReviewReminderState, notifications.REMINDER_STATE_ID)

    assert first_count == 1
    assert second_count == 0
    assert sent == [due_at]
    assert state is not None
    assert state.last_sent_target_at == due_at
    assert state.last_error is None


async def test_due_notification_failure_does_not_mark_target_sent(client, monkeypatch):
    from tests.conftest import TestingSessionLocal

    now = datetime.now(timezone.utc).replace(tzinfo=None)

    async def fake_send(target_at: datetime) -> None:
        raise RuntimeError(f"boom {target_at.isoformat()}")

    monkeypatch.setattr(notifications, "notifications_configured", lambda: True)
    monkeypatch.setattr(notifications, "_send_discord_review_reminder", fake_send)

    async with TestingSessionLocal() as db:
        await setup_notification_deck(db, now - timedelta(minutes=5))

        sent_count = await notifications.process_due_notifications(db, now)
        state = await db.get(ReviewReminderState, notifications.REMINDER_STATE_ID)

    assert sent_count == 0
    assert state is not None
    assert state.last_sent_target_at is None
    assert state.last_error and "boom" in state.last_error


async def test_discord_rate_limit_retries_once_with_bounded_delay(monkeypatch):
    attempts = 0
    delays: list[float] = []

    def fake_post(_url: str, _payload: dict) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise notifications.DiscordRateLimited(120)

    async def fake_sleep(delay: float) -> None:
        delays.append(delay)

    monkeypatch.setattr(
        notifications,
        "settings",
        replace(
            notifications.settings,
            discord_webhook_url="https://example.test/webhook",
        ),
    )
    monkeypatch.setattr(notifications, "_post_discord_webhook", fake_post)
    monkeypatch.setattr(notifications.asyncio, "sleep", fake_sleep)

    await notifications._send_discord_review_reminder(datetime.now(timezone.utc))

    assert attempts == 2
    assert delays == [notifications.MAX_DISCORD_RETRY_SECONDS]
