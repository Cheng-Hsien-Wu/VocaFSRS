import csv
import io
import json
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased
from sqlalchemy.sql.expression import case

from app.config import settings
from app.constants import AdjudicationStatus
from app.models import Card, DeckCard, ReviewLog, ReviewState, TypedStudyAnswer


MistakeFormat = Literal["markdown", "json", "csv", "notebooklm"]


@dataclass(frozen=True)
class MistakeQuery:
    start_date: datetime | None = None
    end_date: datetime | None = None
    deck_id: str | None = None
    rating: str | None = None
    repeated_lapses: bool = False
    minimum_again_count: int | None = None
    page: int | None = None
    limit: int | None = None
    sort_by: Literal["recent", "severity"] = "recent"


def today_utc_bounds() -> tuple[datetime, datetime]:
    tz = ZoneInfo(settings.report_timezone)
    today = datetime.now(tz).date()
    local_start = datetime.combine(today, time.min, tzinfo=tz)
    local_end = local_start + timedelta(days=1)
    return (
        local_start.astimezone(timezone.utc).replace(tzinfo=None),
        local_end.astimezone(timezone.utc).replace(tzinfo=None),
    )


def recent_start(days: int) -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=days)


def _date_filters(column, query: MistakeQuery) -> list[Any]:
    filters = []
    if query.start_date:
        filters.append(column >= query.start_date)
    if query.end_date:
        filters.append(column < query.end_date)
    return filters


def _ranked_latest_subquery(model, id_label: str, date_column, query: MistakeQuery, extra_filters: list[Any] | None = None):
    filters = [*(extra_filters or []), *_date_filters(date_column, query)]
    stmt = select(
        model.card_id,
        model.id.label(id_label),
        func.row_number()
        .over(
            partition_by=model.card_id,
            order_by=(desc(date_column), desc(model.created_at), desc(model.id)),
        )
        .label("rank"),
    )
    if filters:
        stmt = stmt.where(and_(*filters))
    ranked = stmt.subquery()
    return select(ranked.c.card_id, ranked.c[id_label]).where(ranked.c.rank == 1).subquery()


def _latest_typed_answer_subquery(query: MistakeQuery):
    typed_filters = [TypedStudyAnswer.adjudication_status == AdjudicationStatus.SUCCEEDED]
    return _ranked_latest_subquery(
        TypedStudyAnswer,
        "latest_typed_answer_id",
        TypedStudyAnswer.answered_at,
        query,
        typed_filters,
    )


def _base_mistakes_query(query: MistakeQuery):
    log_filters = _date_filters(ReviewLog.reviewed_at, query)

    logs_agg_query = select(
        ReviewLog.card_id,
        func.sum(case((ReviewLog.rating == 1, 1), else_=0)).label("again_count"),
        func.sum(case((ReviewLog.rating == 2, 1), else_=0)).label("hard_count"),
        func.max(ReviewLog.reviewed_at).label("last_review_time"),
    )
    if log_filters:
        logs_agg_query = logs_agg_query.where(and_(*log_filters))
    logs_agg = logs_agg_query.group_by(ReviewLog.card_id).subquery()

    latest_log = _ranked_latest_subquery(
        ReviewLog,
        "latest_review_log_id",
        ReviewLog.reviewed_at,
        query,
    )
    latest_log_alias = aliased(ReviewLog)
    latest_confusion = _ranked_latest_subquery(
        ReviewLog,
        "latest_confusion_log_id",
        ReviewLog.reviewed_at,
        query,
        [ReviewLog.rating == 1],
    )
    latest_confusion_log_alias = aliased(ReviewLog)
    confused_card_alias = aliased(Card)
    latest_typed_time = _latest_typed_answer_subquery(query)
    typed_alias = aliased(TypedStudyAnswer)

    stmt = (
        select(
            Card.id,
            Card.english,
            Card.chinese_meaning,
            Card.part_of_speech,
            Card.sense_hint,
            Card.example_sentence,
            Card.example_translation,
            ReviewState.lapses,
            ReviewState.due.label("next_due"),
            logs_agg.c.again_count,
            logs_agg.c.hard_count,
            logs_agg.c.last_review_time,
            latest_confusion_log_alias.selected_option_card_id.label("confused_card_id"),
            confused_card_alias.english.label("confused_word"),
            confused_card_alias.chinese_meaning.label("confused_meaning"),
            typed_alias.typed_answer,
            typed_alias.verdict,
            typed_alias.rating.label("llm_rating"),
            typed_alias.reason.label("llm_reason"),
            typed_alias.confidence.label("llm_confidence"),
            typed_alias.answered_at.label("typed_answered_at"),
        )
        .select_from(Card)
        .outerjoin(ReviewState, ReviewState.card_id == Card.id)
        .outerjoin(logs_agg, logs_agg.c.card_id == Card.id)
        .outerjoin(latest_log, latest_log.c.card_id == Card.id)
        .outerjoin(
            latest_log_alias,
            latest_log_alias.id == latest_log.c.latest_review_log_id,
        )
        .outerjoin(latest_confusion, latest_confusion.c.card_id == Card.id)
        .outerjoin(
            latest_confusion_log_alias,
            latest_confusion_log_alias.id == latest_confusion.c.latest_confusion_log_id,
        )
        .outerjoin(confused_card_alias, confused_card_alias.id == latest_confusion_log_alias.selected_option_card_id)
        .outerjoin(latest_typed_time, latest_typed_time.c.card_id == Card.id)
        .outerjoin(
            typed_alias,
            typed_alias.id == latest_typed_time.c.latest_typed_answer_id,
        )
    )

    if query.deck_id:
        stmt = stmt.join(DeckCard, DeckCard.card_id == Card.id).where(DeckCard.deck_id == query.deck_id)

    if query.minimum_again_count is not None:
        stmt = stmt.where(logs_agg.c.again_count >= query.minimum_again_count)
    else:
        visibility_filters = []
        if query.rating == "Again":
            visibility_filters.append(latest_log_alias.rating == 1)
        elif query.rating == "Hard":
            visibility_filters.append(latest_log_alias.rating == 2)
        else:
            visibility_filters.extend([latest_log_alias.rating == 1, latest_log_alias.rating == 2])
        stmt = stmt.where(or_(*visibility_filters))
    if query.repeated_lapses:
        stmt = stmt.where(ReviewState.lapses >= 2)

    if query.sort_by == "severity":
        stmt = stmt.order_by(desc(logs_agg.c.again_count + logs_agg.c.hard_count), Card.english)
    else:
        stmt = stmt.order_by(desc(logs_agg.c.last_review_time), Card.english)

    return stmt


def _row_to_item(row: Any) -> dict[str, Any]:
    confused_word = row.confused_word
    wrong_meaning = row.confused_meaning
    if row.confused_card_id == "unknown":
        confused_word = "不知道"
        wrong_meaning = None

    return {
        "id": row.id,
        "english": row.english,
        "chinese_meaning": row.chinese_meaning,
        "part_of_speech": row.part_of_speech,
        "sense_hint": row.sense_hint,
        "again_count": int(row.again_count or 0),
        "hard_count": int(row.hard_count or 0),
        "lapses": int(row.lapses or 0),
        "last_review_time": row.last_review_time.isoformat() if row.last_review_time else None,
        "next_due": row.next_due.isoformat() if row.next_due else None,
        "confused_word": confused_word,
        "selected_wrong_meaning": wrong_meaning,
        "typed_answer": row.typed_answer,
        "verdict": row.verdict,
        "llm_rating": row.llm_rating,
        "llm_reason": row.llm_reason,
        "llm_confidence": row.llm_confidence,
        "typed_answered_at": row.typed_answered_at.isoformat() if row.typed_answered_at else None,
        "example_sentence": row.example_sentence,
        "example_translation": row.example_translation,
    }


async def fetch_mistakes(db: AsyncSession, query: MistakeQuery) -> tuple[int, list[dict[str, Any]]]:
    stmt = _base_mistakes_query(query)
    total = (await db.execute(select(func.count()).select_from(stmt.subquery()))).scalar() or 0

    if query.page is not None and query.limit is not None:
        stmt = stmt.offset((query.page - 1) * query.limit).limit(query.limit)
    elif query.limit is not None:
        stmt = stmt.limit(query.limit)

    rows = (await db.execute(stmt)).all()
    return total, [_row_to_item(row) for row in rows]


def render_mistakes(items: list[dict[str, Any]], export_format: MistakeFormat, title: str) -> str:
    if export_format == "json":
        return json.dumps(items, indent=2, ensure_ascii=False)

    if export_format == "csv":
        output = io.StringIO()
        writer = csv.writer(output, quoting=csv.QUOTE_MINIMAL)
        writer.writerow([
            "English",
            "Traditional Chinese Meaning",
            "Again Count",
            "Hard Count",
            "Confused Word",
            "Selected Wrong Meaning",
            "Example Sentence",
            "Example Translation",
        ])
        for item in items:
            writer.writerow([
                item["english"],
                item["chinese_meaning"],
                item["again_count"],
                item["hard_count"],
                item.get("confused_word") or "",
                item.get("selected_wrong_meaning") or "",
                item.get("example_sentence") or "",
                item.get("example_translation") or "",
            ])
        return output.getvalue()

    if export_format == "notebooklm":
        return render_notebooklm(items, title)

    lines = [f"# {title}", ""]
    for item in items:
        lines.extend(_markdown_item(item))
    return "\n".join(lines)


def render_notebooklm(items: list[dict[str, Any]], title: str) -> str:
    lines = [
        "Create an English vocabulary review podcast based on the list of words and phrases in the source.",
        "",
        "The listener has already studied these words but failed to recall them during practice. The goal is to strengthen memory while improving English listening comprehension.",
        "",
        "Cover every numbered item in the source. Do not skip any item.",
        "",
        "For each word or phrase:",
        "- pronounce it clearly;",
        "- explain its meaning in simple English;",
        "- use it in a natural sentence;",
        "- briefly explain how it is commonly used;",
        "- mention a common collocation, preposition, or sentence pattern when relevant.",
        "",
        "Do not simply read the list. Connect the vocabulary through a natural and interesting conversation.",
        "",
        "Regularly quiz the listener. Before explaining a word, give a sentence with enough context and ask the listener to guess the missing word. Pause briefly before revealing the answer.",
        "",
        "End with:",
        "1. a rapid review of every target word;",
        "2. five sentence-completion questions;",
        "3. a short story that naturally uses as many target words as possible.",
        "",
        "Do not treat additional words introduced during the discussion as target vocabulary.",
        "",
        "Target vocabulary:",
        "",
    ]
    for index, item in enumerate(items, 1):
        pos = item.get("part_of_speech") or "N/A"
        lines.append(f"{index}. {item['english']}")
        lines.append(f"- Part of speech: {pos}")
        lines.append(f"- Correct meaning: {item['chinese_meaning']}")
        if item.get("confused_word"):
            confused = item["confused_word"]
            if item.get("selected_wrong_meaning"):
                confused += f" ({item['selected_wrong_meaning']})"
            lines.append(f"- My answer: {item.get('typed_answer') or confused}")
        elif item.get("typed_answer"):
            lines.append(f"- My answer: {item['typed_answer']}")
        if item.get("example_sentence"):
            lines.append(f"- Example: {item['example_sentence']}")
        lines.append("")
    return "\n".join(lines)


def _markdown_item(item: dict[str, Any]) -> list[str]:
    lines = [
        f"## Term: {item['english']}",
        f"- **Traditional Chinese Meaning**: {item['chinese_meaning']}",
        f"- **Again Count**: {item['again_count']}",
        f"- **Hard Count**: {item['hard_count']}",
    ]
    if item.get("typed_answer"):
        lines.append(f"- **Typed Answer**: {item['typed_answer']}")
    if item.get("llm_rating"):
        lines.append(f"- **LLM Rating**: {item['llm_rating']}")
    if item.get("llm_reason"):
        lines.append(f"- **LLM Reason**: {item['llm_reason']}")
    if item.get("confused_word"):
        line = f"- **Confused Word**: {item['confused_word']}"
        if item.get("selected_wrong_meaning"):
            line += f" (selected wrong meaning: {item['selected_wrong_meaning']})"
        lines.append(line)
    lines.append(f"- **Example Sentence**: {item.get('example_sentence') or 'N/A'}")
    lines.append(f"- **Example Translation**: {item.get('example_translation') or 'N/A'}")
    if item.get("next_due"):
        lines.append(f"- **Next Due**: {item['next_due']}")
    lines.append("")
    return lines


def filename_for_export(filter_type: str, export_format: str) -> str:
    today = date.today().isoformat()
    extension = "md" if export_format in {"markdown", "notebooklm"} else export_format
    return f"vocab_{filter_type}_{today}.{extension}"
