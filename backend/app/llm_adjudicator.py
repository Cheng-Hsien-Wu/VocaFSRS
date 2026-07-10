import asyncio
import json
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.constants import APP_NAME
from app.services.llm_settings import (
    DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
    DEFAULT_OPENROUTER_MODEL,
    DEFAULT_PROVIDER_ORDER,
    LlmRoute,
    LlmRuntimeConfig,
)

DEFAULT_LLM_MODEL = settings.llm_model
SKIPPED_TYPED_ANSWER = "不知道"
RETRYABLE_LLM_HTTP_STATUSES = {429, 502, 503, 504}
MAX_LLM_RETRY_SECONDS = 5.0


@dataclass
class AdjudicationResult:
    verdict: str
    rating: str
    reason: str
    confidence: float
    provider: str
    model: str


@dataclass
class AdjudicationItem:
    id: str
    word: str
    expected: str
    typed: str
    part_of_speech: str | None = None


class AdjudicationUnavailable(Exception):
    pass


class PartialAdjudicationUnavailable(AdjudicationUnavailable):
    def __init__(
        self,
        results: dict[str, AdjudicationResult],
        errors_by_id: dict[str, str],
    ) -> None:
        self.results = results
        self.errors_by_id = errors_by_id
        first_error = next(iter(errors_by_id.values()), "unknown LLM error")
        super().__init__(
            f"{len(errors_by_id)} answer(s) failed LLM adjudication: {first_error}"
        )


def _use_mock_adjudication() -> bool:
    return os.getenv("VOCAB_ENV") == "test" and os.getenv("LLM_TEST_MODE") == "mock"


def _mock_adjudication_result(expected: str, typed: str) -> AdjudicationResult:
    typed_norm = typed.strip()
    expected_norm = expected.strip()
    if typed_norm and typed_norm in expected_norm:
        return AdjudicationResult("correct", "Good", "mock semantic match", 1.0, "mock", "mock")
    if typed_norm:
        return AdjudicationResult("partial", "Hard", "mock non-empty answer", 0.5, "mock", "mock")
    return AdjudicationResult("incorrect", "Again", "mock empty answer", 1.0, "mock", "mock")


def _batch_prompt(items: list[AdjudicationItem]) -> str:
    rows = [
        {
            "id": item.id,
            "word": item.word,
            "part_of_speech": item.part_of_speech,
            "expected_chinese_meaning": item.expected,
            "learner_answer": item.typed,
        }
        for item in items
    ]
    return (
        "You are grading vocabulary recall answers. "
        "For each item, the learner sees an English word and types the Chinese meaning. "
        "Grade semantic correctness, not exact wording. "
        "Return only JSON with one key: results. "
        "results must be an array with one object per input item. "
        "Each result object must include: id, verdict, rating, reason, confidence. "
        "verdict must be one of correct, partial, incorrect. "
        "rating must be Good for correct, Hard for partial, Again for incorrect. "
        "confidence must be a number from 0 to 1. "
        "Preserve each input id exactly.\n\n"
        f"Items:\n{json.dumps(rows, ensure_ascii=False)}"
    )


def _extract_json(text: str) -> dict[str, Any]:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.strip("`")
        if stripped.lower().startswith("json"):
            stripped = stripped[4:].strip()
    start = stripped.find("{")
    end = stripped.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("LLM response did not contain JSON")
    return json.loads(stripped[start:end + 1])


def _normalize(payload: dict[str, Any], provider: str, model: str) -> AdjudicationResult:
    verdict = str(payload.get("verdict", "")).strip().lower()
    rating = str(payload.get("rating", "")).strip()
    if verdict not in {"correct", "partial", "incorrect"}:
        raise ValueError(f"invalid verdict: {verdict}")
    expected_rating = {"correct": "Good", "partial": "Hard", "incorrect": "Again"}[verdict]
    rating = expected_rating
    confidence_raw = payload.get("confidence", 0.0)
    try:
        confidence = float(confidence_raw)
    except (TypeError, ValueError):
        confidence = 0.0
    confidence = max(0.0, min(1.0, confidence))
    return AdjudicationResult(
        verdict=verdict,
        rating=rating,
        reason=str(payload.get("reason", "")).strip()[:500],
        confidence=confidence,
        provider=provider,
        model=model,
    )


def _format_http_error(exc: urllib.error.HTTPError) -> str:
    raw = exc.read().decode("utf-8", errors="replace")
    body = raw.strip()
    if len(body) > 700:
        body = body[:700] + "..."
    return f"HTTP {exc.code} {exc.reason}: {body or 'empty response body'}"


def _http_retry_delay(exc: urllib.error.HTTPError) -> float:
    raw_retry_after = exc.headers.get("Retry-After") if exc.headers else None
    if raw_retry_after:
        try:
            retry_after = float(raw_retry_after)
            if math.isfinite(retry_after):
                return max(0.0, min(retry_after, MAX_LLM_RETRY_SECONDS))
        except ValueError:
            pass
    return 1.0


def _post_json(url: str, headers: dict[str, str], body: dict[str, Any], timeout: int) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    for attempt in range(2):
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            if attempt == 0 and exc.code in RETRYABLE_LLM_HTTP_STATUSES:
                time.sleep(_http_retry_delay(exc))
                continue
            raise AdjudicationUnavailable(_format_http_error(exc)) from exc
    raise AssertionError("HTTP retry loop exhausted")


def _adjudication_json_schema_response_format() -> dict[str, Any]:
    return {
        "type": "json_schema",
        "json_schema": {
            "name": "vocabulary_adjudication",
            "strict": True,
            "schema": {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    "results": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "additionalProperties": False,
                            "properties": {
                                "id": {"type": "string"},
                                "verdict": {"type": "string", "enum": ["correct", "partial", "incorrect"]},
                                "rating": {"type": "string", "enum": ["Good", "Hard", "Again"]},
                                "reason": {"type": "string"},
                                "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                            },
                            "required": ["id", "verdict", "rating", "reason", "confidence"],
                        },
                    }
                },
                "required": ["results"],
            },
        },
    }


def _default_runtime_config() -> LlmRuntimeConfig:
    provider = os.getenv("LLM_PROVIDER", "auto").strip() or "auto"
    return LlmRuntimeConfig(
        provider=provider,
        model=None,
        base_url=os.getenv("OPENAI_COMPATIBLE_BASE_URL") or None,
        api_key=None,
        timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", str(settings.llm_timeout_seconds))),
    )


def _openrouter_model(runtime_config: LlmRuntimeConfig, route: LlmRoute | None = None) -> str:
    if route and route.model:
        return route.model
    if runtime_config.provider == "openrouter" and runtime_config.model:
        return runtime_config.model
    return os.getenv("OPENROUTER_MODEL", settings.openrouter_model or DEFAULT_OPENROUTER_MODEL)


def _provider_api_key(runtime_config: LlmRuntimeConfig, provider: str) -> str:
    if runtime_config.provider == provider and runtime_config.api_key:
        return runtime_config.api_key
    if runtime_config.provider == "auto" and runtime_config.api_key:
        if provider == "openrouter":
            return runtime_config.api_key
    if provider == "openrouter":
        return os.getenv("OPENROUTER_API_KEY", settings.openrouter_api_key or "")
    if provider == "gemini":
        return os.getenv("GOOGLE_API_KEY", settings.google_api_key or "")
    return os.getenv("OPENAI_COMPATIBLE_API_KEY") or os.getenv("GOOGLE_API_KEY", settings.google_api_key or "")


def _openrouter_headers(api_key: str) -> dict[str, str]:
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:8080"),
        "X-Title": os.getenv("OPENROUTER_APP_NAME", APP_NAME),
    }


def _call_google_batch(
    prompt: str,
    timeout: int,
    runtime_config: LlmRuntimeConfig,
    route: LlmRoute | None = None,
) -> tuple[list[dict[str, Any]], str, str]:
    api_key = _provider_api_key(runtime_config, "gemini")
    model = (
        route.model
        if route and route.model
        else runtime_config.model
        if runtime_config.provider == "gemini" and runtime_config.model
        else os.getenv("LLM_MODEL", settings.llm_model)
    )
    if not api_key:
        raise AdjudicationUnavailable("GOOGLE_API_KEY is not configured")
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
    response = _post_json(
        url,
        {"Content-Type": "application/json"},
        {
            "contents": [{"parts": [{"text": prompt}]}],
            "generationConfig": {
                "temperature": 0,
                "response_mime_type": "application/json",
            },
        },
        timeout,
    )
    text = response["candidates"][0]["content"]["parts"][0]["text"]
    payload = _extract_json(text)
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("LLM response did not contain results array")
    return results, "google", model


def _call_openrouter_batch(
    prompt: str,
    timeout: int,
    runtime_config: LlmRuntimeConfig,
    route: LlmRoute | None = None,
) -> tuple[list[dict[str, Any]], str, str]:
    api_key = _provider_api_key(runtime_config, "openrouter")
    model = _openrouter_model(runtime_config, route)
    if not api_key:
        raise AdjudicationUnavailable("OPENROUTER_API_KEY is not configured")
    response = _post_json(
        "https://openrouter.ai/api/v1/chat/completions",
        _openrouter_headers(api_key),
        {
            "model": model,
            "temperature": 0,
            "provider": {"require_parameters": True},
            "response_format": _adjudication_json_schema_response_format(),
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout,
    )
    text = response["choices"][0]["message"]["content"]
    payload = _extract_json(text)
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("LLM response did not contain results array")
    return results, "openrouter", model


def _call_openai_compatible_batch(
    prompt: str,
    timeout: int,
    runtime_config: LlmRuntimeConfig,
    route: LlmRoute | None = None,
) -> tuple[list[dict[str, Any]], str, str]:
    api_key = _provider_api_key(runtime_config, "openai_compatible")
    model = (
        route.model
        if route and route.model
        else runtime_config.model
        if runtime_config.provider == "openai_compatible" and runtime_config.model
        else os.getenv("OPENAI_COMPATIBLE_MODEL", settings.llm_model)
    )
    base_url = (
        runtime_config.base_url
        if runtime_config.provider == "openai_compatible" and runtime_config.base_url
        else os.getenv("OPENAI_COMPATIBLE_BASE_URL") or DEFAULT_OPENAI_COMPATIBLE_BASE_URL
    )
    if not api_key:
        raise AdjudicationUnavailable("OpenAI-compatible API key is not configured")
    if not model:
        raise AdjudicationUnavailable("OpenAI-compatible model is not configured")
    url = base_url.rstrip("/")
    if not url.endswith("/chat/completions"):
        url = f"{url}/chat/completions"
    response = _post_json(
        url,
        {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        {
            "model": model,
            "temperature": 0,
            "response_format": {"type": "json_object"},
            "messages": [{"role": "user", "content": prompt}],
        },
        timeout,
    )
    text = response["choices"][0]["message"]["content"]
    payload = _extract_json(text)
    results = payload.get("results")
    if not isinstance(results, list):
        raise ValueError("LLM response did not contain results array")
    return results, "openai_compatible", model


def _provider_callers(runtime_config: LlmRuntimeConfig):
    callers = {
        "openrouter": _call_openrouter_batch,
        "gemini": _call_google_batch,
        "openai_compatible": _call_openai_compatible_batch,
    }
    selected = runtime_config.provider
    if selected in callers:
        routes = [
            LlmRoute(provider=selected, model=runtime_config.model or ""),
            *runtime_config.fallback_routes,
        ]
        route_callers = []
        seen_routes: set[tuple[str, str]] = set()
        for route in routes:
            route_key = (route.provider, route.model)
            if route.provider not in callers or route_key in seen_routes:
                continue
            seen_routes.add(route_key)
            route_callers.append((callers[route.provider], route))
        return route_callers
    return [(callers[provider], None) for provider in DEFAULT_PROVIDER_ORDER]


async def _adjudicate_remote_batch(
    items: list[AdjudicationItem],
    runtime_config: LlmRuntimeConfig,
) -> dict[str, AdjudicationResult]:
    prompt = _batch_prompt(items)
    expected_ids = {item.id for item in items}
    errors: list[str] = []

    for caller, route in _provider_callers(runtime_config):
        try:
            raw_results, provider, model = await asyncio.to_thread(
                caller,
                prompt,
                runtime_config.timeout_seconds,
                runtime_config,
                route,
            )
            normalized: dict[str, AdjudicationResult] = {}
            for raw in raw_results:
                result_id = str(raw.get("id", "")).strip()
                if result_id not in expected_ids:
                    continue
                normalized[result_id] = _normalize(raw, provider, model)
            missing = expected_ids - set(normalized)
            if missing:
                raise ValueError(f"LLM response missing result ids: {', '.join(sorted(missing))}")
            return normalized
        except (
            AdjudicationUnavailable,
            urllib.error.URLError,
            TimeoutError,
            TypeError,
            AttributeError,
            ValueError,
            KeyError,
            IndexError,
            json.JSONDecodeError,
        ) as exc:
            errors.append(f"{caller.__name__}: {exc}")

    raise AdjudicationUnavailable("; ".join(errors) or "no LLM providers configured")


async def adjudicate_answers(
    items: list[AdjudicationItem],
    runtime_config: LlmRuntimeConfig | None = None,
) -> dict[str, AdjudicationResult]:
    if not items:
        return {}

    runtime_config = runtime_config or _default_runtime_config()
    results = {
        item.id: AdjudicationResult(
            verdict="incorrect",
            rating="Again",
            reason="learner selected unknown",
            confidence=1.0,
            provider="local",
            model="rule",
        )
        for item in items
        if item.typed.strip() == SKIPPED_TYPED_ANSWER
    }
    remote_items = [item for item in items if item.id not in results]
    if not remote_items:
        return results
    if _use_mock_adjudication():
        results.update({
            item.id: _mock_adjudication_result(item.expected, item.typed)
            for item in remote_items
        })
        return results

    batch_size = max(1, runtime_config.batch_size)
    batches = [remote_items[index:index + batch_size] for index in range(0, len(remote_items), batch_size)]
    semaphore = asyncio.Semaphore(max(1, runtime_config.max_concurrency))

    async def run_batch(
        batch: list[AdjudicationItem],
    ) -> tuple[dict[str, AdjudicationResult] | None, AdjudicationUnavailable | None]:
        async with semaphore:
            try:
                return await _adjudicate_remote_batch(batch, runtime_config), None
            except AdjudicationUnavailable as exc:
                return None, exc
            except Exception as exc:
                return None, AdjudicationUnavailable(
                    f"{exc.__class__.__name__}: {exc}"
                )

    errors_by_id: dict[str, str] = {}
    batch_outcomes = await asyncio.gather(*(run_batch(batch) for batch in batches))
    for batch, (batch_results, error) in zip(batches, batch_outcomes):
        if batch_results is not None:
            results.update(batch_results)
            continue
        error_message = str(error) if error else "unknown LLM error"
        errors_by_id.update({item.id: error_message for item in batch})

    if errors_by_id:
        raise PartialAdjudicationUnavailable(results, errors_by_id)
    return results
