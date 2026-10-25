import copy
import json

import pytest

from app import d3_adjudicator as d3
from app.constants import DEFAULT_D3_MODEL
from app.llm_adjudicator import AdjudicationItem, AdjudicationUnavailable, PartialAdjudicationUnavailable, adjudicate_answers
from app.services.llm_settings import LlmRuntimeConfig


def runtime(**overrides):
    return LlmRuntimeConfig(**({"provider": "d3", "model": DEFAULT_D3_MODEL, "api_key": "test-secret", "base_url": None, "timeout_seconds": 9} | overrides))


def response(choice="Good"):
    return {
        "model": DEFAULT_D3_MODEL,
        "meta": {"model_id": DEFAULT_D3_MODEL, "revision": "test-revision"},
        "answers": {"grade": {"type": "choice", "choice": choice, "probabilities": {label: .9 if label == choice else .05 for label in ("Again", "Hard", "Good")}}},
    }


@pytest.fixture(autouse=True)
def no_wait(monkeypatch):
    monkeypatch.setattr(d3.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(d3, "_next_request_at", 0.0)
    monkeypatch.delenv("LLM_TEST_MODE", raising=False)


@pytest.mark.parametrize("choice,verdict,rating", [("Good", "correct", "Good"), ("Hard", "partial", "Again"), ("Again", "incorrect", "Again")])
def test_native_choice_maps_semantics_separately_from_fsrs(monkeypatch, choice, verdict, rating):
    seen = {}

    def post(url, headers, body, timeout):
        seen.update(url=url, headers=headers, body=body, timeout=timeout)
        return response(choice)

    monkeypatch.setattr(d3, "_post_json", post)
    result = d3.adjudicate_d3_answer(AdjudicationItem("item-a", "invoice", "發票", "發票"), runtime())
    assert (result.verdict, result.rating, result.provider) == (verdict, rating, "d3")
    assert result.confidence == .9
    assert result.model == f"{DEFAULT_D3_MODEL}@test-revision/{d3.D3_POLICY_VERSION}"
    assert seen["url"] == "https://decision.vllm-sr.ai/v1/systemone"
    assert seen["headers"]["Authorization"] == "Bearer test-secret"
    assert seen["timeout"] == 9
    assert json.loads(seen["body"]["state"])["learner_answer"] == "發票"
    assert "test-secret" not in json.dumps(seen["body"])
    assert "item-a" not in seen["body"]["state"]


@pytest.mark.parametrize("mutation", ["model", "revision", "extra_question", "bad_choice", "nan", "sum", "argmax"])
def test_invalid_provider_response_never_becomes_a_grade(monkeypatch, mutation):
    raw = response()
    answer = raw["answers"]["grade"]
    if mutation == "model":
        raw["model"] = "different-model"
    elif mutation == "revision":
        raw["meta"].pop("revision")
    elif mutation == "extra_question":
        raw["answers"]["other"] = copy.deepcopy(answer)
    elif mutation == "bad_choice":
        answer["choice"] = "Easy"
    elif mutation == "nan":
        answer["probabilities"]["Good"] = float("nan")
    elif mutation == "sum":
        answer["probabilities"]["Good"] = .6
    else:
        answer["choice"] = "Again"
    monkeypatch.setattr(d3, "_post_json", lambda *args: raw)
    with pytest.raises(AdjudicationUnavailable):
        d3.adjudicate_d3_answer(AdjudicationItem("a", "invoice", "發票", "答案"), runtime())


def test_provider_error_redacts_key(monkeypatch):
    def failed(*args):
        raise AdjudicationUnavailable("HTTP 401 test-secret")

    monkeypatch.setattr(d3, "_post_json", failed)
    with pytest.raises(AdjudicationUnavailable) as caught:
        d3.adjudicate_d3_answer(AdjudicationItem("a", "invoice", "發票", "答案"), runtime())
    assert "test-secret" not in str(caught.value)


async def test_d3_failure_preserves_success_and_stops_remaining_calls(monkeypatch):
    calls = []

    def post(*args):
        calls.append(args)
        if len(calls) == 2:
            raise AdjudicationUnavailable("quota unavailable")
        return response()

    monkeypatch.setattr(d3, "_post_json", post)
    items = [AdjudicationItem(str(i), "invoice", "發票", "答案") for i in range(4)]
    with pytest.raises(PartialAdjudicationUnavailable) as caught:
        await adjudicate_answers(items, runtime())
    assert len(calls) == 2
    assert set(caught.value.results) == {"0"}
    assert set(caught.value.errors_by_id) == {"1", "2", "3"}


async def test_revision_change_is_not_silently_mixed(monkeypatch):
    calls = 0

    def post(*args):
        nonlocal calls
        calls += 1
        raw = response()
        raw["meta"]["revision"] = str(calls)
        return raw

    monkeypatch.setattr(d3, "_post_json", post)
    items = [AdjudicationItem(str(i), "invoice", "發票", "答案") for i in range(3)]
    with pytest.raises(PartialAdjudicationUnavailable) as caught:
        await adjudicate_answers(items, runtime())
    assert set(caught.value.results) == {"0"}
    assert calls == 2
