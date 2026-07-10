from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.schemas import NotificationSettingsResponse, NotificationSettingsUpdate
from app.services.notification_settings import get_notification_settings, update_notification_settings


router = APIRouter(prefix="/api/v1/notification-settings", tags=["notification_settings"])


@router.get("", response_model=NotificationSettingsResponse)
async def read_notification_settings(db: AsyncSession = Depends(get_db)):
    return await get_notification_settings(db)


@router.put("", response_model=NotificationSettingsResponse)
async def save_notification_settings(
    payload: NotificationSettingsUpdate,
    db: AsyncSession = Depends(get_db),
):
    return await update_notification_settings(db, payload)
