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
