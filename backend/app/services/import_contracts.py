from enum import StrEnum


class ImportClassification(StrEnum):
    EXACT_DUPLICATE = "exact_duplicate"
    CROSS_DECK_DUPLICATE = "cross_deck_duplicate"
    SAME_TERM_VARIANT = "same_term_variant"
    PROBABLE_DUPLICATE = "probable_duplicate"
    POTENTIAL_CONFLICT = "potential_conflict"
    POTENTIAL_AMBIGUITY = "potential_ambiguity"
    MULTI_MEANING_CANDIDATE = "multi_meaning_candidate"
    INVALID = "invalid"


class ImportAction(StrEnum):
    CREATED = "created"
    LINKED = "linked"
    SKIPPED = "skipped"
    REJECTED = "rejected"
    FLAGGED_AMBIGUOUS = "flagged_ambiguous"
