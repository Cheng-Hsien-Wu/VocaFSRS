import csv
import json
import os
import re
import uuid
from dataclasses import dataclass

from fastapi import HTTPException
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models import Card, Deck, DeckCard, ImportJob, ImportRowResult
from app.services.import_contracts import ImportAction, ImportClassification
from app.services.import_files import (
    MAX_CHI_LEN,
    MAX_DECKS,
    MAX_ENG_LEN,
    MAX_EX_LEN,
    MAX_HINT_LEN,
    MAX_POS_LEN,
    load_import_job,
    selected_import_deck_name,
    upload_filepath,
)
from app.utils import get_card_fingerprint, is_multi_meaning, normalize_text


def clean_chinese(text: str) -> str:
    if not text:
        return ""
    return "".join(re.findall(r"[\u4e00-\u9fa5a-zA-Z0-9]", text)).lower()


def split_chinese(text: str) -> set[str]:
    if not text:
        return set()
    parts = re.split(r"[,，、;；/／\s]+", text)
    return {part.strip().lower() for part in parts if part.strip()}


def is_chinese_similar(left: str, right: str) -> bool:
    if not left or not right:
        return False
    if clean_chinese(left) == clean_chinese(right):
        return True
    left_parts = split_chinese(left)
    right_parts = split_chinese(right)
    return bool(left_parts and right_parts and left_parts.intersection(right_parts))


@dataclass
class CsvAnalysisCard:
    id: str | None
    english: str
    part_of_speech: str | None
    sense_hint: str | None
    chinese_meaning: str
    fingerprint: str


async def analyze_import_job(
    db: AsyncSession,
    job_id: str,
    field_mapping: dict[str, str],
    deck_selection: str,
) -> dict:
    unique_decks: set[str] = set()

    job = await load_import_job(db, job_id)

    filepath = upload_filepath(job_id)
    if not os.path.exists(filepath):
        raise HTTPException(status_code=404, detail="Uploaded vocabulary file not found on server")

    with open(filepath, "rb") as upload_file:
        decoded = upload_file.read().decode(job.detected_encoding)
    reader = csv.DictReader(decoded.splitlines())

    await db.execute(delete(ImportRowResult).where(ImportRowResult.import_job_id == job_id))

    fingerprint_to_card, english_pos_to_cards = await _existing_card_indexes(db)
    existing_deck_links = await _existing_deck_links(db)
    decks_by_name = await _decks_by_name(db)
    fallback_deck_name = selected_import_deck_name(deck_selection)

    total_rows = 0
    valid_rows = 0
    invalid_rows = 0
    new_cards = 0
    linked_existing_cards = 0
    skipped_duplicates = 0
    conflict_count = 0
    fallback_deck_usage_count = 0
    csv_fingerprints: set[str] = set()
    csv_english_pos: dict[tuple[str, str], list[CsvAnalysisCard]] = {}
    row_results_batch = []

    eng_col = field_mapping.get("english")
    chi_col = field_mapping.get("chinese_meaning")
    pos_col = field_mapping.get("part_of_speech")
    hint_col = field_mapping.get("sense_hint")
    ex_sentence_col = field_mapping.get("example_sentence")
    ex_translation_col = field_mapping.get("example_translation")
    deck_col = field_mapping.get("deck")

    for idx, row in enumerate(reader):
        total_rows += 1
        english = row.get(eng_col or "") if eng_col else None
        chinese = row.get(chi_col or "") if chi_col else None
        pos = row.get(pos_col or "") if pos_col else None
        hint = row.get(hint_col or "") if hint_col else None
        ex_sentence = row.get(ex_sentence_col or "") if ex_sentence_col else None
        ex_translation = row.get(ex_translation_col or "") if ex_translation_col else None

        row_target_deck_name = _target_deck_name(row, deck_col, fallback_deck_name)
        if deck_col and not (row.get(deck_col) or "").strip():
            fallback_deck_usage_count += 1

        if row_target_deck_name:
            unique_decks.add(row_target_deck_name)
            if len(unique_decks) > MAX_DECKS:
                raise HTTPException(
                    status_code=400,
                    detail=f"Unique deck count in uploaded file exceeds the limit of {MAX_DECKS}",
                )

        invalid_message = _invalid_row_message(english, chinese, pos, hint, ex_sentence, ex_translation)
        if invalid_message:
            invalid_rows += 1
            row_results_batch.append(_row_result(job_id, idx, row, ImportClassification.INVALID, ImportAction.REJECTED, invalid_message))
            continue

        fingerprint = get_card_fingerprint(english, chinese, pos)
        row_deck_id = decks_by_name.get(row_target_deck_name)
        classification = ImportClassification.SAME_TERM_VARIANT
        action = ImportAction.CREATED
        message = ""
        card_id = None

        if fingerprint in csv_fingerprints:
            skipped_duplicates += 1
            classification = ImportClassification.EXACT_DUPLICATE
            action = ImportAction.SKIPPED
            message = "Duplicate row within the same uploaded file."
        elif fingerprint in fingerprint_to_card:
            card = fingerprint_to_card[fingerprint]
            card_id = card.id
            if row_deck_id and (row_deck_id, card.id) in existing_deck_links:
                skipped_duplicates += 1
                classification = ImportClassification.EXACT_DUPLICATE
                action = ImportAction.SKIPPED
                message = "Exact card already exists in the target deck."
            else:
                linked_existing_cards += 1
                classification = ImportClassification.CROSS_DECK_DUPLICATE
                action = ImportAction.LINKED
                message = "Exact card exists in another deck. It will be linked."
        else:
            key = (normalize_text(english).lower(), normalize_text(pos or "").lower())
            all_matches = english_pos_to_cards.get(key, []) + csv_english_pos.get(key, [])
            classification, action, message, card_id, conflict_delta, skipped_delta = _classify_non_exact_row(
                chinese=chinese,
                hint=hint,
                all_matches=all_matches,
            )
            conflict_count += conflict_delta
            skipped_duplicates += skipped_delta

            if action in (ImportAction.CREATED, ImportAction.FLAGGED_AMBIGUOUS):
                if is_multi_meaning(chinese) and classification == ImportClassification.SAME_TERM_VARIANT:
                    classification = ImportClassification.MULTI_MEANING_CANDIDATE
                    message = "Chinese definition appears to contain multiple meanings punctuation."

                new_cards += 1
                csv_fingerprints.add(fingerprint)
                csv_english_pos.setdefault(key, []).append(
                    CsvAnalysisCard(
                        id=None,
                        english=english,
                        part_of_speech=pos,
                        sense_hint=hint,
                        chinese_meaning=chinese,
                        fingerprint=fingerprint,
                    )
                )

        valid_rows += 1
        row_results_batch.append(_row_result(job_id, idx, row, classification, action, message, card_id))

    db.add_all(row_results_batch)
    summary = {
        "total_rows": total_rows,
        "valid_rows": valid_rows,
        "invalid_rows": invalid_rows,
        "new_cards": new_cards,
        "linked_existing_cards": linked_existing_cards,
        "skipped_duplicates": skipped_duplicates,
        "conflict_count": conflict_count,
        "fallback_deck_usage_count": fallback_deck_usage_count,
    }

    job.field_mapping_json = json.dumps(field_mapping)
    job.deck_selection = fallback_deck_name
    job.total_rows = total_rows
    job.valid_rows = valid_rows
    job.invalid_rows = invalid_rows
    job.new_cards = new_cards
    job.linked_existing_cards = linked_existing_cards
    job.skipped_duplicates = skipped_duplicates
    job.conflict_count = conflict_count
    job.summary_json = json.dumps(summary)
    await db.commit()

    return {"id": job_id, **summary}


async def _existing_card_indexes(db: AsyncSession) -> tuple[dict[str, Card], dict[tuple[str, str], list[Card]]]:
    cards_result = await db.execute(select(Card))
    fingerprint_to_card = {}
    english_pos_to_cards = {}
    for card in cards_result.scalars().all():
        fingerprint = card.fingerprint if card.fingerprint and card.fingerprint_version == 1 else get_card_fingerprint(
            card.english,
            card.chinese_meaning,
            card.part_of_speech,
        )
        fingerprint_to_card.setdefault(fingerprint, card)
        key = (normalize_text(card.english).lower(), normalize_text(card.part_of_speech or "").lower())
        english_pos_to_cards.setdefault(key, []).append(card)
    return fingerprint_to_card, english_pos_to_cards


async def _existing_deck_links(db: AsyncSession) -> set[tuple[str, str]]:
    deck_cards_result = await db.execute(select(DeckCard))
    return {(deck_card.deck_id, deck_card.card_id) for deck_card in deck_cards_result.scalars().all()}


async def _decks_by_name(db: AsyncSession) -> dict[str, str]:
    deck_query = await db.execute(select(Deck))
    return {deck.name.strip(): deck.id for deck in deck_query.scalars().all()}


def _target_deck_name(row: dict, deck_col: str | None, fallback_deck_name: str) -> str:
    if not deck_col:
        return fallback_deck_name
    raw_deck_value = (row.get(deck_col) or "").strip()
    return raw_deck_value or fallback_deck_name


def _invalid_row_message(
    english: str | None,
    chinese: str | None,
    pos: str | None,
    hint: str | None,
    example_sentence: str | None,
    example_translation: str | None,
) -> str | None:
    if not english or not chinese or not english.strip() or not chinese.strip():
        return "Missing required field: English term or Chinese meaning is empty."
    if len(english) > MAX_ENG_LEN:
        return f"English term length ({len(english)}) exceeds limit of {MAX_ENG_LEN} chars."
    if len(chinese) > MAX_CHI_LEN:
        return f"Chinese meaning length ({len(chinese)}) exceeds limit of {MAX_CHI_LEN} chars."
    if pos and len(pos) > MAX_POS_LEN:
        return f"Part of speech length ({len(pos)}) exceeds limit of {MAX_POS_LEN} chars."
    if hint and len(hint) > MAX_HINT_LEN:
        return f"Sense hint length ({len(hint)}) exceeds limit of {MAX_HINT_LEN} chars."
    if example_sentence and len(example_sentence) > MAX_EX_LEN:
        return f"Example sentence length ({len(example_sentence)}) exceeds limit of {MAX_EX_LEN} chars."
    if example_translation and len(example_translation) > MAX_EX_LEN:
        return f"Example translation length ({len(example_translation)}) exceeds limit of {MAX_EX_LEN} chars."
    return None


def _classify_non_exact_row(
    chinese: str,
    hint: str | None,
    all_matches: list,
) -> tuple[ImportClassification, ImportAction, str, str | None, int, int]:
    if not all_matches:
        return ImportClassification.SAME_TERM_VARIANT, ImportAction.CREATED, "New English term and Part of Speech.", None, 0, 0

    matching_probable_duplicate = next(
        (match for match in all_matches if is_chinese_similar(chinese, match.chinese_meaning)),
        None,
    )
    if matching_probable_duplicate:
        return (
            ImportClassification.PROBABLE_DUPLICATE,
            ImportAction.SKIPPED,
            "Same term, part of speech, and similar Chinese meaning found.",
            getattr(matching_probable_duplicate, "id", None),
            0,
            1,
        )

    normalized_hint = normalize_text(hint or "").lower()
    has_conflict = any(normalized_hint and normalized_hint == normalize_text(match.sense_hint or "").lower() for match in all_matches)
    has_ambiguity = not has_conflict and any(
        not normalized_hint or not normalize_text(match.sense_hint or "").lower()
        for match in all_matches
    )

    if has_conflict:
        return (
            ImportClassification.POTENTIAL_CONFLICT,
            ImportAction.REJECTED,
            f"Contradictory Chinese definitions for the same sense hint ({hint or 'empty'}).",
            None,
            1,
            0,
        )
    if has_ambiguity:
        return (
            ImportClassification.POTENTIAL_AMBIGUITY,
            ImportAction.FLAGGED_AMBIGUOUS,
            "Card shares English and Part of Speech with another card but lacks a distinguishing sense hint.",
            None,
            1,
            0,
        )
    return (
        ImportClassification.SAME_TERM_VARIANT,
        ImportAction.CREATED,
        "Distinct card with identical English and Part of Speech but separated by context/hint.",
        None,
        0,
        0,
    )


def _row_result(
    job_id: str,
    row_index: int,
    row: dict,
    classification: ImportClassification,
    action: ImportAction,
    message: str,
    card_id: str | None = None,
) -> ImportRowResult:
    return ImportRowResult(
        id=str(uuid.uuid4()),
        import_job_id=job_id,
        row_index=row_index,
        original_row_data=json.dumps(row),
        classification=classification.value,
        action=action.value,
        message=message,
        card_id=card_id,
    )
