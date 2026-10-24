"""Test doubles for the LLM client."""

from collections import deque

from pydantic import BaseModel

from cvopt.ports import FilePart, LLMResult, TextPart


class MissingRecording(AssertionError):
    """A test reached the model: record it with `CVOPT_RECORD=1 pytest -m live`."""


def summarize(system: str, content: list[TextPart | FilePart]) -> str:
    task = next((ln for ln in system.splitlines() if ln.startswith("Task:")), "Task: ?")
    parts = [
        p.text[:60] if isinstance(p, TextPart) else f"<{p.media_type}, {len(p.data)} bytes>"
        for p in content
    ]
    return f"{task[:70]} | input: {'; '.join(parts)}"


class FakeLLMClient:
    """Fails on any call, unless responses were scripted with `script()`."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, list[TextPart | FilePart], int]] = []
        self._script: deque[tuple[str, object]] = deque()

    def script(self, stop_reason: str, output: object = None) -> None:
        self._script.append((stop_reason, output))

    async def complete[T: BaseModel](
        self,
        *,
        model: str,
        system: str,
        content: list[TextPart | FilePart],
        output_model: type[T],
        max_tokens: int,
    ) -> LLMResult[T]:
        self.calls.append((model, content, max_tokens))
        if not self._script:
            raise MissingRecording(summarize(system, content))
        stop, output = self._script.popleft()
        parsed = output_model.model_validate(output) if isinstance(output, dict) else output
        return LLMResult(output=parsed, stop_reason=stop, model=model)  # type: ignore[arg-type]
