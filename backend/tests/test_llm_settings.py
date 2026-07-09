from httpx import AsyncClient


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
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["provider"] == "openai_compatible"
    assert body["model"] == "gemini-2.5-flash"
    assert body["api_key_configured"] is True
    assert body["api_key_source"] == "local"
    assert "api_key" not in body


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
