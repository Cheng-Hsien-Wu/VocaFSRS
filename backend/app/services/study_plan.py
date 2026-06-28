from datetime import datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import DEFAULT_STUDY_TARGET_DAYS
from app.models import StudyPlan


async def ensure_study_plan(db: AsyncSession, now_utc: datetime) -> StudyPlan:
    q = await db.execute(select(StudyPlan).where(StudyPlan.id == "default"))
    plan = q.scalars().first()
    if plan:
        return plan

    started_at = now_utc.replace(tzinfo=None)
    plan = StudyPlan(
        id="default",
        started_at=started_at,
        target_days=DEFAULT_STUDY_TARGET_DAYS,
        target_end_at=started_at + timedelta(days=DEFAULT_STUDY_TARGET_DAYS),
    )
    db.add(plan)
    await db.flush()
    return plan
