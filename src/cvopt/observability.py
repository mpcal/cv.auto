"""Local, optional operation observers and conservative new-billing accounting.

Events contain only identifiers, normalized outcomes, durations and counters. No
prompts, response text, source material or exception messages cross this boundary.
"""

import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from ulid import ULID

from cvopt.config import LLMSettings

TokenKind = Literal["input", "output", "cache_read", "cache_write"]
TOKEN_FIELDS: dict[TokenKind, str] = {
    "input": "input_tokens",
    "output": "output_tokens",
    "cache_read": "cache_read_tokens",
    "cache_write": "cache_write_tokens",
}
Outcome = Literal["started", "succeeded", "invalid_output", "refused", "truncated", "error", "hit"]


class TokenUsage(BaseModel):
    """Provider billing categories; None is unobservable, zero is measured zero.

    Input excludes provider cache reads/writes. Output is the inclusive billed
    output count (including any reasoning), never a sum of detail subcategories.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")
    input_tokens: int | None = Field(default=None, ge=0, strict=True)
    output_tokens: int | None = Field(default=None, ge=0, strict=True)
    cache_read_tokens: int | None = Field(default=None, ge=0, strict=True)
    cache_write_tokens: int | None = Field(default=None, ge=0, strict=True)

    @property
    def complete(self) -> bool:
        return all(getattr(self, field) is not None for field in TOKEN_FIELDS.values())


def validate_retries(value: int | None) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError("Retry counts must be nonnegative integers or unavailable.")


@dataclass(frozen=True)
class ProgressEvent:
    operation_id: str
    operation: str
    kind: Literal["stage", "llm", "application_cache"]
    status: Outcome
    stage: str | None = None
    task: str | None = None
    model: str | None = None
    duration_s: float | None = None
    llm_calls: int = 0
    application_cache_hits: int = 0
    usage: TokenUsage | None = None
    retries_taken: int | None = None
    schema_retry: bool = False


Observer = Callable[[ProgressEvent], None]


class Operation:
    def __init__(self, name: str, settings: LLMSettings, observer: Observer | None = None) -> None:
        self.id = str(ULID())
        self.name, self.settings, self.observer = name, settings, observer
        self.started_at = datetime.now(UTC)
        self.started = time.monotonic()
        self.finished_at: datetime | None = None
        self.duration_s: float | None = None
        self.status = "running"
        self.calls = 0
        self.hits = 0
        self.schema_retries = 0
        self.provider_retries = 0
        self.provider_retries_complete = True
        self.token_usage_complete = True
        self.models: dict[str, dict[str, int]] = {}
        self._fields_complete: dict[str, dict[str, bool]] = {}
        self.stages: list[dict[str, object]] = []
        self._stage: ContextVar[str | None] = ContextVar("cvopt_stage", default=None)
        self.tasks: dict[str, dict[str, int]] = {}

    def emit(
        self,
        kind: Literal["stage", "llm", "application_cache"],
        status: Outcome,
        *,
        stage: str | None = None,
        task: str | None = None,
        model: str | None = None,
        duration_s: float | None = None,
        usage: TokenUsage | None = None,
        retries_taken: int | None = None,
        schema_retry: bool = False,
    ) -> None:
        if self.observer is not None:
            # Observation must not change publication or provider behavior.
            with suppress(Exception):
                self.observer(
                    ProgressEvent(
                        self.id,
                        self.name,
                        kind,
                        status,
                        stage or self._stage.get(),
                        task,
                        model,
                        duration_s,
                        self.calls,
                        self.hits,
                        usage,
                        retries_taken,
                        schema_retry,
                    )
                )

    @contextmanager
    def stage(self, name: str) -> Iterator[None]:
        started = time.monotonic()
        token = self._stage.set(name)
        self.emit("stage", "started", stage=name)
        status: Outcome = "succeeded"
        try:
            yield
        except BaseException:
            status = "error"
            raise
        finally:
            duration = time.monotonic() - started
            self.stages.append({"stage": name, "status": status, "duration_s": duration})
            self.emit("stage", status, stage=name, duration_s=duration)
            self._stage.reset(token)

    def cache_hit(self, task: str, model: str) -> None:
        self.hits += 1
        counts = self.tasks.setdefault(task, {})
        counts["application_cache_hits"] = counts.get("application_cache_hits", 0) + 1
        self.emit("application_cache", "hit", task=task, model=model)

    def call(
        self,
        task: str,
        model: str,
        status: Outcome,
        usage: TokenUsage | None,
        *,
        retries_taken: int | None,
        duration_s: float | None = None,
        schema_retry: bool = False,
    ) -> None:
        validate_retries(retries_taken)
        self.calls += 1
        self.schema_retries += int(schema_retry)
        counts = self.tasks.setdefault(task, {})
        counts[status] = counts.get(status, 0) + 1
        self.provider_retries += retries_taken or 0
        self.provider_retries_complete &= retries_taken is not None
        self.token_usage_complete &= usage is not None and usage.complete and retries_taken == 0
        tokens = self.models.setdefault(model, dict.fromkeys(TOKEN_FIELDS, 0))
        fields_complete = self._fields_complete.setdefault(model, dict.fromkeys(TOKEN_FIELDS, True))
        for kind, field in TOKEN_FIELDS.items():
            measured = getattr(usage, field) if usage is not None else None
            tokens[kind] += measured or 0
            fields_complete[kind] &= measured is not None and retries_taken == 0
        self.emit(
            "llm",
            status,
            task=task,
            model=model,
            duration_s=duration_s,
            usage=usage,
            retries_taken=retries_taken,
            schema_retry=schema_retry,
        )

    def finish(self, status: str) -> None:
        self.status = status
        self.finished_at = datetime.now(UTC)
        self.duration_s = time.monotonic() - self.started

    def summary(self, *, status: str | None = None) -> dict[str, object]:
        subtotal = 0.0
        rates_complete = True
        models: dict[str, object] = {}
        for model, tokens in self.models.items():
            prices = self.settings.prices.get(model)
            model_subtotal = 0.0
            unpriced = []
            for kind, count in tokens.items():
                rate = getattr(prices, kind) if prices is not None else None
                if count and rate is None:
                    rates_complete = False
                    unpriced.append(kind)
                elif rate is not None:
                    model_subtotal += count * rate / 1_000_000
            subtotal += model_subtotal
            models[model] = {
                "known_tokens": dict(tokens),
                "token_fields_complete": dict(self._fields_complete[model]),
                "known_subtotal_usd": model_subtotal,
                "unpriced_categories": unpriced,
            }
        complete = self.token_usage_complete and rates_complete
        return {
            "schema_version": 2,
            "operation_id": self.id,
            "operation": self.name,
            "billing_scope": "operation",
            "status": status or self.status,
            "started_at": self.started_at.isoformat(),
            "finished_at": (
                self.finished_at.isoformat()
                if self.finished_at
                else datetime.now(UTC).isoformat()
                if status and status != "running"
                else None
            ),
            "duration_s": self.duration_s
            if self.duration_s is not None
            else time.monotonic() - self.started,
            "counters": {
                "llm_calls": self.calls,
                "application_cache_hits": self.hits,
                "schema_retries": self.schema_retries,
                "provider_retries": self.provider_retries,
                "provider_retries_complete": self.provider_retries_complete,
            },
            "tasks": {k: dict(v) for k, v in self.tasks.items()},
            "models": models,
            "token_usage_complete": self.token_usage_complete,
            "prices_complete": rates_complete,
            "known_subtotal_usd": subtotal,
            "total_usd": subtotal if complete else None,
            "cost_complete": complete,
            "stages": [dict(s) for s in self.stages],
        }
