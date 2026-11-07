"""Synthetic cache and OCR recovery regressions; no provider captures."""

from dataclasses import replace
from pathlib import Path

import pytest

from cvopt.domain.enums import MatchLevel
from cvopt.domain.jd import Requirement
from cvopt.domain.scoring import ScoringParams
from cvopt.errors import LLMTaskError, ValidationFailed
from cvopt.llm.judge import Judge
from cvopt.ports import ExtractedText
from tests.pipeline.test_fact_integrity import pipeline, seed, translation, verified


async def test_later_generation_can_replace_semantically_rejected_translation(
    container, alice, fake_llm
):
    await seed(container, alice)
    p = pipeline(container, alice)
    bad = "Leitete 3 FTE für Alpine und 2 FTE für Birch"
    fake_llm.script("end_turn", translation(bad))
    fake_llm.script("end_turn", verified(False, "T1"))
    for _ in range(container.settings.llm.schema_retries):
        fake_llm.script("end_turn", translation(bad))
    with pytest.raises(ValidationFailed):
        await p.translate(await p.load())
    prior = len(fake_llm.calls)
    fake_llm.script("end_turn", translation("Leitete 2 FTE für Alpine und 3 FTE für Birch"))
    fake_llm.script("end_turn", verified(True, "T1"))
    facts = await p.load()
    await p.translate(facts)
    assert len(fake_llm.calls) == prior + 2
    assert next(iter(facts.claims.values())).text == "Leitete 2 FTE für Alpine und 3 FTE für Birch"


async def test_verifier_outage_keeps_valid_producer_cache(container, alice, fake_llm):
    await seed(container, alice)
    p = pipeline(container, alice)
    fake_llm.script("end_turn", translation("Leitete 2 FTE für Alpine und 3 FTE für Birch"))
    fake_llm.script("refusal")
    with pytest.raises(LLMTaskError):
        await p.translate(await p.load())
    fake_llm.script("end_turn", verified(True, "T1"))
    await p.translate(await p.load())
    assert len(fake_llm.calls) == 3


async def test_pair_cache_tracks_judge_prompt_version(container, alice, fake_llm, monkeypatch):
    import cvopt.llm.judge as module

    req = Requirement(
        id="R01",
        text="Cloud platforms",
        category="hard_skill",
        importance="must",
        keywords=[],
        synonyms=[],
    )
    judge = Judge(container.runners.for_user(alice), ScoringParams(), 10)
    fake_llm.script(
        "end_turn", {"verdicts": [{"ref": "E1", "level": "semantic", "reason": "Synthetic match"}]}
    )
    assert await judge.verdicts(req, ["AWS"]) == {"AWS": MatchLevel.SEMANTIC}
    monkeypatch.setattr(module, "JUDGE_MATCH", replace(module.JUDGE_MATCH, version="changed"))
    fake_llm.script(
        "end_turn", {"verdicts": [{"ref": "E1", "level": "none", "reason": "Synthetic mismatch"}]}
    )
    assert await judge.verdicts(req, ["AWS"]) == {"AWS": MatchLevel.NONE}
    assert len(fake_llm.calls) == 2


async def test_cache_invalidation_is_user_scoped(container, alice, bob, fake_llm):
    from cvopt.llm.tasks.translate import TRANSLATE, TextRef, TranslateInput

    inp = TranslateInput(texts=[TextRef(ref="T1", text="Built systems")], target_language="de")
    fake_llm.script("end_turn", translation("Baute Systeme"))
    fake_llm.script("end_turn", translation("Baute Systeme"))
    a, b = container.runners.for_user(alice), container.runners.for_user(bob)
    await a.run(TRANSLATE, inp)
    await b.run(TRANSLATE, inp)
    await a.invalidate(TRANSLATE, inp)
    await b.run(TRANSLATE, inp)
    fake_llm.script("end_turn", translation("Entwickelte Systeme"))
    assert (await a.run(TRANSLATE, inp)).translations[0].text == "Entwickelte Systeme"
    assert len(fake_llm.calls) == 3


async def test_jd_full_document_transcription_keeps_links_without_duplicate_text(
    container, alice, fake_llm, tmp_path: Path, monkeypatch
):
    path = tmp_path / "mixed.pdf"
    path.write_bytes(b"synthetic PDF bytes")

    async def extract(path):
        return ExtractedText("\nLink targets:\nhttps://example.org", "application/pdf", 2, True)

    monkeypatch.setattr(container.jd._extractor, "extract", extract)
    fake_llm.script("end_turn", {"text": "First page\fScanned second page"})
    text = await container.jd._pdf_text(container.runners.for_user(alice), path)
    assert text == "First page\fScanned second page\n\nLink targets:\nhttps://example.org"
