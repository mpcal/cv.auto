"""TaskRunner: cache, retries with feedback, max_tokens doubling, refusals."""

import pytest

from cvopt.bootstrap import Container
from cvopt.context import UserContext
from cvopt.domain.base import Model
from cvopt.errors import LLMTaskError
from cvopt.llm.task import Task, cache_key, canonical_json
from cvopt.ports import FilePart, TextPart
from tests.fakes import FakeLLMClient, MissingRecording


class EchoIn(Model):
    text: str
    blob: bytes = b""


class EchoOut(Model):
    words: list[str]


def check(inp: EchoIn, out: EchoOut) -> None:
    if not out.words:
        raise ValueError("no words")


ECHO = Task(
    name="transcribe_document",  # reuses an existing prompt file
    version="t1",
    tier="fast",
    input_model=EchoIn,
    output_model=EchoOut,
    max_tokens=100,
    render=lambda i: [TextPart(i.text)],
    check=check,
)


def test_canonical_json_ignores_trivial_differences() -> None:
    a = canonical_json(EchoIn(text="Café  au\n lait "))
    b = canonical_json(EchoIn(text="Café au lait"))
    assert a == b
    assert "sha256" in canonical_json(EchoIn(text="x", blob=b"\x00"))
    assert cache_key("t", "1", "m", a) != cache_key("t", "2", "m", a)


async def test_result_is_cached(
    container: Container, fake_llm: FakeLLMClient, alice: UserContext, bob: UserContext
) -> None:
    fake_llm.script("end_turn", {"words": ["a"]})
    runner = container.runners.for_user(alice)
    assert (await runner.run(ECHO, EchoIn(text="a"))).words == ["a"]
    assert (await runner.run(ECHO, EchoIn(text=" a "))).words == ["a"]  # cache hit
    assert len(fake_llm.calls) == 1
    with pytest.raises(MissingRecording):  # the cache is per user
        await container.runners.for_user(bob).run(ECHO, EchoIn(text="a"))


async def test_invalid_output_retries_with_feedback(
    container: Container, fake_llm: FakeLLMClient, alice: UserContext
) -> None:
    fake_llm.script("end_turn", {"words": []})  # fails check
    fake_llm.script("invalid_output: bad json")  # output None
    fake_llm.script("end_turn", {"words": ["ok"]})
    out = await container.runners.for_user(alice).run(ECHO, EchoIn(text="x"))
    assert out.words == ["ok"]
    feedback = fake_llm.calls[2][1][-1]
    assert isinstance(feedback, TextPart) and "rejected" in feedback.text


async def test_max_tokens_doubles(
    container: Container, fake_llm: FakeLLMClient, alice: UserContext
) -> None:
    fake_llm.script("max_tokens")
    fake_llm.script("end_turn", {"words": ["ok"]})
    await container.runners.for_user(alice).run(ECHO, EchoIn(text="x"))
    assert [c[2] for c in fake_llm.calls] == [100, 200]


async def test_refusal_and_exhausted_retries(
    container: Container, fake_llm: FakeLLMClient, alice: UserContext
) -> None:
    runner = container.runners.for_user(alice)
    fake_llm.script("refusal")
    with pytest.raises(LLMTaskError, match="refused"):
        await runner.run(ECHO, EchoIn(text="x"))
    for _ in range(3):
        fake_llm.script("end_turn", {"words": []})
    with pytest.raises(LLMTaskError, match="retries"):
        await runner.run(ECHO, EchoIn(text="y"))


def test_file_part_is_hashable_data() -> None:
    assert FilePart("application/pdf", b"%PDF") == FilePart("application/pdf", b"%PDF")


async def test_operation_aggregates_all_runners_retries_and_caches_per_user(
    container, alice, bob, monkeypatch
):
    from cvopt.observability import TokenUsage
    from cvopt.ports import LLMResult

    responses = iter(
        [
            ("invalid_output: PRIVATE", None),
            ("max_tokens", None),
            ("end_turn", EchoOut(words=["ok"])),
            ("refusal", None),
        ]
    )

    async def complete(**kwargs):
        stop, output = next(responses)
        return LLMResult(
            output=output,
            stop_reason=stop,
            model="observed-model",
            usage=TokenUsage(
                input_tokens=10, output_tokens=2, cache_read_tokens=3, cache_write_tokens=1
            ),
            retries_taken=0,
        )

    monkeypatch.setattr(container.runners._client, "complete", complete)
    events = []
    with container.runners.operation(alice, "generation", observer=events.append) as op:
        await container.runners.for_user(alice).run(ECHO, EchoIn(text="x"))
        await container.runners.for_user(alice).run(ECHO, EchoIn(text="x"))
        # Bob's call is deliberately inside Alice's async context, but cannot enter her totals.
        with pytest.raises(LLMTaskError):
            await container.runners.for_user(bob).run(ECHO, EchoIn(text="x"))
    summary = op.summary()
    assert summary["status"] == "succeeded"
    assert summary["counters"]["llm_calls"] == 3
    assert summary["counters"]["schema_retries"] == 2
    assert summary["counters"]["application_cache_hits"] == 1
    assert summary["models"]["observed-model"]["known_tokens"]["input"] == 30
    assert summary["token_usage_complete"]
    assert "PRIVATE" not in repr(summary) + repr(events)
    with container.runners.operation(alice, "generation") as next_op:
        await container.runners.for_user(alice).run(ECHO, EchoIn(text="x"))
    assert next_op.summary()["total_usd"] == 0
    assert next_op.summary()["counters"]["llm_calls"] == 0


async def test_failed_operation_keeps_unknown_call_and_error_stage(container, alice):
    with (
        pytest.raises(MissingRecording),
        container.runners.operation(alice, "generation") as op,
        op.stage("translate"),
    ):
        await container.runners.for_user(alice).run(ECHO, EchoIn(text="PRIVATE"))
    summary = op.summary()
    assert summary["status"] == "failed"
    assert summary["counters"]["llm_calls"] == 1
    assert not summary["token_usage_complete"]
    assert "PRIVATE" not in repr(summary)


async def test_unexpected_validation_failure_still_counts_observed_response(
    container, alice, monkeypatch
):
    from dataclasses import replace

    from cvopt.observability import TokenUsage
    from cvopt.ports import LLMResult

    def broken_check(inp, out):
        raise RuntimeError("PRIVATE validation context")

    async def complete(**kwargs):
        return LLMResult(
            output=EchoOut(words=["ok"]),
            stop_reason="end_turn",
            model="m",
            usage=TokenUsage(
                input_tokens=11, output_tokens=2, cache_read_tokens=0, cache_write_tokens=0
            ),
            retries_taken=0,
        )

    monkeypatch.setattr(container.runners._client, "complete", complete)
    with pytest.raises(RuntimeError), container.runners.operation(alice, "generation") as op:
        await container.runners.for_user(alice).run(
            replace(ECHO, check=broken_check), EchoIn(text="x")
        )
    summary = op.summary()
    assert summary["counters"]["llm_calls"] == 1
    assert summary["models"]["m"]["known_tokens"]["input"] == 11
    assert summary["tasks"][ECHO.name]["error"] == 1
    assert "PRIVATE" not in repr(summary)
