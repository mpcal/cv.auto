"""Synthetic compatibility adapter for the fixed historical golden scenarios.

This is NOT a provider recording. It reuses historical coarse pair verdicts only for
requirements whose entire judged input occurs in the captured JD scenarios. New
context-verification calls receive synthetic accepting verdicts here; adversarial
integrity tests use FakeLLMClient directly and never install this adapter.
"""

import json
import re
from collections.abc import Callable
from typing import Any

from pydantic import BaseModel

from cvopt.domain.base import Model
from cvopt.domain.enums import MatchLevel
from cvopt.llm.judge import PAIR_TASK
from cvopt.llm.runner import TaskRunner
from cvopt.llm.task import Task, canonical_json
from cvopt.llm.tasks.classify_fidelity import CLASSIFY_FIDELITY, FidelityOutput, FidelityVerdict
from cvopt.llm.tasks.extract_certificate import EXTRACT_CERTIFICATE
from cvopt.llm.tasks.extract_cv import EXTRACT_CV
from cvopt.llm.tasks.extract_reference_letter import EXTRACT_REFERENCE_LETTER
from cvopt.llm.tasks.group_skills import GROUP_SKILLS, GroupOutput, SkillGroup
from cvopt.llm.tasks.judge_match import JudgedRequirement
from cvopt.llm.tasks.translate import TRANSLATE
from cvopt.llm.tasks.verify_facts import VERIFY_FACTS, FactsOutput, FactVerdict
from cvopt.llm.tasks.write_summary import WRITE_SUMMARY, SummaryCandidate, SummaryOutput
from tests import recordings


class HistoricalPair(Model):
    requirement: str
    element: str


class HistoricalTranslation(Model):
    texts: list[dict[str, str]]
    target_language: str


def scenario_requirements() -> set[str]:
    allowed = set()
    for row in recordings.read_all():
        if row["task"] != "parse_jd":
            continue
        for r in json.loads(row["output"])["requirements"]:
            judged = JudgedRequirement(
                **{
                    k: r[k]
                    for k in (
                        "text",
                        "category",
                        "keywords",
                        "synonyms",
                    )
                }
            )
            allowed.add(canonical_json(judged))
            if r.get("degree_fields"):
                allowed.add(
                    canonical_json(
                        judged.model_copy(
                            update={
                                "text": f"Degree field: {' or '.join(r['degree_fields'])}",
                                "keywords": r["degree_fields"],
                                "synonyms": [],
                            }
                        )
                    )
                )
    return allowed


def install_synthetic_golden(runners: Any) -> None:
    original: Callable[..., TaskRunner] = runners.for_user
    allowed = scenario_requirements()
    translations: dict[str, str] = {}
    statement_sources: dict[str, list[str]] = {}
    # New full-profile fixed text has no historical pair captures. Use conservative,
    # explicitly synthetic NONE verdicts only for these exact known fixture records.
    # Positive title/award coverage is exercised independently by profile-service tests.
    fixed_texts: set[str] = set()
    for row in recordings.read_all():
        if row["task"] == "extract_cv":
            data = json.loads(row["output"])
            fixed_texts.update(
                " · ".join(p[k] for k in ("title", "employer") if p.get(k))
                for p in data["positions"]
            )
            fixed_texts.update(
                " · ".join(a[k] for k in ("title", "awarder", "summary") if a.get(k))
                for a in data.get("awards", [])
            )

    def for_user(user: Any) -> Any:
        runner = original(user)

        class SyntheticGoldenRunner:
            async def run(self, task: Task, inp: BaseModel) -> BaseModel:
                if task in (EXTRACT_CV, EXTRACT_CERTIFICATE, EXTRACT_REFERENCE_LETTER):
                    old_version = {
                        "extract_cv": "3",
                        "extract_certificate": "2",
                        "extract_reference_letter": "1",
                    }[task.name]
                    hit = await runner.cached(
                        task.name, old_version, task.tier, inp, task.output_model
                    )
                    assert hit is not None, "No corresponding historical extraction fixture"
                    data = hit.model_dump()
                    # Synthetic quotations of the actual input, NOT provider-captured
                    # excerpt annotations. Whole-document spans deliberately avoid
                    # pretending that historical extraction supplied precise quotes.
                    if task is EXTRACT_CV:
                        for collection in (
                            "positions",
                            "education",
                            "languages",
                            "certificates",
                            "awards",
                            "claims",
                        ):
                            for record in data[collection]:
                                record["excerpt"] = inp.text
                    elif task is EXTRACT_CERTIFICATE:
                        data[data["record_type"]]["excerpt"] = inp.text
                        for claim in data["claims"]:
                            claim["excerpt"] = inp.text
                    else:
                        data["position_excerpt"] = inp.text
                        for passage in data["passages"]:
                            # Old provider spans allowed normalized whitespace. Recover
                            # the actual source span as a synthetic fixture annotation.
                            pattern = r"\s+".join(
                                re.escape(token) for token in passage["excerpt"].split()
                            )
                            match = re.search(pattern, inp.text)
                            assert match is not None, "Historical passage has no actual source span"
                            passage["excerpt"] = match.group()
                            if (
                                passage["claims"]
                                and "Ihre" in match.group()
                                and "diesem Projekt" in match.group()
                            ):
                                # This historical golden passage omitted the adjacent
                                # quantified project context. The actual contiguous
                                # paragraph names three telecom customers (not 18%).
                                context = re.search(
                                    r"Für\s+drei\s+Telekommunikationskunden[\s\S]*?stets\s+zu\s+unserer\s+vollen\s+Zufriedenheit\.",
                                    inp.text,
                                )
                                assert context is not None
                                passage["excerpt"] = context.group()
                    out = task.output_model.model_validate(data)
                    task.check(inp, out)
                    return out
                if task is VERIFY_FACTS:
                    return FactsOutput(
                        verdicts=[
                            FactVerdict(
                                ref=it.ref,
                                facts_preserved=True,
                                reason="Synthetic compatibility acceptance; not provider verified",
                            )
                            for it in inp.items
                        ]
                    )
                if task is TRANSLATE:
                    # Exact old input correspondence: v1 had no feedback field.
                    assert not inp.feedback, "Golden translations must pass on first attempt"
                    old = HistoricalTranslation(**inp.model_dump(exclude={"feedback"}))
                    hit = await runner.cached(task.name, "1", task.tier, old, task.output_model)
                    assert hit is not None, "No corresponding historical translation fixture"
                    task.check(inp, hit)
                    originals = {t.ref: t.text for t in inp.texts}
                    translations.update({originals[t.ref]: t.text for t in hit.translations})
                    return hit
                if task is GROUP_SKILLS:
                    hit = await runner.cached(
                        task.name, task.version, task.tier, inp, task.output_model
                    )
                    if hit is not None:
                        return hit
                    return GroupOutput(groups=[SkillGroup(name="", keywords=inp.keywords)])
                if task is WRITE_SUMMARY:
                    hit = await runner.cached(
                        task.name, task.version, task.tier, inp, task.output_model
                    )
                    if hit is not None:
                        return hit
                    # Selection legitimately changes when stricter fact guards discard a
                    # historical candidate. Create explicitly synthetic source-copy summaries.
                    short = next(it for it in inp.statements if len(it.text.split()) <= 8)
                    sentences = []
                    seen = set()
                    for it in inp.statements:
                        sources = statement_sources.get(it.text, [it.text])
                        text = translations.get(sources[0], sources[0])
                        if len(text) < 45 or text in seen:
                            continue
                        seen.add(text)
                        sentences.append(
                            SummaryCandidate(
                                text=text,
                                source_refs=[it.ref],
                                fidelity="rephrased",
                            )
                        )
                        if len(sentences) == 4:
                            break
                    out = SummaryOutput(
                        headlines=[
                            SummaryCandidate(
                                text=short.text,
                                source_refs=[short.ref],
                                fidelity="rephrased",
                            )
                        ],
                        sentences=sentences,
                    )
                    task.check(inp, out)
                    return out
                if task is CLASSIFY_FIDELITY:
                    statement_sources.update({it.text: it.sources for it in inp.items})
                    # Replay the old classifier's translated-source input exactly.
                    legacy = inp.model_copy(
                        update={
                            "items": [
                                it.model_copy(
                                    update={"sources": [translations.get(s, s) for s in it.sources]}
                                )
                                for it in inp.items
                            ]
                        }
                    )
                    hit = await runner.cached(
                        task.name, task.version, task.tier, legacy, task.output_model
                    )
                    if hit is not None:
                        return hit
                    if all(
                        it.text in [translations.get(s, s) for s in it.sources] for it in inp.items
                    ):
                        return FidelityOutput(
                            verdicts=[
                                FidelityVerdict(
                                    ref=it.ref,
                                    fidelity="unmodified",
                                    reason="Synthetic exact-source-copy classification",
                                )
                                for it in inp.items
                            ]
                        )
                return await runner.run(task, inp)

            async def cached(self, name, version, tier, inp, output_model):
                hit = await runner.cached(name, version, tier, inp, output_model)
                if hit is not None or name != PAIR_TASK:
                    return hit
                assert canonical_json(inp.requirement) in allowed, (
                    "Synthetic pair reuse is restricted to complete captured scenario requirements",
                    inp.requirement,
                )
                legacy = HistoricalPair(requirement=inp.requirement.text, element=inp.element)
                hit = await runner.cached(name, "1", tier, legacy, output_model)
                if hit is None and inp.element in fixed_texts:
                    return output_model(level=MatchLevel.NONE)
                return hit

            async def store(self, *args):
                return await runner.store(*args)

        return SyntheticGoldenRunner()

    runners.for_user = for_user
