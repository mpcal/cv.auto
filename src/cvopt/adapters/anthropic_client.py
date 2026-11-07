"""AnthropicLLMClient: structured outputs as JSON schema output."""

import base64
from typing import Any

from anthropic import (
    APIConnectionError,
    APIError,
    APIStatusError,
    APITimeoutError,
    AsyncAnthropic,
    transform_schema,
)
from pydantic import BaseModel, ValidationError

from cvopt.config import LLMSettings
from cvopt.errors import InputError, LLMTaskError
from cvopt.observability import TokenUsage
from cvopt.ports import FilePart, LLMResult, TextPart


def to_block(part: TextPart | FilePart) -> dict[str, Any]:
    if isinstance(part, TextPart):
        return {"type": "text", "text": part.text}
    source = {
        "type": "base64",
        "media_type": part.media_type,
        "data": base64.standard_b64encode(part.data).decode("ascii"),
    }
    kind = "document" if part.media_type == "application/pdf" else "image"
    return {"type": kind, "source": source}


class AnthropicLLMClient:
    def __init__(self, s: LLMSettings) -> None:
        self._settings = s
        self._client: AsyncAnthropic | None = None

    def _get(self) -> AsyncAnthropic:
        # Created on first use, so commands without LLM calls need no API key.
        if self._client is None:
            client = AsyncAnthropic(
                max_retries=self._settings.max_retries, timeout=self._settings.request_timeout_s
            )
            if client.api_key is None and client.auth_token is None:
                raise InputError("No Anthropic API key: set ANTHROPIC_API_KEY.")
            self._client = client
        return self._client

    async def complete[T: BaseModel](
        self,
        *,
        model: str,
        system: str,
        content: list[TextPart | FilePart],
        output_model: type[T],
        max_tokens: int,
    ) -> LLMResult[T]:
        # Files go before the text that refers to them.
        blocks = [to_block(p) for p in content if isinstance(p, FilePart)]
        blocks += [to_block(p) for p in content if isinstance(p, TextPart)]
        # Not messages.parse: it parses before stop_reason can be seen, so a reply cut off
        # at max_tokens would surface as invalid JSON instead of max_tokens.
        try:
            raw = await self._get().messages.with_raw_response.create(  # type: ignore[call-overload]
                model=model,
                max_tokens=max_tokens,
                system=system,
                messages=[{"role": "user", "content": blocks}],
                output_config={
                    "format": {"type": "json_schema", "schema": transform_schema(output_model)}
                },
            )
            resp = await raw.parse()
        except APITimeoutError as err:
            raise LLMTaskError(
                "Anthropic request timed out; retry or increase the request timeout."
            ) from err
        except APIConnectionError as err:
            raise LLMTaskError(
                "Cannot connect to Anthropic; check your network and retry."
            ) from err
        except APIStatusError as err:
            if err.status_code in (401, 403):
                message = "Anthropic authentication failed; check your API key and permissions."
            elif err.status_code == 429:
                message = "Anthropic rate limit reached; wait and retry or check account limits."
            elif err.status_code >= 500:
                message = "Anthropic is temporarily unavailable; retry later."
            else:
                message = "Anthropic rejected the request; check model and document settings."
            raise LLMTaskError(message) from err
        except APIError as err:
            raise LLMTaskError("Anthropic returned an invalid response; retry later.") from err
        usage = TokenUsage(
            input_tokens=getattr(resp.usage, "input_tokens", None),
            output_tokens=getattr(resp.usage, "output_tokens", None),
            cache_read_tokens=getattr(resp.usage, "cache_read_input_tokens", None),
            cache_write_tokens=getattr(resp.usage, "cache_creation_input_tokens", None),
        )
        retries = raw.retries_taken
        stop = str(resp.stop_reason)
        if stop in ("refusal", "max_tokens"):
            return LLMResult(
                output=None, stop_reason=stop, model=resp.model, usage=usage, retries_taken=retries
            )
        text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            output = output_model.model_validate_json(text)
        except ValidationError:  # parsed JSON violated the model's own constraints
            return LLMResult(
                output=None,
                stop_reason="invalid_output",
                model=resp.model,
                usage=usage,
                retries_taken=retries,
            )
        return LLMResult(
            output=output, stop_reason=stop, model=resp.model, usage=usage, retries_taken=retries
        )
