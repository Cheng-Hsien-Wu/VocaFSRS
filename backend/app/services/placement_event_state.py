from app.constants import (
    PlacementAnswer,
    PlacementEventType,
    PlacementProjectionResult,
)
from app.models import PlacementEvent


def effective_placement_result(
    events: list[PlacementEvent],
) -> PlacementProjectionResult:
    undone_event_ids = {
        event.target_event_id
        for event in events
        if event.event_type == PlacementEventType.UNDO and event.target_event_id
    }
    last_answer = next(
        (
            event
            for event in reversed(events)
            if event.event_type == PlacementEventType.ANSWER
            and event.id not in undone_event_ids
            and event.idempotency_key not in undone_event_ids
        ),
        None,
    )
    if not last_answer or last_answer.result == PlacementAnswer.PROBLEMATIC:
        return PlacementProjectionResult.SKIPPED
    if last_answer.result == PlacementAnswer.UNKNOWN:
        return PlacementProjectionResult.UNKNOWN
    if last_answer.result == PlacementAnswer.FUZZY:
        return PlacementProjectionResult.FUZZY
    if last_answer.result != PlacementAnswer.KNOWN:
        return PlacementProjectionResult.SKIPPED
    if any(
        event.event_type == PlacementEventType.AUDIT_RECLASSIFY
        for event in events
    ):
        return PlacementProjectionResult.FUZZY
    return PlacementProjectionResult.KNOWN
