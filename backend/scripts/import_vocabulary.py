#!/usr/bin/env python3
import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

from fastapi import HTTPException

from app.constants import DEFAULT_DECK_NAME, ImportJobStatus
from app.database import AsyncSessionLocal
from app.models import ImportJob
from app.services.import_analysis import analyze_import_job
from app.services.import_commit import commit_import_job
from app.services.import_files import (
    MAX_FILE_SIZE,
    MAX_ROWS,
    parse_upload,
    suggest_mapping,
    upload_filepath,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Import a VocaFSRS TXT or CSV vocabulary file.")
    parser.add_argument("dataset", type=Path)
    return parser.parse_args()


async def import_dataset(dataset: Path) -> dict:
    if not dataset.is_file():
        raise ValueError(f"Dataset not found: {dataset}")

    contents = dataset.read_bytes()
    if len(contents) > MAX_FILE_SIZE:
        raise ValueError(f"Dataset exceeds the {MAX_FILE_SIZE // (1024 * 1024)} MB limit.")

    headers, rows, stored_contents, encoding = parse_upload(dataset.name, contents)
    if len(rows) > MAX_ROWS:
        raise ValueError(f"Dataset exceeds the {MAX_ROWS}-row limit.")

    mapping = suggest_mapping(headers)
    if "english" not in mapping or "chinese_meaning" not in mapping:
        raise ValueError(
            "Could not identify English and Chinese columns. "
            "Use headers named english/word and chinese/meaning, or use the TXT format."
        )

    job_id = str(uuid.uuid4())
    Path(upload_filepath(job_id)).write_bytes(stored_contents)
    job = ImportJob(
        id=job_id,
        original_filename=dataset.name,
        status=ImportJobStatus.PENDING,
        detected_encoding=encoding,
        expires_at=datetime.now(timezone.utc) + timedelta(hours=24),
        created_at=datetime.now(timezone.utc),
    )

    async with AsyncSessionLocal() as session:
        session.add(job)
        await session.commit()
        analysis = await analyze_import_job(session, job_id, mapping, DEFAULT_DECK_NAME)
        result = await commit_import_job(
            session,
            job_id,
            idempotency_key=str(uuid.uuid4()),
            request_hash=f"installer:{job_id}",
        )
    return {"analysis": analysis, "result": result}


async def main() -> int:
    try:
        result = await import_dataset(parse_args().dataset.expanduser().resolve())
    except (ValueError, HTTPException) as exc:
        detail = exc.detail if isinstance(exc, HTTPException) else str(exc)
        print(f"Import failed: {detail}", file=sys.stderr)
        return 1

    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
