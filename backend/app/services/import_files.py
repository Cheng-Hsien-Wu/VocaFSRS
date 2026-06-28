import csv
import io
import logging
import os
import re
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.constants import DEFAULT_DECK_NAME, ImportJobStatus
from app.models import ImportJob


logger = logging.getLogger(__name__)

UPLOAD_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "uploads")
os.makedirs(UPLOAD_DIR, exist_ok=True)

MAX_FILE_SIZE = 10 * 1024 * 1024
MAX_ROWS = 10000
MAX_DECKS = 50
MAX_ENG_LEN = 100
MAX_CHI_LEN = 1000
MAX_POS_LEN = 50
MAX_HINT_LEN = 200
MAX_EX_LEN = 1000
SUPPORTED_EXTENSIONS = (".csv", ".txt")

ALIASES = {
    "english": ["english", "word", "term", "front"],
    "chinese_meaning": ["chinese", "meaning", "definition", "back"],
    "part_of_speech": ["part_of_speech", "pos"],
    "sense_hint": ["sense_hint", "hint"],
    "example_sentence": ["example", "example_sentence"],
    "example_translation": ["example_translation", "translation"],
    "deck": ["deck", "set", "category"],
}


def suggest_mapping(headers: list[str]) -> dict[str, str]:
    mapping = {}
    for db_field, aliases in ALIASES.items():
        for header in headers:
            normalized_header = header.lower().strip()
            if normalized_header in aliases or normalized_header.replace(" ", "_") in aliases:
                mapping[db_field] = header
                break
    return mapping


def selected_import_deck_name(deck_selection: str | None) -> str:
    deck_name = (deck_selection or "").strip()
    return deck_name or DEFAULT_DECK_NAME


def upload_filepath(job_id: str) -> str:
    return os.path.join(UPLOAD_DIR, f"{job_id}.csv")


def cleanup_uploaded_file(job_id: str) -> None:
    filepath = upload_filepath(job_id)
    if not os.path.exists(filepath):
        return
    try:
        os.remove(filepath)
    except OSError:
        logger.warning("Failed to remove temporary import file for job %s", job_id, exc_info=True)


def decode_upload(contents: bytes) -> tuple[str, str]:
    is_bom = contents.startswith(b"\xef\xbb\xbf")
    encoding = "utf-8-sig" if is_bom else "utf-8"
    try:
        return contents.decode(encoding), encoding
    except UnicodeDecodeError:
        raise HTTPException(
            status_code=400,
            detail="Unsupported file encoding. File must be encoded in UTF-8 or UTF-8 BOM.",
        )


def parse_csv_text(decoded: str) -> tuple[list[str], list[list[str]], bytes]:
    try:
        reader = csv.reader(decoded.splitlines())
        headers = next(reader, [])
        rows = list(reader)
    except csv.Error:
        raise HTTPException(status_code=400, detail="Malformed CSV content")
    return headers, rows, decoded.encode("utf-8")


def parse_txt_vocabulary(decoded: str) -> tuple[list[str], list[list[str]], bytes]:
    rows: list[list[str]] = []
    for line_number, raw_line in enumerate(decoded.splitlines(), start=1):
        line = raw_line.strip()
        if not line:
            continue

        english, chinese = split_txt_vocabulary_line(line, line_number)
        rows.append([english.strip(), chinese.strip()])

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["english", "chinese"])
    writer.writerows(rows)
    return ["english", "chinese"], rows, output.getvalue().encode("utf-8")


def split_txt_vocabulary_line(line: str, line_number: int) -> tuple[str, str]:
    if "\t" in line:
        return line.split("\t", 1)

    parts = re.split(r"\s{2,}", line, maxsplit=1)
    if len(parts) == 2:
        return parts[0], parts[1]

    first_cjk = re.search(r"[\u3400-\u9fff]", line)
    if first_cjk:
        english = line[: first_cjk.start()].strip()
        chinese = line[first_cjk.start() :].strip()
        if english and chinese:
            return english, chinese

    raise HTTPException(
        status_code=400,
        detail=(
            f"TXT row {line_number} must contain an English term and Chinese meaning "
            "separated by a tab, two spaces, or a space before the Chinese meaning."
        ),
    )


def parse_upload(filename: str, contents: bytes) -> tuple[list[str], list[list[str]], bytes, str]:
    decoded, encoding = decode_upload(contents)
    lower_name = filename.lower()
    if lower_name.endswith(".csv"):
        headers, rows, stored_contents = parse_csv_text(decoded)
    elif lower_name.endswith(".txt"):
        headers, rows, stored_contents = parse_txt_vocabulary(decoded)
    else:
        raise HTTPException(status_code=400, detail="Only CSV or TXT files (.csv, .txt) are allowed")
    return headers, rows, stored_contents, encoding


async def cleanup_expired_import_jobs(db: AsyncSession) -> None:
    now = datetime.now(timezone.utc)
    result = await db.execute(
        select(ImportJob).where(
            ImportJob.expires_at < now,
            ImportJob.status.in_((ImportJobStatus.PENDING, ImportJobStatus.COMMITTING)),
        )
    )
    expired_jobs = result.scalars().all()
    for job in expired_jobs:
        job.status = ImportJobStatus.FAILED
        cleanup_uploaded_file(job.id)
    if expired_jobs:
        await db.commit()


async def load_import_job(db: AsyncSession, job_id: str) -> ImportJob:
    job = await db.get(ImportJob, job_id)
    if not job:
        raise HTTPException(status_code=404, detail="Import job not found")

    expires_at = job.expires_at.replace(tzinfo=timezone.utc)
    if expires_at >= datetime.now(timezone.utc):
        return job

    if job.status in (ImportJobStatus.PENDING, ImportJobStatus.COMMITTING):
        job.status = ImportJobStatus.FAILED
        await db.commit()
    cleanup_uploaded_file(job_id)
    raise HTTPException(status_code=410, detail="IMPORT_PREVIEW_EXPIRED")
