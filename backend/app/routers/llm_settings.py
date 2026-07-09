from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.database import get_db
from app.llm_adjudicator import AdjudicationItem, AdjudicationUnavailable, adjudicate_answers
from app.schemas import LlmSettingsResponse, LlmSettingsTestResponse, LlmSettingsUpdate
from app.services.llm_settings import (
    get_llm_runtime_config,
    get_llm_settings_response,
    update_llm_settings,
)


router = APIRouter(prefix="/api/v1/llm-settings", tags=["llm_settings"])


@router.get("", response_model=LlmSettingsResponse)
async def read_llm_settings(db: AsyncSession = Depends(get_db)):
    return await get_llm_settings_response(db)


@router.put("", response_model=LlmSettingsResponse)
async def save_llm_settings(payload: LlmSettingsUpdate, db: AsyncSession = Depends(get_db)):
    return await update_llm_settings(db, payload)


@router.post("/test", response_model=LlmSettingsTestResponse)
async def test_llm_settings(db: AsyncSession = Depends(get_db)):
    runtime = await get_llm_runtime_config(db)
    try:
        result = await adjudicate_answers(
            [
                AdjudicationItem(
                    id="settings-test",
                    word="apple",
                    expected="蘋果",
                    typed="蘋果",
                    part_of_speech="noun",
                )
            ],
            runtime_config=runtime,
        )
    except AdjudicationUnavailable as exc:
        return LlmSettingsTestResponse(ok=False, error=str(exc)[:1000])

    first = result["settings-test"]
    return LlmSettingsTestResponse(ok=True, provider=first.provider, model=first.model)
