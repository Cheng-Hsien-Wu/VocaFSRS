import asyncio
import json
import logging
import math
from datetime import datetime, timezone
from urllib import error, request

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import ReviewReminderState, ReviewState, StudyPlan
from app.services.deck_scope import DeckScopeError, resolve_deck_ids
from app.services.study_availability import get_study_availability

REMINDER_STATE_ID = "default"
MAX_DISCORD_RETRY_SECONDS = 30.0
logger = logging.getLogger(__name__)


class DiscordRateLimited(RuntimeError):
    def __init__(self, retry_after: float):
        if not math.isfinite(retry_after):
            retry_after = 0.0
        self.retry_after = max(0.0, min(retry_after, MAX_DISCORD_RETRY_SECONDS))
        super().__init__(f"Discord webhook rate limited; retry after {self.retry_after:g}s")


def notifications_configured() -> bool:
    return bool(settings.discord_webhook_url)


async def next_review_notification_target(db: AsyncSession, now_utc: datetime) -> datetime | None:
    plan_q = await db.execute(select(StudyPlan).where(StudyPlan.id == "default"))
    if not plan_q.scalars().first():
        return None

    try:
        deck_ids = await resolve_deck_ids(db, None)
    except DeckScopeError:
        return None

    availability = await get_study_availability(
        db=db,
        now_utc=now_utc,
        deck_ids=deck_ids,
        has_study_plan=True,
        selection_limit=1,
    )
    if availability.due_cards:
        review_state = await db.get(ReviewState, availability.due_cards[0].id)
        if review_state:
            return review_state.due
    return availability.next_review_due_at


async def process_due_notifications(db: AsyncSession, now_utc: datetime) -> int:
    if not notifications_configured():
        return 0

    target_at = await next_review_notification_target(db, now_utc)
    if not target_at or target_at > now_utc:
        return 0

    state = await _get_or_create_reminder_state(db)
    if state.last_sent_target_at == target_at:
        return 0

    try:
        await _send_discord_review_reminder(target_at)
    except Exception as exc:
        state.last_error = str(exc)[:1000]
        await db.commit()
        return 0

    state.last_sent_target_at = target_at
    state.last_sent_at = now_utc
    state.last_error = None
    await db.commit()
    return 1


async def _get_or_create_reminder_state(db: AsyncSession) -> ReviewReminderState:
    state = await db.get(ReviewReminderState, REMINDER_STATE_ID)
    if state:
        return state

    state = ReviewReminderState(id=REMINDER_STATE_ID)
    db.add(state)
    await db.flush()
    return state


async def _send_discord_review_reminder(target_at: datetime) -> None:
    if not settings.discord_webhook_url:
        return

    payload = {
        "content": _discord_message(target_at),
        "allowed_mentions": {"parse": []},
    }
    try:
        await asyncio.to_thread(_post_discord_webhook, settings.discord_webhook_url, payload)
    except DiscordRateLimited as exc:
        await asyncio.sleep(exc.retry_after)
        await asyncio.to_thread(_post_discord_webhook, settings.discord_webhook_url, payload)


def _discord_message(target_at: datetime) -> str:
    lines = [
        "**VocaFSRS**",
        "有單字到期，可以開始複習。",
        f"Due target: {target_at.isoformat()} UTC",
    ]
    if settings.app_public_url:
        lines.append(settings.app_public_url)
    return "\n".join(lines)


def _post_discord_webhook(url: str, payload: dict) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "VocaFSRS/0.1",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=10) as response:
            if response.status >= 400:
                raise RuntimeError(f"Discord webhook returned HTTP {response.status}")
    except error.HTTPError as exc:
        retry_after = _discord_retry_after(exc)
        if exc.code == 429 and retry_after is not None:
            raise DiscordRateLimited(retry_after) from exc
        detail = f"Discord webhook returned HTTP {exc.code}"
        raise RuntimeError(detail) from exc


def _discord_retry_after(exc: error.HTTPError) -> float | None:
    header = exc.headers.get("Retry-After")
    if header:
        try:
            return float(header)
        except ValueError:
            pass

    try:
        body = json.loads(exc.read().decode("utf-8"))
        return float(body["retry_after"])
    except (KeyError, TypeError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None


async def notification_worker_loop(session_factory) -> None:
    while True:
        await asyncio.sleep(max(settings.notification_poll_seconds, 10))
        try:
            async with session_factory() as db:
                now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
                await process_due_notifications(db, now_utc)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("Notification worker iteration failed")
            continue
