import asyncio
import io
import json
import urllib.error

import pytest

from app.llm_adjudicator import (
    AdjudicationItem,
    AdjudicationResult,
    AdjudicationUnavailable,
    PartialAdjudicationUnavailable,
    _post_json,
    _call_openrouter_batch,
    adjudicate_answers,
)
from app.services.llm_settings import LlmRoute, LlmRuntimeConfig


def test_openrouter_uses_json_schema_response_format(monkeypatch):
    seen = {}

    def fake_post_json(url, headers, body, timeout):
        seen["url"] = url
        seen["headers"] = headers
        seen["body"] = body
        seen["timeout"] = timeout
        return {
            "choices": [
                {
                    "message": {
                        "content": '{"results":[{"id":"item-1","verdict":"correct","rating":"Good","reason":"ok","confidence":1}]}'
                    }
                }
            ]
        }

    monkeypatch.setattr("app.llm_adjudicator._post_json", fake_post_json)

    results, provider, model = _call_openrouter_batch(
        "prompt",
        9,
        LlmRuntimeConfig(
            provider="openrouter",
            model="tencent/hy3",
            base_url=None,
            api_key="test-key",
            timeout_seconds=9,
        ),
    )

    response_format = seen["body"]["response_format"]
    assert seen["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert seen["headers"]["Authorization"] == "Bearer test-key"
    assert seen["timeout"] == 9
    assert seen["body"]["provider"] == {"require_parameters": True}
    assert response_format["type"] == "json_schema"
    assert response_format["json_schema"]["schema"]["required"] == ["results"]
    assert results[0]["id"] == "item-1"
    assert provider == "openrouter"
    assert model == "tencent/hy3"


def test_llm_http_rate_limit_retries_once_with_bounded_delay(monkeypatch):
    attempts = 0
    delays: list[float] = []

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return json.dumps({"ok": True}).encode("utf-8")

    def fake_urlopen(_request, timeout):
        nonlocal attempts
        attempts += 1
        assert timeout == 9
        if attempts == 1:
            raise urllib.error.HTTPError(
                "https://example.test",
                429,
                "Too Many Requests",
                {"Retry-After": "120"},
                io.BytesIO(b'{"error":"rate limited"}'),
            )
        return FakeResponse()

    monkeypatch.setattr("app.llm_adjudicator.urllib.request.urlopen", fake_urlopen)
    monkeypatch.setattr("app.llm_adjudicator.time.sleep", delays.append)

    assert _post_json("https://example.test", {}, {"test": True}, 9) == {"ok": True}
    assert attempts == 2
    assert delays == [5.0]


@pytest.mark.asyncio
async def test_openrouter_fallback_uses_openrouter_environment_key(monkeypatch):
    import app.llm_adjudicator as adjudicator

    seen = {}

    def fake_post_json(url, headers, _body, _timeout):
        if "generativelanguage.googleapis.com" in url:
            assert "key=gemini-primary-key" in url
            raise AdjudicationUnavailable("Gemini unavailable")
        seen["authorization"] = headers["Authorization"]
        return {
            "choices": [{
                "message": {
                    "content": '{"results":[{"id":"item-1","verdict":"correct","rating":"Good","reason":"ok","confidence":1}]}'
                }
            }]
        }

    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-fallback-key")
    monkeypatch.setattr("app.llm_adjudicator._post_json", fake_post_json)

    runtime = LlmRuntimeConfig(
        provider="gemini",
        model="gemini-test",
        base_url=None,
        api_key="gemini-primary-key",
        timeout_seconds=9,
        fallback_routes=(LlmRoute(provider="openrouter", model="openrouter/fallback"),),
    )
    results = await adjudicator._adjudicate_remote_batch(
        [AdjudicationItem(id="item-1", word="apple", expected="蘋果", typed="蘋果")],
        runtime,
    )

    assert seen["authorization"] == "Bearer openrouter-fallback-key"
    assert results["item-1"].provider == "openrouter"


@pytest.mark.asyncio
async def test_same_openrouter_key_can_try_multiple_models_in_order(monkeypatch):
    import app.llm_adjudicator as adjudicator

    attempted_models: list[str] = []

    def fake_post_json(_url, _headers, body, _timeout):
        attempted_models.append(body["model"])
        if body["model"] != "openrouter/backup-b":
            raise AdjudicationUnavailable("model unavailable")
        return {
            "choices": [{
                "message": {
                    "content": '{"results":[{"id":"item-1","verdict":"correct","rating":"Good","reason":"ok","confidence":1}]}'
                }
            }]
        }

    monkeypatch.setattr("app.llm_adjudicator._post_json", fake_post_json)
    runtime = LlmRuntimeConfig(
        provider="openrouter",
        model="openrouter/primary",
        base_url=None,
        api_key="shared-openrouter-key",
        timeout_seconds=9,
        fallback_routes=(
            LlmRoute(provider="openrouter", model="openrouter/primary"),
            LlmRoute(provider="openrouter", model="openrouter/backup-a"),
            LlmRoute(provider="openrouter", model="openrouter/backup-b"),
        ),
    )

    results = await adjudicator._adjudicate_remote_batch(
        [AdjudicationItem(id="item-1", word="apple", expected="蘋果", typed="蘋果")],
        runtime,
    )

    assert attempted_models == ["openrouter/primary", "openrouter/backup-a", "openrouter/backup-b"]
    assert results["item-1"].model == "openrouter/backup-b"


@pytest.mark.parametrize(
    ("api_key_env", "expected_provider"),
    [
        ("GOOGLE_API_KEY", "google"),
        ("OPENAI_COMPATIBLE_API_KEY", "openai_compatible"),
    ],
)
@pytest.mark.asyncio
async def test_auto_provider_does_not_send_other_provider_keys_to_openrouter(
    monkeypatch,
    api_key_env,
    expected_provider,
):
    import app.llm_adjudicator as adjudicator

    for env_name in (
        "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_COMPATIBLE_API_KEY",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv(api_key_env, "provider-specific-secret")
    posted_urls: list[str] = []

    def fake_post_json(url, _headers, _body, _timeout):
        posted_urls.append(url)
        result = {
            "id": "item-1",
            "verdict": "correct",
            "rating": "Good",
            "reason": "ok",
            "confidence": 1,
        }
        if expected_provider == "google":
            return {
                "candidates": [{
                    "content": {"parts": [{"text": json.dumps({"results": [result]})}]}
                }]
            }
        return {
            "choices": [{
                "message": {"content": json.dumps({"results": [result]})}
            }]
        }

    monkeypatch.setattr(adjudicator, "_post_json", fake_post_json)
    runtime = LlmRuntimeConfig(
        provider="auto",
        model=None,
        base_url=None,
        api_key=None,
        timeout_seconds=9,
    )

    results = await adjudicator._adjudicate_remote_batch(
        [AdjudicationItem(id="item-1", word="apple", expected="蘋果", typed="蘋果")],
        runtime,
    )

    assert all("openrouter.ai" not in url for url in posted_urls)
    assert results["item-1"].provider == expected_provider


@pytest.mark.asyncio
async def test_adjudication_skips_unknown_and_batches_by_stable_ids(monkeypatch):
    import app.llm_adjudicator as adjudicator

    active = 0
    max_active = 0
    batch_ids: list[list[str]] = []

    async def fake_remote_batch(items, _runtime_config):
        nonlocal active, max_active
        active += 1
        max_active = max(max_active, active)
        batch_ids.append([item.id for item in items])
        await asyncio.sleep(0.01)
        active -= 1
        return {
            item.id: AdjudicationResult("correct", "Good", "ok", 1.0, "fake", "fake")
            for item in items
        }

    monkeypatch.setattr(adjudicator, "_adjudicate_remote_batch", fake_remote_batch)
    items = [
        AdjudicationItem(id="a0", word="w0", expected="e0", typed="不知道"),
        *[
            AdjudicationItem(id=f"a{index}", word=f"w{index}", expected=f"e{index}", typed=f"t{index}")
            for index in range(1, 6)
        ],
    ]
    runtime = LlmRuntimeConfig(
        provider="gemini",
        model="test",
        base_url=None,
        api_key="key",
        timeout_seconds=9,
        batch_size=2,
        max_concurrency=2,
    )

    results = await adjudicate_answers(items, runtime)

    assert set(results) == {item.id for item in items}
    assert results["a0"].rating == "Again"
    assert results["a0"].provider == "local"
    assert sorted(len(batch) for batch in batch_ids) == [1, 2, 2]
    assert all("a0" not in batch for batch in batch_ids)
    assert max_active == 2


@pytest.mark.asyncio
async def test_adjudication_preserves_successful_batches_when_one_batch_fails(monkeypatch):
    import app.llm_adjudicator as adjudicator

    async def fake_remote_batch(items, _runtime_config):
        if items[0].id == "a2":
            raise AdjudicationUnavailable("provider timeout")
        return {
            item.id: AdjudicationResult("correct", "Good", "ok", 1.0, "fake", "fake")
            for item in items
        }

    monkeypatch.setattr(adjudicator, "_adjudicate_remote_batch", fake_remote_batch)
    items = [
        AdjudicationItem(id=f"a{index}", word="word", expected="字", typed="答案")
        for index in range(4)
    ]
    runtime = LlmRuntimeConfig(
        provider="gemini",
        model="test",
        base_url=None,
        api_key="key",
        timeout_seconds=9,
        batch_size=2,
        max_concurrency=2,
    )

    with pytest.raises(PartialAdjudicationUnavailable) as caught:
        await adjudicate_answers(items, runtime)

    assert set(caught.value.results) == {"a0", "a1"}
    assert set(caught.value.errors_by_id) == {"a2", "a3"}
    assert all("provider timeout" in error for error in caught.value.errors_by_id.values())


@pytest.mark.asyncio
async def test_adjudication_preserves_successful_batches_on_unexpected_batch_error(monkeypatch):
    import app.llm_adjudicator as adjudicator

    async def fake_remote_batch(items, _runtime_config):
        if items[0].id == "a2":
            raise TypeError("malformed provider payload")
        return {
            item.id: AdjudicationResult("correct", "Good", "ok", 1.0, "fake", "fake")
            for item in items
        }

    monkeypatch.setattr(adjudicator, "_adjudicate_remote_batch", fake_remote_batch)
    items = [
        AdjudicationItem(id=f"a{index}", word="word", expected="字", typed="答案")
        for index in range(4)
    ]
    runtime = LlmRuntimeConfig(
        provider="gemini",
        model="test",
        base_url=None,
        api_key="key",
        timeout_seconds=9,
        batch_size=2,
        max_concurrency=2,
    )

    with pytest.raises(PartialAdjudicationUnavailable) as caught:
        await adjudicate_answers(items, runtime)

    assert set(caught.value.results) == {"a0", "a1"}
    assert set(caught.value.errors_by_id) == {"a2", "a3"}
    assert all("TypeError: malformed provider payload" in error for error in caught.value.errors_by_id.values())
