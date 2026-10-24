"""TaskRunner: cache, call, validate, retry."""

import asyncio
import logging
import time
from functools import cache
from importlib.resources import files

from pydantic import BaseModel, ValidationError

from cvopt.config import Settings
from cvopt.context import UserContext
from cvopt.errors import LLMTaskError
from cvopt.llm.task import Task, cache_key, canonical_json
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
    ) -> None:
        self._client = client
        self._repo = repo
        self._settings = settings
        self._semaphore = semaphore

    async def run[I: BaseModel, O: BaseModel](self, task: Task[I, O], inp: I) -> O:
        model = self._settings.llm.model_for(task.tier)
        key = cache_key(task.name, task.version, model, canonical_json(inp))
        if (hit := await self._repo.cache_get(key)) is not None:
            log.info("llm cache hit", extra={"task": task.name, "cache": "hit"})
            return task.output_model.model_validate_json(hit)
        max_tokens = task.max_tokens
        feedback: list[TextPart] = []
        for attempt in range(1 + self._settings.llm.schema_retries):
            started = time.monotonic()
            async with self._semaphore:
                res = await self._client.complete(
                    model=model,
                    system=load_prompt(task.name),
                    content=[*task.render(inp), *feedback],
                    output_model=task.output_model,
                    max_tokens=max_tokens,
                )
            log.info(
                "llm call",
                extra={
                    "task": task.name,
                    "cache": "miss",
                    "attempt": attempt,
                    "stop_reason": res.stop_reason.split(":")[0],
                    "duration_s": round(time.monotonic() - started, 2),
                },
            )
            if res.stop_reason == "refusal":
                raise LLMTaskError(f"{task.name}: the model refused")
            if res.stop_reason == "max_tokens":
                max_tokens *= 2
                continue
            try:
                out = task.output_model.model_validate(res.output)
                if task.check:
                    task.check(inp, out)
            except (ValidationError, ValueError) as err:
                feedback = [TextPart(f"Your previous answer was rejected: {err}. Answer again.")]
                continue
            await self._repo.cache_put(key, task.name, task.version, model, out.model_dump_json())
            return out
        raise LLMTaskError(f"{task.name}: no valid output after retries")

    def _key(self, name: str, version: str, tier: str, inp: BaseModel) -> tuple[str, str]:
        model = self._settings.llm.model_for(tier)
        return cache_key(name, version, model, canonical_json(inp)), model

    async def cached[O: BaseModel](
        self, name: str, version: str, tier: str, inp: BaseModel, output_model: type[O]
    ) -> O | None:
        """A finer-grained cache entry than a whole task call (e.g. one judged pair)."""
        key, _ = self._key(name, version, tier, inp)
        hit = await self._repo.cache_get(key)
        return None if hit is None else output_model.model_validate_json(hit)

    async def store(
        self, name: str, version: str, tier: str, inp: BaseModel, out: BaseModel
    ) -> None:
        key, model = self._key(name, version, tier, inp)
        await self._repo.cache_put(key, name, version, model, out.model_dump_json())


class TaskRunnerFactory:
    """Runners are per user, because the LLM cache is."""

    def __init__(self, client: LLMClient, repos: RepositoryFactory, settings: Settings) -> None:
        self._client = client
        self._repos = repos
        self._settings = settings
        self._semaphore = asyncio.Semaphore(settings.llm.max_concurrency)

    def for_user(self, user: UserContext) -> TaskRunner:
        return TaskRunner(self._client, self._repos.for_user(user), self._settings, self._semaphore)
