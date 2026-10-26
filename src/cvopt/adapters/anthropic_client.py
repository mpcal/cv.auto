"""AnthropicLLMClient: structured outputs as JSON schema output."""

import base64
from typing import Any

from anthropic import AsyncAnthropic, transform_schema
from pydantic import BaseModel, ValidationError

from cvopt.config import LLMSettings
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
            self._client = AsyncAnthropic(
                max_retries=self._settings.max_retries, timeout=self._settings.request_timeout_s
            )
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
        resp = await self._get().messages.create(  # type: ignore[call-overload]
            model=model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": blocks}],
            output_config={
                "format": {"type": "json_schema", "schema": transform_schema(output_model)}
            },
        )
        stop = str(resp.stop_reason)
        if stop in ("refusal", "max_tokens"):
            return LLMResult(output=None, stop_reason=stop, model=resp.model)
        text = "".join(b.text for b in resp.content if b.type == "text")
        try:
            output = output_model.model_validate_json(text)
        except ValidationError as err:  # parsed JSON violated the model's own constraints
            return LLMResult(output=None, stop_reason=f"invalid_output: {err}", model=resp.model)
        return LLMResult(output=output, stop_reason=stop, model=resp.model)
