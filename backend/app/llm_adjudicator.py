import asyncio
import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from app.config import settings
from app.constants import APP_NAME
from app.services.llm_settings import (
    DEFAULT_OPENAI_COMPATIBLE_BASE_URL,
    DEFAULT_OPENROUTER_MODEL,
    LlmRuntimeConfig,
)

DEFAULT_LLM_MODEL = settings.llm_model


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


def _post_json(url: str, headers: dict[str, str], body: dict[str, Any], timeout: int) -> dict[str, Any]:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        raise AdjudicationUnavailable(_format_http_error(exc)) from exc


def _default_runtime_config() -> LlmRuntimeConfig:
    provider = os.getenv("LLM_PROVIDER", "auto").strip() or "auto"
    return LlmRuntimeConfig(
        provider=provider,
        model=None,
        base_url=os.getenv("OPENAI_COMPATIBLE_BASE_URL") or None,
        api_key=None,
        timeout_seconds=int(os.getenv("LLM_TIMEOUT_SECONDS", str(settings.llm_timeout_seconds))),
    )


def _openrouter_model(runtime_config: LlmRuntimeConfig) -> str:
    return runtime_config.model or os.getenv("OPENROUTER_MODEL", settings.openrouter_model or DEFAULT_OPENROUTER_MODEL)


def _openrouter_headers(runtime_config: LlmRuntimeConfig) -> dict[str, str]:
    api_key = runtime_config.api_key or os.getenv("OPENROUTER_API_KEY", settings.openrouter_api_key or "")
    return {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {api_key}",
        "HTTP-Referer": os.getenv("OPENROUTER_SITE_URL", "http://localhost:8080"),
        "X-Title": os.getenv("OPENROUTER_APP_NAME", APP_NAME),
    }


def _call_google_batch(prompt: str, timeout: int, runtime_config: LlmRuntimeConfig) -> tuple[list[dict[str, Any]], str, str]:
    api_key = runtime_config.api_key or os.getenv("GOOGLE_API_KEY", settings.google_api_key or "")
    model = runtime_config.model or os.getenv("LLM_MODEL", settings.llm_model)
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


def _call_openrouter_batch(prompt: str, timeout: int, runtime_config: LlmRuntimeConfig) -> tuple[list[dict[str, Any]], str, str]:
    api_key = runtime_config.api_key or os.getenv("OPENROUTER_API_KEY", settings.openrouter_api_key or "")
    model = _openrouter_model(runtime_config)
    if not api_key:
        raise AdjudicationUnavailable("OPENROUTER_API_KEY is not configured")
    response = _post_json(
        "https://openrouter.ai/api/v1/chat/completions",
        _openrouter_headers(runtime_config),
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
    return results, "openrouter", model


def _call_openai_compatible_batch(prompt: str, timeout: int, runtime_config: LlmRuntimeConfig) -> tuple[list[dict[str, Any]], str, str]:
    api_key = runtime_config.api_key or os.getenv("OPENAI_COMPATIBLE_API_KEY") or os.getenv("GOOGLE_API_KEY", settings.google_api_key or "")
    model = runtime_config.model or os.getenv("OPENAI_COMPATIBLE_MODEL", settings.llm_model)
    base_url = runtime_config.base_url or os.getenv("OPENAI_COMPATIBLE_BASE_URL") or DEFAULT_OPENAI_COMPATIBLE_BASE_URL
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
        return [callers[selected]]
    return [_call_openrouter_batch, _call_google_batch, _call_openai_compatible_batch]


async def adjudicate_answers(
    items: list[AdjudicationItem],
    runtime_config: LlmRuntimeConfig | None = None,
) -> dict[str, AdjudicationResult]:
    if not items:
        return {}

    runtime_config = runtime_config or _default_runtime_config()
    timeout = runtime_config.timeout_seconds

    if _use_mock_adjudication():
        return {item.id: _mock_adjudication_result(item.expected, item.typed) for item in items}

    prompt = _batch_prompt(items)
    expected_ids = {item.id for item in items}
    errors: list[str] = []

    for caller in _provider_callers(runtime_config):
        try:
            raw_results, provider, model = await asyncio.to_thread(caller, prompt, timeout, runtime_config)
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
        except (AdjudicationUnavailable, urllib.error.URLError, TimeoutError, ValueError, KeyError, IndexError, json.JSONDecodeError) as exc:
            errors.append(f"{caller.__name__}: {exc}")

    raise AdjudicationUnavailable("; ".join(errors) or "no LLM providers configured")
