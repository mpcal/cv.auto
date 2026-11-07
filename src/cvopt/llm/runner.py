"""TaskRunner: cache, call, validate, retry."""

import asyncio
import logging
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import cache
from importlib.resources import files

from pydantic import BaseModel, ValidationError

from cvopt.config import Settings
from cvopt.context import UserContext
from cvopt.errors import LLMOutputError, LLMTaskError
from cvopt.llm.task import Task, cache_key, canonical_json
from cvopt.observability import Observer, Operation, Outcome
from cvopt.ports import LLMClient, Repository, RepositoryFactory, TextPart

log = logging.getLogger(__name__)


@cache
def load_prompt(name: str) -> str:
    prompts = files("cvopt.resources").joinpath("prompts")
    common = prompts.joinpath("_common.md").read_text(encoding="utf-8")
    return common + "\n\n" + prompts.joinpath(f"{name}.md").read_text(encoding="utf-8")


class TaskRunner:
    def __init__(
        self,
        client: LLMClient,
        repo: Repository,
        settings: Settings,
        semaphore: asyncio.Semaphore,
        operation: Callable[[], Operation | None] = lambda: None,
    ) -> None:
        self._client = client
        self._repo = repo
        self._settings = settings
        self._semaphore = semaphore
        self._operation = operation

    async def run[I: BaseModel, O: BaseModel](self, task: Task[I, O], inp: I) -> O:
        model = self._settings.llm.model_for(task.tier)
        key = cache_key(task.name, task.version, model, canonical_json(inp))
        if (hit := await self._repo.cache_get(key)) is not None:
            log.info("llm cache hit", extra={"task": task.name, "cache": "hit"})
            out = task.output_model.model_validate_json(hit)
            if (op := self._operation()) is not None:
                op.cache_hit(task.name, model)
            return out
        max_tokens = task.max_tokens
        feedback: list[TextPart] = []
        for attempt in range(1 + self._settings.llm.schema_retries):
            started = time.monotonic()
            op = self._operation()
            try:
                async with self._semaphore:
                    res = await self._client.complete(
                        model=model,
                        system=load_prompt(task.name),
                        content=[*task.render(inp), *feedback],
                        output_model=task.output_model,
                        max_tokens=max_tokens,
                    )
            except BaseException:
                if op is not None:
                    op.call(
                        task.name,
                        model,
                        "error",
                        None,
                        retries_taken=None,
                        duration_s=time.monotonic() - started,
                        schema_retry=attempt > 0,
                    )
                raise
            status: Outcome = "succeeded"
            if res.stop_reason == "refusal":
                status = "refused"
            elif res.stop_reason == "max_tokens":
                status = "truncated"
            else:
                try:
                    out = task.output_model.model_validate(res.output)
                    if task.check:
                        task.check(inp, out)
                except (ValidationError, ValueError) as err:
                    status = "invalid_output"
                    feedback = [
                        TextPart(f"Your previous answer was rejected: {err}. Answer again.")
                    ]
                except BaseException:
                    if op is not None:
                        op.call(
                            task.name,
                            res.model,
                            "error",
                            res.usage,
                            retries_taken=res.retries_taken,
                            duration_s=time.monotonic() - started,
                            schema_retry=attempt > 0,
                        )
                    raise
            if op is not None:
                op.call(
                    task.name,
                    res.model,
                    status,
                    res.usage,
                    retries_taken=res.retries_taken,
                    duration_s=time.monotonic() - started,
                    schema_retry=attempt > 0,
                )
            log.info(
                "llm call",
                extra={
                    "task": task.name,
                    "cache": "miss",
                    "attempt": attempt,
                    "stop_reason": status,
                    "duration_s": round(time.monotonic() - started, 2),
                },
            )
            if status == "refused":
                raise LLMTaskError(f"{task.name}: the model refused")
            if status == "truncated":
                max_tokens *= 2
                continue
            if status == "invalid_output":
                continue
            await self._repo.cache_put(key, task.name, task.version, model, out.model_dump_json())
            return out
        raise LLMOutputError(f"{task.name}: no valid output after retries")

    async def invalidate[I: BaseModel, O: BaseModel](self, task: Task[I, O], inp: I) -> None:
        """Remove only this user's exact producer input/model/prompt cache entry."""
        key, _ = self._key(task.name, task.version, task.tier, inp)
        await self._repo.cache_delete(key)

    def _key(self, name: str, version: str, tier: str, inp: BaseModel) -> tuple[str, str]:
        model = self._settings.llm.model_for(tier)
        return cache_key(name, version, model, canonical_json(inp)), model

    async def cached[O: BaseModel](
        self, name: str, version: str, tier: str, inp: BaseModel, output_model: type[O]
    ) -> O | None:
        """A finer-grained cache entry than a whole task call (e.g. one judged pair)."""
        key, model = self._key(name, version, tier, inp)
        hit = await self._repo.cache_get(key)
        if hit is None:
            return None
        out = output_model.model_validate_json(hit)
        if (op := self._operation()) is not None:
            op.cache_hit(name, model)
        return out

    async def store(
        self, name: str, version: str, tier: str, inp: BaseModel, out: BaseModel
    ) -> None:
        key, model = self._key(name, version, tier, inp)
        await self._repo.cache_put(key, name, version, model, out.model_dump_json())


class TaskRunnerFactory:
    """Runners are per user, because the LLM cache is."""

    def __init__(
        self,
        client: LLMClient,
        repos: RepositoryFactory,
        settings: Settings,
        observer: Observer | None = None,
    ) -> None:
        self._client = client
        self._repos = repos
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.llm.max_concurrency)
        self._observer = observer
        self._active: ContextVar[tuple[str, Operation] | None] = ContextVar(
            "cvopt_operation", default=None
        )

    @contextmanager
    def operation(
        self, user: UserContext, name: str, *, observer: Observer | None = None
    ) -> Iterator[Operation]:
        """Share a same-user parent scope; another user always gets a distinct scope."""
        active = self._active.get()
        if active is not None and active[0] == user.user_key:
            yield active[1]
            return
        operation = Operation(name, self._settings.llm, observer or self._observer)
        token = self._active.set((user.user_key, operation))
        try:
            yield operation
        except BaseException:
            operation.finish("failed")
            raise
        else:
            operation.finish("succeeded")
        finally:
            self._active.reset(token)

    @contextmanager
    def stage(self, user: UserContext, name: str) -> Iterator[None]:
        operation = self._for_user_operation(user)
        if operation is None:
            yield
        else:
            with operation.stage(name):
                yield

    def _for_user_operation(self, user: UserContext) -> Operation | None:
        active = self._active.get()
        return active[1] if active is not None and active[0] == user.user_key else None

    def for_user(self, user: UserContext) -> TaskRunner:
        return TaskRunner(
            self._client,
            self._repos.for_user(user),
            self._settings,
            self._semaphore,
            lambda: self._for_user_operation(user),
        )
