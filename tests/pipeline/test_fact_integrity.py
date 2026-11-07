"""Synthetic adversarial tests; scripted outputs are not provider captures."""

import json
from datetime import date

import pytest

from cvopt.bootstrap import Container
from cvopt.context import UserContext
from cvopt.domain.candidates import Annotation
from cvopt.domain.cv import JSONResume, Placement, parse_elements
from cvopt.domain.enums import Fidelity, Slot
from cvopt.domain.jd import JobDescription, Requirement
from cvopt.domain.optimizer import Selection
from cvopt.domain.rules import Vocabulary
from cvopt.domain.scoring import ScoringParams
from cvopt.errors import ValidationFailed
from cvopt.llm.judge import Judge
from cvopt.services.generation import _Pipeline
from tests.fakes import FakeLLMClient


def pipeline(container: Container, alice: UserContext, language: str = "de") -> _Pipeline:
    jd = JobDescription(
        title="Engineer", language=language, requirements=[], company="Synthetic", logistics=[]
    )
    return _Pipeline(container.generation, alice, jd, date(2026, 6, 30))


async def seed(container: Container, alice: UserContext):
    pos = await container.data.create_position(
        alice,
        {
            "title": "Engineering Lead",
            "employer": "Acme",
            "start_month": "2020-01",
            "end_month": None,
        },
    )
    result = await container.data.create_claim(
        alice,
        text="Managed 2 FTE for Alpine and 3 FTE for Birch",
        text_lang="en",
        type="achievement",
        position=pos.id,
    )
    c = result.claim
    return pos, c


def translation(text: str):
    return {"translations": [{"ref": "T1", "text": text}]}


def verified(ok: bool, ref: str = "X1"):
    return {"verdicts": [{"ref": ref, "facts_preserved": ok, "reason": "Synthetic verdict"}]}


async def test_translation_retains_original_claim_objects(
    container: Container,
    alice: UserContext,
    fake_llm: FakeLLMClient,
) -> None:
    _, original = await seed(container, alice)
    p = pipeline(container, alice)
    facts = await p.load()
    fake_llm.script("end_turn", translation("Leitete 2 FTE für Alpine und 3 FTE für Birch"))
    fake_llm.script("end_turn", verified(True, "T1"))
    await p.translate(facts)
    assert facts.original_claims[original.id] == original
    assert facts.claims[original.id].text.startswith("Leitete")
    assert facts.original_text[original.id] == original.text


@pytest.mark.parametrize(
    "bad",
    [
        "Leitete 200 FTE für Alpine und 3 FTE für Birch",
        "Leitete 2 FTE für Cedar und 3 FTE für Birch",
        "Leitete 3 FTE für Alpine und 2 FTE für Birch",
        "Leitete 2 Monate für Alpine und 3 FTE für Birch",
    ],
)
async def test_persistent_translation_fact_failure_aborts(
    container: Container,
    alice: UserContext,
    fake_llm: FakeLLMClient,
    bad: str,
) -> None:
    await seed(container, alice)
    p = pipeline(container, alice)
    facts = await p.load()
    for attempt in range(1 + container.settings.llm.schema_retries):
        fake_llm.script("end_turn", translation(bad))
        if "200" not in bad and attempt == 0:
            fake_llm.script("end_turn", verified(False, "T1"))
    with pytest.raises(ValidationFailed, match="[Tt]ranslat"):
        await p.translate(facts)
    assert facts.claims == facts.original_claims


async def test_translation_retries_after_context_failure(
    container: Container,
    alice: UserContext,
    fake_llm: FakeLLMClient,
) -> None:
    await seed(container, alice)
    p = pipeline(container, alice)
    facts = await p.load()
    fake_llm.script("end_turn", translation("Leitete 3 FTE für Alpine und 2 FTE für Birch"))
    fake_llm.script("end_turn", verified(False, "T1"))
    fake_llm.script("end_turn", translation("Leitete 2 FTE für Alpine und 3 FTE für Birch"))
    fake_llm.script("end_turn", verified(True, "T1"))
    await p.translate(facts)
    assert next(iter(facts.claims.values())).text == "Leitete 2 FTE für Alpine und 3 FTE für Birch"


@pytest.mark.parametrize(
    "intended", [Fidelity.UNMODIFIED, Fidelity.REPHRASED, Fidelity.EXAGGERATED]
)
async def test_sourced_candidates_use_original_context_even_when_unmodified(
    container: Container,
    alice: UserContext,
    fake_llm: FakeLLMClient,
    intended: Fidelity,
) -> None:
    pos, c = await seed(container, alice)
    p = pipeline(container, alice)
    facts = await p.load()
    # Simulate a compromised display path; original facts still govern validation.
    facts.claims[c.id] = c.model_copy(
        update={"text": "Managed 3 FTE for Alpine and 2 FTE for Birch"}
    )
    cand = p.candidate(facts, Slot.HIGHLIGHT, facts.claims[c.id].text, [c.id], intended, pos.id)
    if intended is not Fidelity.UNMODIFIED:
        fake_llm.script(
            "end_turn",
            {
                "verdicts": [
                    {
                        "ref": "X1",
                        "fidelity": intended.value,
                        "reason": "Synthetic classification",
                    }
                ]
            },
        )
    fake_llm.script("end_turn", verified(False))
    await p.verify(facts, [cand], Vocabulary([]))
    assert cand.id in p.pool.discarded
    checked = fake_llm.calls[-1][1][0].text
    payload = json.loads(checked.split("\n", 1)[1].rsplit("\n", 1)[0])
    assert payload["items"][0]["sources"] == ["Managed 2 FTE for Alpine and 3 FTE for Birch"]
    assert payload["items"][0]["text"] == "Managed 3 FTE for Alpine and 2 FTE for Birch"


async def test_position_fallback_has_record_provenance_and_validates(
    container: Container,
    alice: UserContext,
    fake_llm: FakeLLMClient,
) -> None:
    pos, c = await seed(container, alice)
    p = pipeline(container, alice, "en")
    facts = await p.load()
    p.relevance[c.id] = {"r": 1.0}
    chosen = p.candidate(facts, Slot.HIGHLIGHT, c.text, [c.id], Fidelity.UNMODIFIED, pos.id)
    fake_llm.script(
        "end_turn",
        {
            "headlines": [
                {
                    "text": "Managed teams for Cedar",
                    "source_refs": ["S1"],
                    "fidelity": "rephrased",
                }
            ],
            "sentences": [],
        },
    )
    fake_llm.script(
        "end_turn",
        {
            "verdicts": [
                {
                    "ref": "X1",
                    "fidelity": "invented",
                    "reason": "New client",
                }
            ]
        },
    )
    options = await p.summary_candidates(facts, Selection(chosen=[chosen]), Vocabulary([]))
    head = next(c for c in options if c.slot is Slot.HEADLINE)
    assert head.source_claim_ids == []
    assert head.source_record_refs == [f"position:{pos.id}"]
    cv, placed = await p.assemble(facts, [head], {})
    assert cv["basics"]["label"] == "Engineering Lead"
    assert placed["/basics/label", None].source_record_refs == [f"position:{pos.id}"]


async def test_degree_override_survives_generation_and_expires_on_edit(
    container: Container,
    alice: UserContext,
) -> None:
    await container.data.create_education(
        alice,
        {
            "institution": "Academy",
            "degree_type": "Special Diploma",
            "degree_level": "master",
        },
    )
    p = pipeline(container, alice)
    facts = await p.load()
    cv, _ = await p.assemble(facts, [], {})
    entry = parse_elements(JSONResume.model_validate(cv), p.as_of, "de", Placement())[0]
    assert entry.meta["degree_level"] == "master"
    jd = p.jd.model_copy(
        update={
            "requirements": [
                Requirement(
                    id="E1",
                    text="Master degree",
                    category="education",
                    importance="must",
                    keywords=[],
                    synonyms=[],
                    degree_level="master",
                )
            ]
        }
    )
    generated = await container.scoring.score_cv(alice, JSONResume.model_validate(cv), jd, p.as_of)
    assert generated.result.total == 100.0
    cv["education"][0]["area"] = "Edited field"
    edited = parse_elements(JSONResume.model_validate(cv), p.as_of, "de", Placement())[0]
    assert edited.meta["degree_level"] == "other"
    rescored = await container.scoring.score_cv(alice, JSONResume.model_validate(cv), jd, p.as_of)
    assert rescored.result.total == 0.0
    cv.pop("meta")
    external = parse_elements(JSONResume.model_validate(cv), p.as_of, "de", Placement())[0]
    assert external.meta["degree_level"] == "other"


@pytest.mark.parametrize(
    "changed",
    [
        {"category": "domain"},
        {"keywords": ["Snake"]},
        {"synonyms": ["Serpent"]},
    ],
)
async def test_pair_cache_invalidates_complete_judged_requirement(
    container: Container,
    alice: UserContext,
    fake_llm: FakeLLMClient,
    changed: dict,
) -> None:
    r = Requirement(
        id="R1",
        text="Python",
        category="hard_skill",
        importance="must",
        keywords=["Python"],
        synonyms=["Py"],
    )
    j = Judge(container.runners.for_user(alice), ScoringParams(), 10)
    fake_llm.script(
        "end_turn", {"verdicts": [{"ref": "E1", "level": "semantic", "reason": "Synthetic"}]}
    )
    assert (await j.verdicts(r, ["Python experience"]))["Python experience"].value == "semantic"
    fake_llm.script(
        "end_turn", {"verdicts": [{"ref": "E1", "level": "none", "reason": "Synthetic"}]}
    )
    assert (
        await j.verdicts(
            Requirement.model_validate({**r.model_dump(), **changed}), ["Python experience"]
        )
    )["Python experience"].value == "none"


def test_legacy_annotations_read_without_record_provenance() -> None:
    a = Annotation(
        pointer="/basics/label",
        text="Engineer",
        fidelity="unmodified",
        provability="medium",
        source_claim_ids=["c"],
        inferred_from=[],
        requirement_ids=[],
        impact=0,
        row=1,
    )
    assert a.source_record_refs == []


async def test_translation_preserves_provider_refusal_error(
    container: Container,
    alice: UserContext,
    fake_llm: FakeLLMClient,
) -> None:
    from cvopt.errors import LLMTaskError

    await seed(container, alice)
    p = pipeline(container, alice)
    facts = await p.load()
    fake_llm.script("refusal")
    with pytest.raises(LLMTaskError, match="refused"):
        await p.translate(facts)


async def test_summary_fallback_cannot_republish_corrupt_display_translation(
    container: Container,
    alice: UserContext,
    fake_llm: FakeLLMClient,
) -> None:
    pos, c = await seed(container, alice)
    p = pipeline(container, alice)
    facts = await p.load()
    facts.claims[c.id] = c.model_copy(
        update={
            "text": "Managed 3 FTE for Alpine and 2 FTE for Birch",
            "text_lang": "de",
        }
    )
    p.relevance[c.id] = {"r": 1.0}
    chosen = p.candidate(facts, Slot.HIGHLIGHT, c.text, [c.id], Fidelity.UNMODIFIED, pos.id)
    fake_llm.script(
        "end_turn",
        {
            "headlines": [
                {
                    "text": "Managed teams for Cedar",
                    "source_refs": ["S1"],
                    "fidelity": "rephrased",
                }
            ],
            "sentences": [],
        },
    )
    fake_llm.script(
        "end_turn",
        {
            "verdicts": [
                {
                    "ref": "X1",
                    "fidelity": "invented",
                    "reason": "Synthetic: new client",
                }
            ]
        },
    )
    fake_llm.script("end_turn", verified(False))
    options = await p.summary_candidates(facts, Selection(chosen=[chosen]), Vocabulary([]))
    assert all(c.slot is not Slot.SUMMARY for c in options)
    rejected = [c for c in p.pool.cands.values() if c.slot is Slot.SUMMARY]
    assert rejected and all(c.id in p.pool.discarded for c in rejected)


async def test_position_record_provenance_reaches_final_validator(
    container: Container,
    alice: UserContext,
) -> None:
    from cvopt.domain.candidates import Candidate
    from cvopt.domain.validator import PlacedStatement, validate_statements

    pos, _ = await seed(container, alice)
    p = pipeline(container, alice, "en")
    facts = await p.load()
    head = Candidate(
        id="h",
        slot="headline",
        position_id=None,
        text=pos.title,
        source_claim_ids=[],
        source_record_refs=[f"position:{pos.id}"],
        fidelity="unmodified",
        provability="medium",
        lines=1,
    )
    placed = PlacedStatement(
        pointer="/basics/label",
        sentence=None,
        text=head.text,
        candidate=head,
        sources=[],
        source_records=p.record_sources(facts, head),
        inferable=False,
        gain=0,
        alternative_gain=None,
    )
    annotation = Annotation(
        pointer=placed.pointer,
        text=head.text,
        fidelity=head.fidelity,
        provability=head.provability,
        source_claim_ids=[],
        source_record_refs=head.source_record_refs,
        inferred_from=[],
        requirement_ids=[],
        impact=0,
        row=1,
    )
    assert validate_statements([placed], [annotation], Vocabulary([]), 1.0) == []
    corrupted = annotation.model_copy(update={"source_record_refs": []})
    assert any(
        v.rule == "annotation"
        for v in validate_statements(
            [placed],
            [corrupted],
            Vocabulary([]),
            1.0,
        )
    )


@pytest.mark.parametrize("text", ["Zwei Standorte modernisiert", "Twenty Offices managed"])
async def test_invented_capitalized_counts_cannot_reach_final_validation(
    container: Container,
    alice: UserContext,
    fake_llm: FakeLLMClient,
    text: str,
) -> None:
    from cvopt.domain.enums import Provability
    from cvopt.domain.validator import PlacedStatement, validate_statements

    pos, _ = await seed(container, alice)
    p = pipeline(container, alice)
    facts = await p.load()
    candidate = p.candidate(facts, Slot.HIGHLIGHT, text, [], Fidelity.INVENTED, pos.id)
    fake_llm.script("end_turn", verified(False))
    await p.verify(facts, [candidate], Vocabulary([]))
    assert candidate.id in p.pool.discarded
    # The final deterministic guard must also reject a bypassed verification path.
    candidate = candidate.model_copy(update={"provability": Provability.NONE})
    placed = PlacedStatement(
        pointer="/work/0/highlights/0",
        sentence=None,
        text=text,
        candidate=candidate,
        sources=[],
        inferable=False,
        gain=0,
        alternative_gain=None,
    )
    annotation = Annotation(
        pointer=placed.pointer,
        text=text,
        fidelity="invented",
        provability="none",
        source_claim_ids=[],
        inferred_from=[],
        requirement_ids=[],
        impact=0,
        row=1,
    )
    assert any(
        v.rule == "hard_facts"
        for v in validate_statements(
            [placed],
            [annotation],
            Vocabulary([]),
            1.0,
        )
    )


async def test_verified_article_translation_survives_unmodified_and_summary_fallback(
    container: Container,
    alice: UserContext,
    fake_llm: FakeLLMClient,
) -> None:
    from cvopt.domain.enums import Provability
    from cvopt.domain.validator import PlacedStatement, validate_statements

    pos, _ = await seed(container, alice)
    stored = await container.data.create_claim(
        alice, type="achievement", text="Leitete ein Team", text_lang="de", position=pos.id
    )
    p = pipeline(container, alice, "en")
    facts = await p.load()
    fake_llm.script("end_turn", translation("Led one team"))
    fake_llm.script("end_turn", verified(True, "T1"))
    await p.translate(facts)
    fake_llm.script("end_turn", verified(True))
    for slot in [Slot.HIGHLIGHT, Slot.SUMMARY]:
        candidate = p.candidate(
            facts,
            slot,
            facts.claims[stored.claim.id].text,
            [stored.claim.id],
            Fidelity.UNMODIFIED,
            pos.id if slot is Slot.HIGHLIGHT else None,
        )
        await p.verify(facts, [candidate], Vocabulary([]))
        assert candidate.id not in p.pool.discarded
        candidate = p.pool.cands[candidate.id]
        assert candidate.provability is Provability.MEDIUM
        pointer, sentence = (
            ("/work/0/highlights/0", None)
            if slot is Slot.HIGHLIGHT
            else (
                "/basics/summary",
                0,
            )
        )
        placed = PlacedStatement(
            pointer=pointer,
            sentence=sentence,
            text=candidate.text,
            candidate=candidate,
            sources=[stored.claim],
            inferable=False,
            gain=0,
            alternative_gain=None,
        )
        annotation = Annotation(
            pointer=pointer,
            sentence=sentence,
            text=candidate.text,
            fidelity="unmodified",
            provability="medium",
            source_claim_ids=[stored.claim.id],
            inferred_from=[],
            requirement_ids=[],
            impact=0,
            row=1,
        )
        assert validate_statements([placed], [annotation], Vocabulary([]), 1.0) == []


@pytest.mark.parametrize("proof", [False, True])
async def test_invented_spelled_numeric_name_needs_explicit_verified_span(
    container: Container,
    alice: UserContext,
    fake_llm: FakeLLMClient,
    proof: bool,
) -> None:
    from cvopt.domain.validator import PlacedStatement, validate_statements

    pos, _ = await seed(container, alice)
    stored = await container.data.create_claim(
        alice, type="achievement", text="Advised Seven Seas", text_lang="en", position=pos.id
    )
    p = pipeline(container, alice, "en")
    facts = await p.load()
    candidate = p.candidate(
        facts, Slot.HIGHLIGHT, "Advised Seven Seas on strategy", [], Fidelity.INVENTED, pos.id
    )
    verdict = verified(True)
    if proof:
        verdict["verdicts"][0]["numeric_name_spans"] = ["Seven Seas"]
    fake_llm.script("end_turn", verdict)
    await p.verify(facts, [candidate], Vocabulary([]))
    assert (candidate.id not in p.pool.discarded) is proof
    if proof:
        candidate = p.pool.cands[candidate.id]
        assert candidate.numeric_name_spans == ["Seven Seas"]
        body = fake_llm.calls[-1][1][0].text
        item = json.loads(body.split("\n", 1)[1].rsplit("\n", 1)[0])["items"][0]
        assert item["mode"] == "invented"
        assert stored.claim.text in item["sources"]
        assert any("Acme" in source for source in item["sources"])
        placed = PlacedStatement(
            pointer="/work/0/highlights/0",
            sentence=None,
            text=candidate.text,
            candidate=candidate,
            sources=[],
            inferable=False,
            gain=0,
            alternative_gain=None,
        )
        annotation = Annotation(
            pointer=placed.pointer,
            text=candidate.text,
            fidelity="invented",
            provability="none",
            source_claim_ids=[],
            inferred_from=[],
            requirement_ids=[],
            impact=0,
            row=1,
        )
        assert validate_statements([placed], [annotation], Vocabulary([]), 1.0) == []


async def test_verified_name_does_not_authorize_an_invented_count(
    container: Container,
    alice: UserContext,
    fake_llm: FakeLLMClient,
) -> None:
    pos, _ = await seed(container, alice)
    p = pipeline(container, alice, "en")
    facts = await p.load()
    candidate = p.candidate(
        facts, Slot.HIGHLIGHT, "Advised Seven Seas with two teams", [], Fidelity.INVENTED, pos.id
    )
    fake_llm.script(
        "end_turn",
        {
            "verdicts": [
                {
                    "ref": "X1",
                    "facts_preserved": True,
                    "reason": "Synthetic name only proof",
                    "numeric_name_spans": ["Seven Seas"],
                }
            ]
        },
    )
    await p.verify(facts, [candidate], Vocabulary([]))
    assert candidate.id in p.pool.discarded


@pytest.mark.parametrize(
    ("text", "allowed"),
    [
        ("Used OneDrive", True),
        ("Used Windows 11", False),
    ],
)
async def test_invention_preserves_tool_names_and_raw_digit_prohibition(
    container: Container,
    alice: UserContext,
    text: str,
    allowed: bool,
) -> None:
    pos, _ = await seed(container, alice)
    p = pipeline(container, alice, "en")
    facts = await p.load()
    candidate = p.candidate(facts, Slot.HIGHLIGHT, text, [], Fidelity.INVENTED, pos.id)
    await p.verify(facts, [candidate], Vocabulary([]))
    assert (candidate.id not in p.pool.discarded) is allowed
