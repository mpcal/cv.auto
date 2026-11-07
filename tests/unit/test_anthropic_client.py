"""AnthropicLLMClient: a missing API key is a user error, not an internal one."""

import pytest

from cvopt.adapters.anthropic_client import AnthropicLLMClient
from cvopt.config import LLMSettings
from cvopt.errors import InputError


async def test_missing_api_key_raises_input_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    client = AnthropicLLMClient(LLMSettings())
    with pytest.raises(InputError, match="ANTHROPIC_API_KEY"):
        await client.complete(
            model="m", system="s", content=[], output_model=LLMSettings, max_tokens=1
        )


@pytest.mark.parametrize("kind", ["connection", "timeout", "rate", "auth", "status", "response"])
async def test_provider_failures_are_safe_task_errors(kind: str) -> None:
    from types import SimpleNamespace

    import anthropic
    import httpx

    from cvopt.errors import LLMTaskError

    request = httpx.Request("POST", "https://example.org")
    errors = {
        "response": anthropic.APIResponseValidationError(
            response=httpx.Response(200, request=request), body=None, message="SECRET-CV"
        ),
        "connection": anthropic.APIConnectionError(message="SECRET-CV", request=request),
        "timeout": anthropic.APITimeoutError(request=request),
        "rate": anthropic.RateLimitError(
            "SECRET-CV", response=httpx.Response(429, request=request), body=None
        ),
        "auth": anthropic.AuthenticationError(
            "SECRET-CV", response=httpx.Response(401, request=request), body=None
        ),
        "status": anthropic.InternalServerError(
            "SECRET-CV", response=httpx.Response(500, request=request), body=None
        ),
    }

    async def create(**kwargs):
        raise errors[kind]

    client = AnthropicLLMClient(LLMSettings())
    client._client = SimpleNamespace(
        messages=SimpleNamespace(with_raw_response=SimpleNamespace(create=create))
    )
    with pytest.raises(LLMTaskError) as caught:
        await client.complete(
            model="m", system="s", content=[], output_model=LLMSettings, max_tokens=1
        )
    assert "SECRET-CV" not in str(caught.value)
    assert "Anthropic" in str(caught.value)


@pytest.mark.parametrize("stop", ["end_turn", "refusal", "max_tokens", "invalid"])
async def test_sdk_raw_response_observes_usage_and_actual_retries(stop):
    """Real installed SDK + synthetic local transport, including one HTTP retry."""
    import json

    import httpx2
    from anthropic import AsyncAnthropic

    seen = []

    def respond(request):
        seen.append(request)
        if len(seen) == 1:
            return httpx2.Response(500, json={"error": {"type": "api_error", "message": "retry"}})
        return httpx2.Response(
            200,
            json={
                "id": "msg_synthetic",
                "type": "message",
                "role": "assistant",
                "model": "m",
                "stop_reason": "end_turn" if stop == "invalid" else stop,
                "stop_sequence": None,
                "content": [
                    {
                        "type": "text",
                        "text": "PRIVATE invalid body" if stop == "invalid" else json.dumps({}),
                    }
                ],
                "usage": {
                    "input_tokens": 20,
                    "output_tokens": 5,
                    "cache_read_input_tokens": 10,
                    "cache_creation_input_tokens": 3,
                },
            },
        )

    async with AsyncAnthropic(
        api_key="synthetic",
        max_retries=1,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(respond)),
    ) as sdk:
        client = AnthropicLLMClient(LLMSettings())
        client._client = sdk
        result = await client.complete(
            model="m", system="synthetic", content=[], output_model=LLMSettings, max_tokens=10
        )
    assert result.retries_taken == 1
    assert result.usage.model_dump() == {
        "input_tokens": 20,
        "output_tokens": 5,
        "cache_read_tokens": 10,
        "cache_write_tokens": 3,
    }
    assert "PRIVATE" not in result.stop_reason
    if stop == "invalid":
        assert result.stop_reason == "invalid_output"


async def test_absent_provider_cache_fields_remain_unobservable():
    from types import SimpleNamespace

    async def parse():
        return SimpleNamespace(
            model="m",
            stop_reason="refusal",
            content=[],
            usage=SimpleNamespace(input_tokens=0, output_tokens=0),
        )

    async def create(**kwargs):
        return SimpleNamespace(parse=parse, retries_taken=0)

    client = AnthropicLLMClient(LLMSettings())
    client._client = SimpleNamespace(
        messages=SimpleNamespace(with_raw_response=SimpleNamespace(create=create))
    )
    result = await client.complete(
        model="m", system="s", content=[], output_model=LLMSettings, max_tokens=1
    )
    assert result.usage.input_tokens == 0
    assert result.usage.cache_read_tokens is None
    assert not result.usage.complete
