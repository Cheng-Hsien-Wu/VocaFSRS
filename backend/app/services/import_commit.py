import json
import logging
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.constants import CardQualityStatus, DataQualityStatus, ImportJobStatus
from app.models import Card, DataQualityIssue, Deck, DeckCard, ImportJob, ImportRowResult
from app.services.import_contracts import ImportAction
from app.services.import_files import cleanup_uploaded_file, load_import_job
from app.utils import get_card_fingerprint, normalize_text


logger = logging.getLogger(__name__)


async def commit_import_job(
    db: AsyncSession,
    job_id: str,
    idempotency_key: str,
    request_hash: str,
) -> dict:
    job = await load_import_job(db, job_id)

    if job.status == ImportJobStatus.COMMITTED:
        if job.idempotency_key == idempotency_key and job.request_hash == request_hash:
            return json.loads(job.summary_json) if job.summary_json else {}
        raise HTTPException(status_code=400, detail="Import job already committed with a different request structure")

    if job.status == ImportJobStatus.COMMITTING:
        if job.idempotency_key == idempotency_key and job.request_hash == request_hash and job.summary_json:
            return json.loads(job.summary_json)
        if job.idempotency_key != idempotency_key or job.request_hash != request_hash:
            raise HTTPException(status_code=409, detail="Import job transaction is already in progress")
        raise HTTPException(status_code=409, detail="Import job transaction is already in progress")
    else:
        claim = await db.execute(
            update(ImportJob)
            .where(ImportJob.id == job_id, ImportJob.status == ImportJobStatus.PENDING)
            .values(
                status=ImportJobStatus.COMMITTING,
                idempotency_key=idempotency_key,
                request_hash=request_hash,
            )
        )
        if claim.rowcount != 1:
            await db.rollback()
            raise HTTPException(status_code=409, detail="Import job transaction is already in progress")
        await db.commit()

    session_factory = async_sessionmaker(bind=db.bind, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as import_session:
            summary = await _commit_import_rows(import_session, job_id, job)
            await import_session.commit()

        cleanup_uploaded_file(job_id)
        return summary
    except Exception as exc:
        logger.exception("Import commit transaction failed for job %s", job_id)
        async with session_factory() as error_session:
            await error_session.execute(
                update(ImportJob)
                .where(
                    ImportJob.id == job_id,
                    ImportJob.status == ImportJobStatus.COMMITTING,
                    ImportJob.idempotency_key == idempotency_key,
                    ImportJob.request_hash == request_hash,
                )
                .values(status=ImportJobStatus.FAILED, summary_json=json.dumps({"status": "failed", "error": str(exc)}))
            )
            await error_session.commit()
        cleanup_uploaded_file(job_id)
        raise HTTPException(status_code=500, detail=f"Database transaction failure during import commit: {str(exc)}")


async def _commit_import_rows(import_session: AsyncSession, job_id: str, job: ImportJob) -> dict:
    mapping = json.loads(job.field_mapping_json) if job.field_mapping_json else {}
    deck_selection = job.deck_selection

    eng_col = mapping.get("english")
    chi_col = mapping.get("chinese_meaning")
    pos_col = mapping.get("part_of_speech")
    hint_col = mapping.get("sense_hint")
    ex_sentence_col = mapping.get("example_sentence")
    ex_translation_col = mapping.get("example_translation")
    deck_col = mapping.get("deck")

    rows_query = await import_session.execute(select(ImportRowResult).where(ImportRowResult.import_job_id == job_id))
    rows = rows_query.scalars().all()

    deck_ids_by_name: dict[str, str] = {}
    fingerprint_to_card_id = await _fingerprint_to_card_id(import_session)
    new_cards_count = 0
    linked_cards_count = 0
    skipped_duplicates_count = 0

    for row_result in rows:
        if row_result.action == ImportAction.REJECTED:
            continue

        row_data = json.loads(row_result.original_row_data)
        english = row_data.get(eng_col)
        chinese = row_data.get(chi_col)
        pos = row_data.get(pos_col) if pos_col else None
        hint = row_data.get(hint_col) if hint_col else None
        ex_sentence = row_data.get(ex_sentence_col) if ex_sentence_col else None
        ex_translation = row_data.get(ex_translation_col) if ex_translation_col else None
        row_deck = row_data.get(deck_col) if (deck_col and deck_col in row_data) else None
        deck_name = row_deck.strip() if row_deck else deck_selection
        deck_id = await _get_or_create_deck(import_session, deck_name, deck_ids_by_name)
        fingerprint = get_card_fingerprint(english, chinese, pos)
        card_id = None

        if row_result.action == ImportAction.SKIPPED:
            card_id = fingerprint_to_card_id.get(fingerprint)
            skipped_duplicates_count += 1
        elif row_result.action == ImportAction.LINKED:
            card_id = row_result.card_id or fingerprint_to_card_id.get(fingerprint)
            if card_id and await _link_card_to_deck(import_session, deck_id, card_id, job_id):
                linked_cards_count += 1
        elif row_result.action in (ImportAction.CREATED, ImportAction.FLAGGED_AMBIGUOUS):
            card_id = await _create_card_from_import_row(
                import_session=import_session,
                job_id=job_id,
                row_result=row_result,
                english=english,
                chinese=chinese,
                pos=pos,
                hint=hint,
                example_sentence=ex_sentence,
                example_translation=ex_translation,
                fingerprint=fingerprint,
                deck_id=deck_id,
            )
            fingerprint_to_card_id[fingerprint] = card_id
            new_cards_count += 1

        row_result.card_id = card_id

    summary = {
        "status": "success",
        "new_cards": new_cards_count,
        "linked_existing_cards": linked_cards_count,
        "skipped_duplicates": skipped_duplicates_count,
    }
    import_job = await import_session.get(ImportJob, job_id)
    if import_job:
        import_job.status = ImportJobStatus.COMMITTED
        import_job.committed_at = datetime.now(timezone.utc)
        import_job.summary_json = json.dumps(summary)
    return summary


async def _get_or_create_deck(import_session: AsyncSession, deck_name: str, deck_ids_by_name: dict[str, str]) -> str:
    deck_name_clean = deck_name.strip()
    if deck_name_clean in deck_ids_by_name:
        return deck_ids_by_name[deck_name_clean]

    deck_q = await import_session.execute(select(Deck).where(Deck.name == deck_name_clean))
    deck = deck_q.scalars().first()
    if not deck:
        deck = Deck(
            id=str(uuid.uuid4()),
            name=deck_name_clean,
            enabled=True,
            deck_type="imported",
        )
        import_session.add(deck)

    deck_ids_by_name[deck_name_clean] = deck.id
    return deck.id


async def _fingerprint_to_card_id(import_session: AsyncSession) -> dict[str, str]:
    cards_q = await import_session.execute(select(Card))
    result = {}
    for card in cards_q.scalars().all():
        fingerprint = card.fingerprint if card.fingerprint and card.fingerprint_version == 1 else get_card_fingerprint(
            card.english,
            card.chinese_meaning,
            card.part_of_speech,
        )
        result.setdefault(fingerprint, card.id)
    return result


async def _link_card_to_deck(import_session: AsyncSession, deck_id: str, card_id: str, job_id: str) -> bool:
    link_q = await import_session.execute(select(DeckCard).where(DeckCard.deck_id == deck_id, DeckCard.card_id == card_id))
    if link_q.scalars().first():
        return False

    import_session.add(DeckCard(deck_id=deck_id, card_id=card_id, source_import_id=job_id))
    return True


async def _create_card_from_import_row(
    import_session: AsyncSession,
    job_id: str,
    row_result: ImportRowResult,
    english: str,
    chinese: str,
    pos: str | None,
    hint: str | None,
    example_sentence: str | None,
    example_translation: str | None,
    fingerprint: str,
    deck_id: str,
) -> str:
    card_id = str(uuid.uuid4())
    is_ambiguous = row_result.action == ImportAction.FLAGGED_AMBIGUOUS
    card = Card(
        id=card_id,
        english=english,
        english_normalized=normalize_text(english).lower(),
        chinese_meaning=chinese,
        chinese_normalized=normalize_text(chinese),
        part_of_speech=pos,
        sense_hint=hint,
        example_sentence=example_sentence,
        example_translation=example_translation,
        source="imported",
        active=True,
        study_eligible=not is_ambiguous,
        data_quality_status=CardQualityStatus.AMBIGUOUS if is_ambiguous else CardQualityStatus.CLEAN,
        fingerprint=fingerprint,
        fingerprint_version=1,
    )
    import_session.add(card)
    import_session.add(DeckCard(deck_id=deck_id, card_id=card_id, source_import_id=job_id))

    if is_ambiguous:
        import_session.add(
            DataQualityIssue(
                id=str(uuid.uuid4()),
                card_id=card_id,
                source="import_check",
                issue_type="potential_ambiguity",
                note=row_result.message or "Ambiguous term sharing English and POS with another card without context.",
                status=DataQualityStatus.OPEN,
            )
        )

    return card_id
