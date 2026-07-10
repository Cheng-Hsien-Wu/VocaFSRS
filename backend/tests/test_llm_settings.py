import pytest
from httpx import AsyncClient


def test_legacy_fallback_provider_list_is_converted_to_model_routes(monkeypatch):
    from types import SimpleNamespace

    from app.services.llm_settings import _load_fallback_routes

    monkeypatch.setenv("OPENROUTER_MODEL", "openrouter/legacy-default")
    row = SimpleNamespace(fallback_routes_json=["openrouter", "gemini"])

    routes = _load_fallback_routes(row, "openai_compatible")

    assert [(route.provider, route.model) for route in routes] == [
        ("openrouter", "openrouter/legacy-default"),
        ("gemini", "gemini-2.5-flash"),
    ]


@pytest.mark.parametrize("api_key_env", ["GOOGLE_API_KEY", "OPENAI_COMPATIBLE_API_KEY"])
async def test_auto_runtime_does_not_promote_environment_key_to_generic_key(
    monkeypatch,
    api_key_env,
):
    from app.services import llm_settings

    async def no_settings_row(_db):
        return None

    for env_name in (
        "GOOGLE_API_KEY",
        "OPENROUTER_API_KEY",
        "OPENAI_COMPATIBLE_API_KEY",
        "LLM_PROVIDER",
    ):
        monkeypatch.delenv(env_name, raising=False)
    monkeypatch.setenv(api_key_env, "provider-specific-secret")
    monkeypatch.setattr(llm_settings, "get_llm_settings_row", no_settings_row)

    runtime = await llm_settings.get_llm_runtime_config(None)

    assert runtime.provider == "auto"
    assert runtime.api_key is None


async def test_llm_settings_round_trip_does_not_return_secret(client: AsyncClient):
    res = await client.get("/api/v1/llm-settings")
    assert res.status_code == 200
    assert res.json()["provider"] == "auto"
    assert "api_key" not in res.json()

    updated = await client.put(
        "/api/v1/llm-settings",
        json={
            "provider": "openai_compatible",
            "model": "gemini-2.5-flash",
            "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/chat/completions",
            "api_key": "secret-test-key",
            "timeout_seconds": 30,
            "fallback_routes": [
                {"provider": "openrouter", "model": "openrouter/backup-a"},
                {"provider": "openrouter", "model": "openrouter/backup-b"},
            ],
            "batch_size": 10,
            "max_concurrency": 2,
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["provider"] == "openai_compatible"
    assert body["model"] == "gemini-2.5-flash"
    assert body["api_key_configured"] is True
    assert body["api_key_source"] == "local"
    assert body["fallback_routes"] == [
        {"provider": "openrouter", "model": "openrouter/backup-a"},
        {"provider": "openrouter", "model": "openrouter/backup-b"},
    ]
    assert body["batch_size"] == 10
    assert body["max_concurrency"] == 2
    assert body["effective_route_chain"] == [
        {"provider": "openai_compatible", "model": "gemini-2.5-flash"},
    ]
    assert body["provider_readiness"] == [
        {
            "provider": "openrouter",
            "api_key_configured": False,
            "api_key_source": "none",
            "effective_model": "openrouter/owl-alpha",
            "fallback_available": False,
        },
        {
            "provider": "gemini",
            "api_key_configured": False,
            "api_key_source": "none",
            "effective_model": "gemini-2.5-flash",
            "fallback_available": False,
        },
        {
            "provider": "openai_compatible",
            "api_key_configured": True,
            "api_key_source": "local",
            "effective_model": "gemini-2.5-flash",
            "fallback_available": False,
        },
    ]
    assert "api_key" not in body


async def test_llm_settings_reports_environment_fallback_readiness(client: AsyncClient, monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-env-key")
    monkeypatch.setenv("OPENROUTER_MODEL", "openrouter/env-model")

    updated = await client.put(
        "/api/v1/llm-settings",
        json={
            "provider": "gemini",
            "model": "gemini/custom-model",
            "api_key": "gemini-local-key",
            "timeout_seconds": 30,
            "fallback_routes": [
                {"provider": "openrouter", "model": "openrouter/backup-model"},
            ],
            "batch_size": 10,
            "max_concurrency": 2,
        },
    )

    assert updated.status_code == 200
    body = updated.json()
    assert body["effective_model"] == "gemini/custom-model"
    assert body["effective_route_chain"] == [
        {"provider": "gemini", "model": "gemini/custom-model"},
        {"provider": "openrouter", "model": "openrouter/backup-model"},
    ]
    readiness = {item["provider"]: item for item in body["provider_readiness"]}
    assert readiness["gemini"]["api_key_source"] == "local"
    assert readiness["openrouter"] == {
        "provider": "openrouter",
        "api_key_configured": True,
        "api_key_source": "environment",
        "effective_model": "openrouter/env-model",
        "fallback_available": True,
    }


async def test_legacy_settings_update_preserves_omitted_new_fields(client: AsyncClient):
    configured = await client.put(
        "/api/v1/llm-settings",
        json={
            "provider": "openrouter",
            "model": "openrouter/primary",
            "api_key": "secret-test-key",
            "timeout_seconds": 30,
            "fallback_routes": [
                {"provider": "openrouter", "model": "openrouter/backup"},
            ],
            "batch_size": 7,
            "max_concurrency": 3,
        },
    )
    assert configured.status_code == 200

    legacy_update = await client.put(
        "/api/v1/llm-settings",
        json={
            "provider": "openrouter",
            "model": "openrouter/new-primary",
        },
    )

    assert legacy_update.status_code == 200
    body = legacy_update.json()
    assert body["timeout_seconds"] == 30
    assert body["fallback_routes"] == [
        {"provider": "openrouter", "model": "openrouter/backup"},
    ]
    assert body["batch_size"] == 7
    assert body["max_concurrency"] == 3


async def test_explicit_empty_fallback_routes_clears_existing_routes(client: AsyncClient):
    configured = await client.put(
        "/api/v1/llm-settings",
        json={
            "provider": "openrouter",
            "fallback_routes": [
                {"provider": "openrouter", "model": "openrouter/backup"},
            ],
        },
    )
    assert configured.status_code == 200

    cleared = await client.put(
        "/api/v1/llm-settings",
        json={
            "provider": "openrouter",
            "fallback_routes": [],
        },
    )

    assert cleared.status_code == 200
    assert cleared.json()["fallback_routes"] == []


async def test_llm_settings_test_uses_runtime_config(client: AsyncClient, monkeypatch):
    from app.llm_adjudicator import AdjudicationResult
    from app.routers import llm_settings

    await client.put(
        "/api/v1/llm-settings",
        json={
            "provider": "openrouter",
            "model": "openrouter/test-model",
            "api_key": "secret-test-key",
            "timeout_seconds": 12,
        },
    )

    seen = {}

    async def fake_adjudicate_answers(items, runtime_config=None):
        seen["provider"] = runtime_config.provider
        seen["model"] = runtime_config.model
        seen["timeout"] = runtime_config.timeout_seconds
        return {
            items[0].id: AdjudicationResult(
                verdict="correct",
                rating="Good",
                reason="ok",
                confidence=1.0,
                provider=runtime_config.provider,
                model=runtime_config.model,
            )
        }

    monkeypatch.setattr(llm_settings, "adjudicate_answers", fake_adjudicate_answers)

    res = await client.post("/api/v1/llm-settings/test")
    assert res.status_code == 200
    assert res.json() == {"ok": True, "provider": "openrouter", "model": "openrouter/test-model", "error": None}
    assert seen == {"provider": "openrouter", "model": "openrouter/test-model", "timeout": 12}
