"""Synthetic accounting integration; provider outputs are scripted test data."""

import json

import pytest

from cvopt.domain.cv import JSONResume
from cvopt.domain.enums import ClaimType
from cvopt.errors import LLMTaskError
from cvopt.observability import TokenUsage
from cvopt.services.generation import _Pipeline
from cvopt.services.publication import RunPublication
from tests.pipeline.test_publication import populate
from tests.unit.test_runner import ECHO, EchoIn


def jd_output():
    return dict(
        is_job_description=True,
        title="Engineer",
        company="Example",
        language="en",
        requirements=[
            dict(
                text="Python",
                category="hard_skill",
                importance="must",
                keywords=["Python"],
                synonyms=[],
                years=0,
                language_code="",
                language_level="",
                degree_level="",
                degree_fields=[],
                certificate_names=[],
            )
        ],
        logistics=[],
    )


async def test_intake_generation_and_failed_regeneration_keep_distinct_usage(
    container, fake_llm, alice, tmp_path, monkeypatch
):
    from dataclasses import replace

    original_complete = fake_llm.complete

    async def complete(**kwargs):
        result = await original_complete(**kwargs)
        return replace(
            result,
            usage=TokenUsage(
                input_tokens=10, output_tokens=2, cache_read_tokens=0, cache_write_tokens=0
            ),
            retries_taken=0,
        )

    monkeypatch.setattr(fake_llm, "complete", complete)
    fake_llm.script("end_turn", jd_output())
    text = tmp_path / "jd.txt"
    text.write_text("Synthetic job description")
    run = await container.jd.create_run(alice, text=text)
    intake = json.loads((run.path / "intake-usage.json").read_text())
    assert intake["operation"] == "jd_intake"
    assert intake["counters"]["llm_calls"] == 1
    assert intake["token_usage_complete"]
    await container.data.create_claim(alice, type=ClaimType.SKILL, text="Python", text_lang="en")

    async def pipeline_run(self):
        await self.runner.run(ECHO, EchoIn(text="pipeline"))
        # These real services create their own runners; they must share the operation.
        await self.profile_service.coverage(self.user, self.jd, self.as_of)
        target = await self.svc._scoring.score_cv(
            self.user,
            JSONResume.model_validate({"basics": {"label": "Python target"}}),
            self.jd,
            self.as_of,
        )
        self.final_score = target.result

    def write(self, path, info):
        from types import SimpleNamespace

        populate(SimpleNamespace(path=path))
        (path / "run.json").write_text(info.model_dump_json())
        return list(path.iterdir())

    monkeypatch.setattr(_Pipeline, "run", pipeline_run)
    monkeypatch.setattr(_Pipeline, "write", write)
    fake_llm.script("end_turn", {"words": ["pipeline"]})
    verdict = {"verdicts": [{"ref": "E1", "level": "semantic", "reason": "synthetic"}]}
    fake_llm.script("end_turn", verdict)
    fake_llm.script("end_turn", verdict)
    await container.generation.generate(alice, run.name)
    first = json.loads((run.path / "usage.json").read_text())
    assert first["operation"]["counters"]["llm_calls"] == 3
    assert first["jd_intake"] == intake
    assert not first["jd_intake_billed_in_operation"]
    assert first["operation"]["token_usage_complete"]
    assert first["operation"]["total_usd"] is None  # no guessed prices
    await container.generation.generate(alice, run.name)
    cached = json.loads((run.path / "usage.json").read_text())
    assert cached["operation"]["total_usd"] == 0
    assert cached["operation"]["counters"]["llm_calls"] == 0
    assert cached["operation"]["counters"]["application_cache_hits"] >= 3
    assert cached["jd_intake"]["operation_id"] == intake["operation_id"]
    selected = (run.path / "current-generation").resolve()

    async def fail(self):
        await self.runner.run(ECHO, EchoIn(text="new-attempt"))

    monkeypatch.setattr(_Pipeline, "run", fail)
    fake_llm.script("refusal")
    with pytest.raises(LLMTaskError):
        await container.generation.generate(alice, run.name)
    attempt = json.loads((run.path / "last-attempt.json").read_text())
    assert attempt["usage"]["counters"]["llm_calls"] == 1
    assert attempt["usage"]["status"] == "failed"
    assert attempt["usage"]["tasks"][ECHO.name]["refused"] == 1
    assert (run.path / "current-generation").resolve() == selected
    assert json.loads((run.path / "usage.json").read_text()) == cached


def test_publication_attaches_safe_usage_even_when_failure_precedes_publish(tmp_path):
    run = tmp_path / "run"
    run.mkdir()
    (run / "run.json").write_text("{}")
    from cvopt.config import LLMSettings
    from cvopt.observability import Operation

    operation = Operation("generation", LLMSettings())
    with pytest.raises(LLMTaskError), RunPublication(run).attempt(usage=operation.summary):
        operation.call("translate", "m", "refused", None, retries_taken=None)
        raise LLMTaskError("PRIVATE source text")
    status = json.loads((run / "last-attempt.json").read_text())
    assert status["usage"]["status"] == "failed"
    assert status["usage"]["total_usd"] is None
    assert "PRIVATE" not in json.dumps(status)
