import os
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.constants import DEFAULT_LLM_BATCH_SIZE, DEFAULT_LLM_MAX_CONCURRENCY
from app.models import LlmSettings
from app.schemas import LlmSettingsResponse, LlmSettingsUpdate


DEFAULT_SETTINGS_ID = "default"
DEFAULT_OPENROUTER_MODEL = "openrouter/owl-alpha"
DEFAULT_OPENAI_COMPATIBLE_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions"
PROVIDERS = {"auto", "gemini", "openrouter", "openai_compatible"}
DEFAULT_PROVIDER_ORDER = ("openrouter", "gemini", "openai_compatible")


@dataclass(frozen=True)
class LlmRoute:
    provider: str
    model: str


@dataclass(frozen=True)
class LlmRuntimeConfig:
    provider: str
    model: str | None
    base_url: str | None
    api_key: str | None
    timeout_seconds: int
    fallback_routes: tuple[LlmRoute, ...] = ()
    batch_size: int = DEFAULT_LLM_BATCH_SIZE
    max_concurrency: int = DEFAULT_LLM_MAX_CONCURRENCY


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


def _load_fallback_routes(row: LlmSettings | None, primary_provider: str) -> tuple[LlmRoute, ...]:
    routes: list[LlmRoute] = []
    for item in (row.fallback_routes_json if row else []) or []:
        if isinstance(item, str):
            provider = item
            model = _default_model(provider) if provider in PROVIDERS - {"auto"} else ""
        elif isinstance(item, dict):
            provider = _clean(item.get("provider")) or ""
            model = _clean(item.get("model")) or ""
        else:
            continue
        if provider in PROVIDERS - {"auto"} and model:
            routes.append(LlmRoute(provider=provider, model=model))
    return tuple(routes) if primary_provider != "auto" else ()


def _route_chain(runtime: LlmRuntimeConfig) -> tuple[LlmRoute, ...]:
    if runtime.provider != "auto":
        return (
            LlmRoute(provider=runtime.provider, model=runtime.model or _default_model(runtime.provider)),
            *runtime.fallback_routes,
        )
    return tuple(LlmRoute(provider=provider, model=_default_model(provider)) for provider in DEFAULT_PROVIDER_ORDER)


def _provider_key_source(
    row: LlmSettings | None,
    selected_provider: str,
    provider: str,
) -> str:
    has_local_key = bool(row and _clean(row.api_key))
    local_key_provider = selected_provider if selected_provider != "auto" else DEFAULT_PROVIDER_ORDER[0]
    if has_local_key and provider == local_key_provider:
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
    local_api_key = _clean(row.api_key if row else None)
    api_key = local_api_key or (None if provider == "auto" else _env_api_key(provider))
    fallback_routes = _load_fallback_routes(row, provider)
    return LlmRuntimeConfig(
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        timeout_seconds=timeout,
        fallback_routes=fallback_routes,
        batch_size=row.batch_size if row and row.batch_size else DEFAULT_LLM_BATCH_SIZE,
        max_concurrency=row.max_concurrency if row and row.max_concurrency else DEFAULT_LLM_MAX_CONCURRENCY,
    )


async def get_llm_settings_response(db: AsyncSession) -> LlmSettingsResponse:
    row = await get_llm_settings_row(db)
    runtime = await get_llm_runtime_config(db)
    source = _api_key_source(row, runtime.provider)
    route_chain = _route_chain(runtime)
    provider_sources = {
        provider: _provider_key_source(row, runtime.provider, provider)
        for provider in DEFAULT_PROVIDER_ORDER
    }
    return LlmSettingsResponse(
        provider=runtime.provider,  # type: ignore[arg-type]
        model=_clean(row.model if row else None),
        base_url=_clean(row.base_url if row else None),
        timeout_seconds=runtime.timeout_seconds,
        api_key_configured=source != "none",
        api_key_source=source,  # type: ignore[arg-type]
        effective_model=runtime.model or "",
        fallback_routes=[
            {"provider": route.provider, "model": route.model}
            for route in runtime.fallback_routes
        ],  # type: ignore[list-item]
        batch_size=runtime.batch_size,
        max_concurrency=runtime.max_concurrency,
        effective_route_chain=[
            {"provider": route.provider, "model": route.model}
            for route in route_chain
            if provider_sources[route.provider] != "none"
        ],  # type: ignore[list-item]
        provider_readiness=[
            {
                "provider": provider,
                "api_key_configured": provider_sources[provider] != "none",
                "api_key_source": provider_sources[provider],
                "effective_model": _default_model(provider),
                "fallback_available": bool(_env_api_key(provider)),
            }
            for provider in DEFAULT_PROVIDER_ORDER
        ],
    )


async def update_llm_settings(db: AsyncSession, payload: LlmSettingsUpdate) -> LlmSettingsResponse:
    row = await get_llm_settings_row(db)
    if row is None:
        row = LlmSettings(
            id=DEFAULT_SETTINGS_ID,
            timeout_seconds=settings.llm_timeout_seconds,
            fallback_routes_json=[],
            batch_size=DEFAULT_LLM_BATCH_SIZE,
            max_concurrency=DEFAULT_LLM_MAX_CONCURRENCY,
        )
        db.add(row)

    row.provider = payload.provider
    row.model = _clean(payload.model)
    row.base_url = _clean(payload.base_url)
    if payload.timeout_seconds is not None:
        row.timeout_seconds = payload.timeout_seconds
    if payload.fallback_routes is not None:
        row.fallback_routes_json = [route.model_dump() for route in payload.fallback_routes]
    if payload.batch_size is not None:
        row.batch_size = payload.batch_size
    if payload.max_concurrency is not None:
        row.max_concurrency = payload.max_concurrency
    if payload.clear_api_key:
        row.api_key = None
    elif payload.api_key is not None and payload.api_key.strip():
        row.api_key = payload.api_key.strip()

    await db.commit()
    return await get_llm_settings_response(db)
