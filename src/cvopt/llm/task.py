"""Task definitions, input canonicalization and cache keys."""

import hashlib
import json
import re
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel

from cvopt.ports import FilePart, TextPart

Tier = Literal["strong", "fast"]


@dataclass(frozen=True)
class Task[I: BaseModel, O: BaseModel]:
    name: str  # "parse_jd"; system prompt in resources/prompts/<name>.md
    version: str  # bump on any change to prompt, schema or render
    tier: Tier
    input_model: type[I]
    output_model: type[O]
    max_tokens: int
    render: Callable[[I], list[TextPart | FilePart]]  # builds the user message
    check: Callable[[I, O], None] | None = None  # raises ValueError on semantic errors


def _canonical(value: object) -> object:
    if isinstance(value, str):
        return re.sub(r"\s+", " ", unicodedata.normalize("NFC", value)).strip()
    if isinstance(value, bytes):
        return {"sha256": hashlib.sha256(value).hexdigest()}
    if isinstance(value, dict):
        return {str(k): _canonical(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_canonical(v) for v in value]
    return value


def canonical_json(inp: BaseModel) -> str:
    """Sorted keys, NFC, collapsed whitespace; bytes enter as their sha256."""
    return json.dumps(
        _canonical(inp.model_dump(mode="python")), sort_keys=True, ensure_ascii=False, default=str
    )


def cache_key(task: str, version: str, model: str, canonical_input: str) -> str:
    payload = json.dumps([task, version, model, canonical_input], ensure_ascii=False)
    return hashlib.sha256(payload.encode()).hexdigest()
