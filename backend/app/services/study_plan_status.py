import math
from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import (
    DEFAULT_STUDY_TARGET_DAYS,
    PlacementGateStatus,
    StudyAvailabilityState,
)
from app.models import StudyPlan
from app.services.deck_scope import DeckScopeError, resolve_deck_ids
from app.services.placement_gate import get_placement_gate
from app.services.study_availability import get_study_availability


async def study_plan_payload(db: AsyncSession, now_utc: datetime) -> dict[str, Any]:
    plan_q = await db.execute(select(StudyPlan).where(StudyPlan.id == "default"))
    plan = plan_q.scalars().first()
    try:
        deck_ids = await resolve_deck_ids(db, None)
    except DeckScopeError as exc:
        return _base_payload(
            plan=plan,
            now_utc=now_utc,
            availability_state=StudyAvailabilityState.DECK_SCOPE_REQUIRED,
            deck_scope_error=exc.message,
        )

    placement_gate = await get_placement_gate(db, deck_ids)
    if not placement_gate.is_complete:
        availability_state = (
            StudyAvailabilityState.NO_CARDS
            if placement_gate.status == PlacementGateStatus.NO_CARDS
            else StudyAvailabilityState.PLACEMENT_REQUIRED
        )
        return _base_payload(
            plan=plan,
            now_utc=now_utc,
            availability_state=availability_state,
            placement_status=placement_gate.to_response(),
        )

    availability = await get_study_availability(
        db=db,
        now_utc=now_utc,
        deck_ids=deck_ids,
        has_study_plan=plan is not None,
        selection_limit=0,
    )
    remaining_new = availability.pending_new_count
    remaining_days = _remaining_days(plan, now_utc)
    suggested_new = math.ceil(remaining_new / remaining_days) if remaining_new > 0 else 0

    return {
        "started": plan is not None,
        "started_at": plan.started_at.isoformat() if plan else None,
        "target_days": plan.target_days if plan else DEFAULT_STUDY_TARGET_DAYS,
        "target_end_at": plan.target_end_at.isoformat() if plan else None,
        "remaining_days": remaining_days,
        "remaining_new_cards": remaining_new,
        "suggested_new_cards_today": suggested_new,
        "due_count": availability.due_count,
        "next_due": availability.next_review_due_at.isoformat() if availability.next_review_due_at else None,
        "pending_new_count": availability.pending_new_count,
        "available_now_count": availability.available_now_count,
        "next_review_due_at": availability.next_review_due_at.isoformat() if availability.next_review_due_at else None,
        "pending_adjudication_count": availability.pending_adjudication_count,
        "active_session_blocked_count": availability.active_session_blocked_count,
        "availability_state": availability.availability_state,
        "placement_status": placement_gate.to_response(),
    }


def _remaining_days(plan: StudyPlan | None, now_utc: datetime) -> int:
    if not plan:
        return DEFAULT_STUDY_TARGET_DAYS
    return max(1, math.ceil((plan.target_end_at - now_utc).total_seconds() / 86400))


def _base_payload(
    plan: StudyPlan | None,
    now_utc: datetime,
    availability_state: StudyAvailabilityState,
    placement_status: dict | None = None,
    deck_scope_error: str | None = None,
) -> dict[str, Any]:
    remaining_days = _remaining_days(plan, now_utc)
    return {
        "started": plan is not None,
        "started_at": plan.started_at.isoformat() if plan else None,
        "target_days": plan.target_days if plan else DEFAULT_STUDY_TARGET_DAYS,
        "target_end_at": plan.target_end_at.isoformat() if plan else None,
        "remaining_days": remaining_days,
        "remaining_new_cards": 0,
        "suggested_new_cards_today": 0,
        "due_count": 0,
        "next_due": None,
        "pending_new_count": 0,
        "available_now_count": 0,
        "next_review_due_at": None,
        "pending_adjudication_count": 0,
        "active_session_blocked_count": 0,
        "availability_state": availability_state,
        "deck_scope_error": deck_scope_error,
        "placement_status": placement_status,
    }
