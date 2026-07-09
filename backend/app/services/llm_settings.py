import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.models import LlmSettings
from app.schemas import LlmSettingsResponse, LlmSettingsUpdate


DEFAULT_SETTINGS_ID = "default"
DEFAULT_OPENROUTER_MODEL = "openrouter/owl-alpha"
DEFAULT_OPENAI_COMPATIBLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
PROVIDERS = {"auto", "gemini", "openrouter", "openai_compatible"}


@dataclass(frozen=True)
class LlmRuntimeConfig:
    provider: str
    model: str | None
    base_url: str | None
    api_key: str | None
    timeout_seconds: int


def _clean(value: str | None) -> str | None:
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _env_provider() -> str:
    provider = _clean(os.getenv("LLM_PROVIDER"))
    return provider if provider in PROVIDERS else "auto"


def _default_model(provider: str) -> str:
    if provider == "openrouter":
        return os.getenv("OPENROUTER_MODEL", settings.openrouter_model or DEFAULT_OPENROUTER_MODEL)
    if provider == "openai_compatible":
        return os.getenv("OPENAI_COMPATIBLE_MODEL", settings.llm_model)
    return os.getenv("LLM_MODEL", settings.llm_model)


def _env_api_key(provider: str) -> str | None:
    if provider == "openrouter":
        return _clean(os.getenv("OPENROUTER_API_KEY", settings.openrouter_api_key or ""))
    if provider == "openai_compatible":
        return _clean(os.getenv("OPENAI_COMPATIBLE_API_KEY") or os.getenv("GOOGLE_API_KEY", settings.google_api_key or ""))
    if provider == "gemini":
        return _clean(os.getenv("GOOGLE_API_KEY", settings.google_api_key or ""))
    return (
        _clean(os.getenv("OPENROUTER_API_KEY", settings.openrouter_api_key or ""))
        or _clean(os.getenv("GOOGLE_API_KEY", settings.google_api_key or ""))
        or _clean(os.getenv("OPENAI_COMPATIBLE_API_KEY"))
    )


def _env_base_url(provider: str) -> str | None:
    if provider == "openai_compatible":
        return _clean(os.getenv("OPENAI_COMPATIBLE_BASE_URL")) or DEFAULT_OPENAI_COMPATIBLE_BASE_URL
    return None


def _api_key_source(row: LlmSettings | None, provider: str) -> str:
    if row and _clean(row.api_key):
        return "local"
    if _env_api_key(provider):
        return "environment"
    return "none"


async def get_llm_settings_row(db: AsyncSession) -> LlmSettings | None:
    result = await db.execute(select(LlmSettings).where(LlmSettings.id == DEFAULT_SETTINGS_ID))
    return result.scalar_one_or_none()


async def get_llm_runtime_config(db: AsyncSession) -> LlmRuntimeConfig:
    row = await get_llm_settings_row(db)
    provider = _clean(row.provider if row else None) or _env_provider()
    if provider not in PROVIDERS:
        provider = "auto"

    timeout = row.timeout_seconds if row and row.timeout_seconds else settings.llm_timeout_seconds
    model = _clean(row.model if row else None) or _default_model(provider)
    base_url = _clean(row.base_url if row else None) or _env_base_url(provider)
    api_key = _clean(row.api_key if row else None) or _env_api_key(provider)
    return LlmRuntimeConfig(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=timeout,
    )


async def get_llm_settings_response(db: AsyncSession) -> LlmSettingsResponse:
    row = await get_llm_settings_row(db)
    runtime = await get_llm_runtime_config(db)
    source = _api_key_source(row, runtime.provider)
    return LlmSettingsResponse(
        provider=runtime.provider,  # type: ignore[arg-type]
        model=_clean(row.model if row else None),
        base_url=_clean(row.base_url if row else None),
        timeout_seconds=runtime.timeout_seconds,
        api_key_configured=source != "none",
        api_key_source=source,  # type: ignore[arg-type]
        effective_model=runtime.model or "",
    )


async def update_llm_settings(db: AsyncSession, payload: LlmSettingsUpdate) -> LlmSettingsResponse:
    row = await get_llm_settings_row(db)
    if row is None:
        row = LlmSettings(id=DEFAULT_SETTINGS_ID)
        db.add(row)

    row.provider = payload.provider
    row.model = _clean(payload.model)
    row.base_url = _clean(payload.base_url)
    row.timeout_seconds = payload.timeout_seconds
    if payload.clear_api_key:
        row.api_key = None
    elif payload.api_key is not None and payload.api_key.strip():
        row.api_key = payload.api_key.strip()

    await db.commit()
    return await get_llm_settings_response(db)
