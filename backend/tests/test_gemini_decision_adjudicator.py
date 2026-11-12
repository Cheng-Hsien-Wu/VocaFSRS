import pytest

from app import decision_adjudicator as decision
from app.llm_adjudicator import AdjudicationItem, AdjudicationUnavailable, adjudicate_answers
from app.services.llm_settings import LlmRuntimeConfig


ITEM = AdjudicationItem("one", "invoice", "發票", "發票")


def runtime():
    return LlmRuntimeConfig(provider="gemini_decision", model="gemini-decision-1", api_key="google-primary", base_url=None, timeout_seconds=9)


def response(choice="Good"):
    return {"answers": {"grade": {"type": "choice", "choice": choice, "confidence": .9,
        "probabilities": {label: .9 if label == choice else .05 for label in ("Again", "Hard", "Good")}}}}


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    monkeypatch.setattr(decision.time, "sleep", lambda _: None)
    monkeypatch.setenv("GEMINI_DECISION_BACKUP_API_KEY", "")
    monkeypatch.delenv("LLM_TEST_MODE", raising=False)


@pytest.mark.parametrize("choice,rating", [("Good", "Good"), ("Hard", "Again"), ("Again", "Again")])
async def test_native_google_transport_and_mapping(monkeypatch, choice, rating):
    def post(url, headers, payload, timeout):
        assert url == decision.GEMINI_DECISION_API_URL
        assert headers == {"Content-Type": "application/json", "x-goog-api-key": "google-primary"}
        assert "input" in payload and "state" not in payload and "options" not in payload
        assert payload["questions"]["grade"]["instructions"] == decision.DECISION_INSTRUCTIONS
        return response(choice)

    monkeypatch.setattr(decision, "_post_json", post)
    result = (await adjudicate_answers([ITEM], runtime()))[ITEM.id]
    assert result.provider == "gemini_decision"
    assert result.rating == rating
    assert result.model == "gemini-decision-1/bare-word-v1"


def test_google_backup_only(monkeypatch):
    monkeypatch.setenv("GEMINI_DECISION_BACKUP_API_KEY", "google-backup")
    monkeypatch.setenv("D3_BACKUP_API_KEY", "unrelated")
    seen = []

    def post(url, headers, payload, timeout):
        seen.append(headers["x-goog-api-key"])
        if len(seen) == 1:
            raise AdjudicationUnavailable("temporary error")
        return response()

    monkeypatch.setattr(decision, "_post_json", post)
    assert decision.adjudicate_decision_answer(ITEM, runtime()).rating == "Good"
    assert seen == ["google-primary", "google-backup"]


@pytest.mark.parametrize("field,value", [("confidence", float("nan")), ("confidence", True), ("choice", "Easy"), ("probabilities", {"Good": 1})])
def test_invalid_response_never_graded_or_retried(monkeypatch, field, value):
    monkeypatch.setenv("GEMINI_DECISION_BACKUP_API_KEY", "google-backup")
    raw = response()
    raw["answers"]["grade"][field] = value
    calls = []

    def post(*args):
        calls.append(1)
        return raw

    monkeypatch.setattr(decision, "_post_json", post)
    with pytest.raises(AdjudicationUnavailable):
        decision.adjudicate_decision_answer(ITEM, runtime())
    assert len(calls) == 1
