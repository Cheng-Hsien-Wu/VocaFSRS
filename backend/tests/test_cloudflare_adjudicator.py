import pytest

from app import decision_adjudicator as decision
from app.llm_adjudicator import AdjudicationItem, AdjudicationUnavailable, adjudicate_answers
from app.services.llm_settings import LlmRuntimeConfig


ENDPOINT = "https://api.cloudflare.com/client/v4/accounts/" + "a" * 32 + "/ai/run/@cf/cloudflare/clef"
ITEM = AdjudicationItem("one", "invoice", "發票", "發票")


def runtime(**overrides):
    return LlmRuntimeConfig(**({"provider": "cloudflare", "model": "clef", "api_key": "cf-primary", "base_url": ENDPOINT, "timeout_seconds": 9} | overrides))


def response(choice="Good"):
    return {"success": True, "result": {"model": "clef", "answers": {"grade": {
        "type": "choice", "choice": choice,
        "probabilities": {label: .9 if label == choice else .05 for label in ("Again", "Hard", "Good")},
    }}}}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(decision.time, "sleep", lambda _: None)
    monkeypatch.setenv("CLOUDFLARE_BACKUP_API_TOKEN", "")
    monkeypatch.delenv("LLM_TEST_MODE", raising=False)


@pytest.mark.parametrize("choice,rating", [("Good", "Good"), ("Hard", "Again"), ("Again", "Again")])
async def test_native_api_and_shared_fsrs_mapping(monkeypatch, choice, rating):
    def post(url, headers, payload, timeout):
        assert url == ENDPOINT
        assert headers["Authorization"] == "Bearer cf-primary"
        assert payload["model"] == "clef"
        assert payload["questions"]["grade"]["instructions"] == decision.DECISION_INSTRUCTIONS
        assert "options" not in payload
        return response(choice)

    monkeypatch.setattr(decision, "_post_json", post)
    result = (await adjudicate_answers([ITEM], runtime()))[ITEM.id]
    assert result.provider == "cloudflare"
    assert result.rating == rating
    assert result.model == "@cf/cloudflare/clef/bare-word-v1"


@pytest.mark.parametrize("url", [None, "https://evil.example/", ENDPOINT + "?redirect=evil", ENDPOINT.replace("a" * 32, "bad")])
def test_invalid_endpoint_never_receives_a_key(monkeypatch, url):
    def post(*args):
        pytest.fail("No network call permitted")

    monkeypatch.setattr(decision, "_post_json", post)
    with pytest.raises(AdjudicationUnavailable):
        decision.adjudicate_decision_answer(ITEM, runtime(base_url=url))


def test_only_its_own_backup_key_is_used(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_BACKUP_API_TOKEN", "cf-backup")
    monkeypatch.setenv("D3_BACKUP_API_KEY", "unrelated-key")
    seen = []

    def post(url, headers, payload, timeout):
        seen.append(headers["Authorization"])
        if len(seen) == 1:
            return {"success": False}
        return response()

    monkeypatch.setattr(decision, "_post_json", post)
    assert decision.adjudicate_decision_answer(ITEM, runtime()).rating == "Good"
    assert seen == ["Bearer cf-primary", "Bearer cf-backup"]


@pytest.mark.parametrize("mutation", ["model", "result", "probabilities", "questions"])
def test_invalid_response_is_not_retried_or_graded(monkeypatch, mutation):
    monkeypatch.setenv("CLOUDFLARE_BACKUP_API_TOKEN", "cf-backup")
    raw = response()
    if mutation == "model":
        raw["result"]["model"] = "clef-flash"
    elif mutation == "result":
        raw["result"] = None
    elif mutation == "probabilities":
        raw["result"]["answers"]["grade"]["probabilities"]["Good"] = float("nan")
    else:
        raw["result"]["answers"]["other"] = {}
    calls = []

    def post(*args):
        calls.append(args)
        return raw

    monkeypatch.setattr(decision, "_post_json", post)
    with pytest.raises(AdjudicationUnavailable):
        decision.adjudicate_decision_answer(ITEM, runtime())
    assert len(calls) == 1
