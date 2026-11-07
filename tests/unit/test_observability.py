"""Synthetic operation accounting: missing measurements never imply free usage."""

import math

import pytest
from pydantic import ValidationError

from cvopt.config import LLMSettings
from cvopt.ports import LLMResult


def test_usage_fields_are_optional_and_zero_is_observable():
    from cvopt.observability import TokenUsage

    assert LLMResult(output=None, stop_reason="refusal", model="m").usage is None
    usage = TokenUsage(input_tokens=0, output_tokens=0, cache_read_tokens=0, cache_write_tokens=0)
    assert usage.complete
    assert not TokenUsage(input_tokens=0).complete
    for value in (-1, 1.5, True):
        with pytest.raises(ValidationError):
            TokenUsage(input_tokens=value)
        with pytest.raises(ValueError):
            LLMResult(output=None, stop_reason="refusal", model="m", retries_taken=value)


@pytest.mark.parametrize("rate", [-1, math.inf, -math.inf, math.nan])
def test_model_prices_must_be_finite_nonnegative(rate):
    with pytest.raises(ValidationError):
        LLMSettings.model_validate({"prices": {"m": {"input": rate}}})


def test_cost_subtotal_unknown_prices_and_cache_only_zero():
    from cvopt.observability import Operation, TokenUsage

    settings = LLMSettings.model_validate({"prices": {"m": {"input": 2, "output": 4}}})
    op = Operation("generation", settings)
    op.call(
        "judge_match",
        "m",
        "succeeded",
        TokenUsage(input_tokens=100, output_tokens=10, cache_read_tokens=0, cache_write_tokens=0),
        retries_taken=0,
    )
    summary = op.summary()
    assert summary["total_usd"] == pytest.approx(0.00024)
    assert summary["cost_complete"]
    op.call(
        "judge_match",
        "unknown",
        "refused",
        TokenUsage(input_tokens=2, output_tokens=1, cache_read_tokens=0, cache_write_tokens=0),
        retries_taken=0,
    )
    summary = op.summary()
    assert summary["total_usd"] is None
    assert summary["known_subtotal_usd"] == pytest.approx(0.00024)
    assert summary["token_usage_complete"]
    assert not summary["cost_complete"]
    cache = Operation("generation", LLMSettings())
    cache.cache_hit("judge_match", "unpriced")
    assert cache.summary()["total_usd"] == 0
    assert cache.summary()["token_usage_complete"]
    assert cache.summary()["counters"]["application_cache_hits"] == 1


def test_hidden_retries_missing_usage_and_stage_events_are_safe():
    from cvopt.observability import Operation, TokenUsage

    events = []
    op = Operation("generation", LLMSettings(), events.append)
    with op.stage("score"):
        op.call("judge_match", "m", "invalid_output", TokenUsage(input_tokens=10), retries_taken=2)
    op.call("translate", "m", "error", None, retries_taken=None)
    result = op.summary(status="failed")
    assert not result["token_usage_complete"]
    assert result["total_usd"] is None
    assert result["counters"]["provider_retries"] == 2
    assert not result["counters"]["provider_retries_complete"]
    assert result["stages"][0]["duration_s"] >= 0
    assert [e.kind for e in events] == ["stage", "llm", "stage", "llm"]
    assert events[0].status == "started"
    assert events[2].status == "succeeded"
    assert events[2].stage == "score"


def test_provider_cache_tokens_have_their_own_rates_and_event_measurements():
    from cvopt.observability import Operation, TokenUsage

    events = []
    rates = LLMSettings.model_validate(
        {"prices": {"m": {"input": 2, "output": 4, "cache_read": 0.5, "cache_write": 1}}}
    )
    op = Operation("generation", rates, events.append)
    with op.stage("matching"):
        op.call(
            "judge_match",
            "m",
            "succeeded",
            TokenUsage(
                input_tokens=100, output_tokens=10, cache_read_tokens=8, cache_write_tokens=5
            ),
            retries_taken=0,
        )
    summary = op.summary()
    assert summary["total_usd"] == pytest.approx(0.000249)
    assert summary["models"]["m"]["known_tokens"]["cache_read"] == 8
    assert events[1].usage.cache_write_tokens == 5
    assert events[1].retries_taken == 0
    assert events[1].stage == "matching"


def test_published_token_categories_distinguish_missing_measurements_from_zero():
    from cvopt.observability import Operation, TokenUsage

    op = Operation("generation", LLMSettings())
    op.call("translate", "m", "succeeded", TokenUsage(input_tokens=0), retries_taken=0)
    model = op.summary()["models"]["m"]
    assert model["known_tokens"]["input"] == model["known_tokens"]["output"] == 0
    assert model["token_fields_complete"]["input"]
    assert not model["token_fields_complete"]["output"]
    assert not op.summary()["token_usage_complete"]
