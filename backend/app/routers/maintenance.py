import io
import csv
import json
from typing import Optional
from pydantic import BaseModel
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, desc
from sqlalchemy.orm import aliased

from app.database import get_db
from app.models import (
    Card, ConfusionCount
)
from app.services.deck_scope import DeckScopeError, resolve_single_default_deck_id
from app.services.mistake_exports import (
    MistakeQuery,
    fetch_mistakes,
    filename_for_export,
    recent_start,
    render_mistakes,
    today_utc_bounds,
)

router = APIRouter(prefix="/api/v1", tags=["maintenance"])


class ExportRequest(BaseModel):
    filter_type: str
    deck_id: Optional[str] = None
    limit: Optional[int] = None
    format: str


class ResetProgressRequest(BaseModel):
    confirm: str

# ─── Export Endpoint ───────────────────────────────────────
@router.post("/exports")
async def export_data(data: ExportRequest, db: AsyncSession = Depends(get_db)):
    # Verify export operations do not change FSRS states/logs
    start_date = None
    end_date = None
    if data.filter_type == "recent_7_days":
        start_date = recent_start(7)
    elif data.filter_type == "recent_30_days":
        start_date = recent_start(30)
    elif data.filter_type == "today":
        start_date, end_date = today_utc_bounds()

    # 1. Handle Confusions Export
    if data.filter_type == "confusions":
        TargetCard = aliased(Card)
        WrongCard = aliased(Card)
        query = (
            select(
                ConfusionCount.occurrence_count,
                ConfusionCount.last_occurred_at,
                TargetCard.english.label("target_english"),
                TargetCard.chinese_meaning.label("target_chinese"),
                TargetCard.example_sentence.label("target_example"),
                WrongCard.english.label("wrong_english"),
                WrongCard.chinese_meaning.label("wrong_chinese"),
                WrongCard.example_sentence.label("wrong_example")
            )
            .select_from(ConfusionCount)
            .join(TargetCard, TargetCard.id == ConfusionCount.target_card_id)
            .join(WrongCard, WrongCard.id == ConfusionCount.selected_wrong_card_id)
            .where(ConfusionCount.selected_wrong_card_id != "unknown")
            .order_by(desc(ConfusionCount.occurrence_count), desc(ConfusionCount.last_occurred_at))
        )
        if data.limit:
            query = query.limit(data.limit)
            
        res = await db.execute(query)
        rows = res.all()
        
        # Formatting
        if data.format == "json":
            content_list = []
            for r in rows:
                content_list.append({
                    "target_word": r.target_english,
                    "target_meaning": r.target_chinese,
                    "target_example": r.target_example,
                    "confused_word": r.wrong_english,
                    "confused_meaning": r.wrong_chinese,
                    "confused_example": r.wrong_example,
                    "occurrence_count": r.occurrence_count
                })
            return {"content": json.dumps(content_list, indent=2, ensure_ascii=False), "filename": "confusion_export.json"}
            
        elif data.format == "csv":
            headers = ["Target Word", "Target Meaning", "Target Example", "Confused Word", "Confused Meaning", "Confused Example", "Occurrence Count"]
            csv_rows = []
            for r in rows:
                csv_rows.append([
                    r.target_english, r.target_chinese, r.target_example or "",
                    r.wrong_english, r.wrong_chinese, r.wrong_example or "",
                    r.occurrence_count
                ])
            output = io.StringIO()
            writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
            writer.writerow(headers)
            writer.writerows(csv_rows)
            return {"content": output.getvalue(), "filename": "confusion_export.csv"}
            
        else: # markdown
            md = "# Confusion Pairs Study Export\n\n"
            for r in rows:
                md += f"## Target Term: {r.target_english}\n"
                md += f"- **Traditional Chinese Meaning**: {r.target_chinese}\n"
                md += f"- **Confused Word**: {r.wrong_english} (selected wrong meaning: {r.wrong_chinese})\n"
                md += f"- **Occurrence Count**: {r.occurrence_count}\n"
                md += f"- **Target Example**: {r.target_example or 'N/A'}\n"
                md += f"- **Wrong Example**: {r.wrong_example or 'N/A'}\n\n"
            return {"content": md, "filename": "confusion_export.md"}

    # 2. Handle Mistakes Export
    deck_filter_id = data.deck_id
    if data.filter_type == "deck" and not deck_filter_id:
        try:
            deck_filter_id = await resolve_single_default_deck_id(db)
        except DeckScopeError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message)

    title = "Today's Vocabulary Mistakes for NotebookLM" if data.filter_type == "today" else "Vocabulary Mistakes Study Export"
    _, items = await fetch_mistakes(
        db,
        MistakeQuery(
            start_date=start_date,
            end_date=end_date,
            deck_id=deck_filter_id,
            limit=data.limit,
            sort_by="recent" if data.filter_type == "today" else "severity",
        ),
    )
    export_format = data.format if data.format in {"markdown", "json", "csv", "notebooklm"} else "markdown"
    return {
        "content": render_mistakes(items, export_format, title),
        "filename": filename_for_export(data.filter_type, export_format),
    }

@router.post("/maintenance/reset-progress")
async def reset_progress(data: ResetProgressRequest, db: AsyncSession = Depends(get_db)):
    if data.confirm != "RESET":
        raise HTTPException(status_code=400, detail="Reset confirmation is required.")

    from sqlalchemy import delete
    from app.models import (
        ReviewState, ReviewLog, ConfusionCount,
        PlacementSession, PlacementItem, PlacementEvent,
        PlacementAudit, PlacementAuditItem, PlacementAuditEvent,
        StudySession, SessionItem, ActivationQueue,
        StudyPlan, TypedStudyAnswer
    )
    
    try:
        await db.execute(delete(TypedStudyAnswer))
        await db.execute(delete(ReviewLog))
        await db.execute(delete(ConfusionCount))
        await db.execute(delete(ReviewState))
        await db.execute(delete(ActivationQueue))
        await db.execute(delete(PlacementAuditEvent))
        await db.execute(delete(PlacementAuditItem))
        await db.execute(delete(PlacementAudit))
        await db.execute(delete(PlacementEvent))
        await db.execute(delete(PlacementItem))
        await db.execute(delete(PlacementSession))
        await db.execute(delete(SessionItem))
        await db.execute(delete(StudySession))
        await db.execute(delete(StudyPlan))
        
        await db.commit()
    except Exception as e:
        await db.rollback()
        raise HTTPException(status_code=500, detail=f"Failed to reset progress: {str(e)}")
        
    return {"status": "success", "message": "Progress reset successfully."}
