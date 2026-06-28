from dataclasses import dataclass

from app.models import Card
from app.services.study_availability import StudyCandidate


@dataclass(frozen=True)
class StudySessionItemCandidate:
    card: Card
    source_type: str


def build_study_session_items(
    due_cards: list[Card],
    new_candidates: list[StudyCandidate],
    requested_size: int,
    activation_budget: int,
) -> list[StudySessionItemCandidate]:
    selected_due = due_cards[:requested_size]

    remaining_needed = requested_size - len(selected_due)
    selected_new = new_candidates[:min(remaining_needed, activation_budget)]

    primary_candidates: list[StudySessionItemCandidate] = [
        StudySessionItemCandidate(card=card, source_type="fsrs_due")
        for card in selected_due
    ]
    primary_candidates.extend(
        StudySessionItemCandidate(card=candidate.card, source_type=candidate.source_type)
        for candidate in selected_new
    )

    return primary_candidates[:requested_size]
