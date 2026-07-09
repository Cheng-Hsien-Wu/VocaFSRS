from app.llm_adjudicator import _call_openrouter_batch
from app.services.llm_settings import LlmRuntimeConfig


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
