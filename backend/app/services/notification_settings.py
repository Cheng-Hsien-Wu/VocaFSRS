from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import DEFAULT_MINIMUM_DUE_COUNT
from app.models import ReviewReminderState
from app.schemas import NotificationSettingsResponse, NotificationSettingsUpdate
from app.services.notifications import REMINDER_STATE_ID, notifications_configured

async def get_notification_settings(db: AsyncSession) -> NotificationSettingsResponse:
    state = await db.get(ReviewReminderState, REMINDER_STATE_ID)
    return NotificationSettingsResponse(
        minimum_due_count=(state.minimum_due_count if state else DEFAULT_MINIMUM_DUE_COUNT),
        discord_configured=notifications_configured(),
    )


async def update_notification_settings(
    db: AsyncSession,
    payload: NotificationSettingsUpdate,
) -> NotificationSettingsResponse:
    state = await db.get(ReviewReminderState, REMINDER_STATE_ID)
    if state is None:
        state = ReviewReminderState(id=REMINDER_STATE_ID)
        db.add(state)
    elif state.minimum_due_count != payload.minimum_due_count:
        state.notification_armed = True
    state.minimum_due_count = payload.minimum_due_count
    await db.commit()
    return await get_notification_settings(db)
