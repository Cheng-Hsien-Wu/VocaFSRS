import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from pydantic import BaseModel

from app.database import get_db
from app.constants import DEFAULT_DECK_NAME, ImportJobStatus
from app.models import Deck, ImportJob, ImportRowResult
from app.services.import_files import (
    MAX_FILE_SIZE,
    MAX_ROWS,
    SUPPORTED_EXTENSIONS,
    cleanup_expired_import_jobs,
    load_import_job,
    parse_upload,
    suggest_mapping,
    upload_filepath,
)
from app.services.import_analysis import analyze_import_job
from app.services.import_commit import commit_import_job


router = APIRouter(prefix="/api/v1/imports", tags=["import"])

class AnalyzeRequest(BaseModel):
    field_mapping: Dict[str, str]
    deck_selection: str = DEFAULT_DECK_NAME

class CommitRequest(BaseModel):
    idempotency_key: str
    request_hash: str

@router.get("/decks")
async def list_decks(db: AsyncSession = Depends(get_db)):
    result = await db.execute(select(Deck).order_by(Deck.name))
    decks = result.scalars().all()
    return [{"id": d.id, "name": d.name} for d in decks]

@router.post("/upload")
async def upload_csv(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    # Clean up expired jobs first
    await cleanup_expired_import_jobs(db)

    # 1. Validate file extension
    if not file.filename or not file.filename.lower().endswith(SUPPORTED_EXTENSIONS):
        raise HTTPException(status_code=400, detail="Only CSV or TXT files (.csv, .txt) are allowed")

    # 2. Read file to check file size and validate UTF-8 encoding
    contents = await file.read(MAX_FILE_SIZE + 1)
    if len(contents) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="File size exceeds the 10MB limit")
        
    # 3. Read rows and enforce row count limit. TXT uploads are normalized into
    # the same temporary CSV format used by the existing import pipeline.
    headers, all_rows, stored_contents, encoding = parse_upload(file.filename, contents)

    if len(all_rows) > MAX_ROWS:
        raise HTTPException(status_code=400, detail=f"Uploaded file row count exceeds the limit of {MAX_ROWS} rows")

    # 4. Save file temporarily
    job_id = str(uuid.uuid4())
    filepath = upload_filepath(job_id)
    with open(filepath, "wb") as f:
        f.write(stored_contents)
        
    # 5. Create ImportJob record
    expires_at = datetime.now(timezone.utc) + timedelta(hours=24)
    job = ImportJob(
        id=job_id,
        original_filename=file.filename or "uploaded.csv",
        status=ImportJobStatus.PENDING,
        detected_encoding=encoding,
        expires_at=expires_at,
        created_at=datetime.now(timezone.utc)
    )
    db.add(job)
    await db.commit()
    
    # 6. Generate mapping suggestion and preview rows
    mapping = suggest_mapping(headers)
    preview_rows = all_rows[:10]
            
    return {
        "import_job_id": job_id,
        "headers": headers,
        "suggested_mapping": mapping,
        "deck_suggestion": DEFAULT_DECK_NAME,
        "preview_rows": preview_rows
    }

@router.post("/{job_id}/analyze")
async def analyze_import(job_id: str, data: AnalyzeRequest, db: AsyncSession = Depends(get_db)):
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")

    return await analyze_import_job(db, job_id, data.field_mapping, data.deck_selection)


@router.get("/{job_id}/rows")
async def get_import_rows(
    job_id: str,
    page: int = Query(1, ge=1),
    limit: int = Query(20, ge=1, le=100),
    classification: Optional[str] = None,
    action: Optional[str] = None,
    db: AsyncSession = Depends(get_db),
):
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")

    await load_import_job(db, job_id)

    offset = (page - 1) * limit
    
    query = select(ImportRowResult).where(ImportRowResult.import_job_id == job_id)
    if classification:
        query = query.where(ImportRowResult.classification == classification)
    if action:
        query = query.where(ImportRowResult.action == action)
        
    # Count query
    count_query = select(func.count()).select_from(query.subquery())
    total_count_result = await db.execute(count_query)
    total_count = total_count_result.scalar() or 0
    
    # Paginated results
    query = query.order_by(ImportRowResult.row_index).offset(offset).limit(limit)
    rows_result = await db.execute(query)
    rows = rows_result.scalars().all()
    
    results = []
    for r in rows:
        results.append({
            "id": r.id,
            "row_index": r.row_index,
            "original_row_data": json.loads(r.original_row_data),
            "classification": r.classification,
            "action": r.action,
            "message": r.message,
            "card_id": r.card_id
        })
        
    return {
        "total": total_count,
        "page": page,
        "limit": limit,
        "rows": results
    }

@router.post("/{job_id}/commit")
async def commit_import(job_id: str, data: CommitRequest, db: AsyncSession = Depends(get_db)):
    try:
        uuid.UUID(job_id)
    except ValueError:
        raise HTTPException(status_code=400, detail="Invalid job ID format")

    return await commit_import_job(db, job_id, data.idempotency_key, data.request_hash)
