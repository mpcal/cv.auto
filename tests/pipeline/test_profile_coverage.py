"""Synthetic profile coverage and provenance against real isolated repositories."""

import importlib
from datetime import date

import pytest

from cvopt.domain.enums import ClaimType, DocumentKind, EvidenceKind, MatchLevel, RecordType
from cvopt.domain.jd import JobDescription, Requirement
from cvopt.llm.judge import Judge
from tests.conftest import TEST_NOW

AS_OF = date(2026, 6, 30)


def service(c):
    try:
        module = importlib.import_module("cvopt.services.profile")
    except ModuleNotFoundError:
        pytest.fail("Reusable profile service missing")
    return module.ProfileCoverageService(c.repos, c.runners, c.settings)


def job(category="hard_skill", **kw):
    return JobDescription(
        title="Engineer",
        company="Example",
        language="en",
        logistics=[],
        requirements=[
            Requirement(
                id="R1",
                text="Python",
                category=category,
                importance="must",
                keywords=["Python"],
                synonyms=[],
                **kw,
            )
        ],
    )


async def semantic(self, requirement, texts):
    return {t: MatchLevel.SEMANTIC if "Python" in t else MatchLevel.NONE for t in texts}


async def test_coverage_snapshot_keeps_stale_sources_and_fingerprint_materiality(
    container, alice, bob, monkeypatch
):
    monkeypatch.setattr(Judge, "verdicts", semantic)
    repo = container.repos.for_user(alice)
    claim = (
        await container.data.create_claim(
            alice, type=ClaimType.SKILL, text="Python", text_lang="en"
        )
    ).claim
    doc = await repo.insert_document(
        dict(
            kind=DocumentKind.CV,
            filename="original.pdf",
            sha256="a" * 64,
            mime_type="application/pdf",
            extracted_text="Python",
            received_at=TEST_NOW,
        )
    )
    await container.data.add_evidence(alice, claim.id, kind=EvidenceKind.OWN_CV, document=doc.id)
    svc = service(container)
    assert hasattr(svc, "coverage"), "Full-profile coverage API missing"
    before = await svc.coverage(alice, job(), AS_OF)
    assert before.result.total == 100
    assert before.snapshot.fingerprint
    assert before.snapshot.assertions[0].sources[-1].excerpt == ""
    assert any(
        s.document_name == "original.pdf" and s.state == "active"
        for a in before.snapshot.assertions
        for s in a.sources
    )
    await container.data.update_claim(alice, claim.id, {"keywords": ["new hint"]})
    hinted = await svc.coverage(alice, job(), AS_OF)
    assert before.snapshot.fingerprint == hinted.snapshot.fingerprint
    await container.data.update_claim(alice, claim.id, {"text": "Python delivery"})
    edited = await svc.coverage(alice, job(), AS_OF)
    assert edited.snapshot.fingerprint != before.snapshot.fingerprint
    assert any(
        s.state == "stale" and s.document_name == "original.pdf"
        for a in edited.snapshot.assertions
        for s in a.sources
    )
    assert not (await svc.coverage(bob, job(), AS_OF)).snapshot.claims


async def test_unconfirmed_and_stale_matches_explain_but_never_score(container, alice, monkeypatch):
    monkeypatch.setattr(Judge, "verdicts", semantic)
    data = container.data
    held = (
        await data.create_claim(
            alice, type=ClaimType.EXPERIENCE, text="Confirmed delivery", text_lang="en"
        )
    ).claim
    proposed = await data.create_claim(alice, type=ClaimType.SKILL, text="Python", text_lang="de")
    await data.remove_evidence(alice, proposed.evidence[0].id)
    svc = service(container)
    assert hasattr(svc, "coverage"), "Full-profile coverage API missing"
    result = await svc.coverage(alice, job(), AS_OF)
    assert result.result.total == 0
    assert {e.id for e in result.elements} == {f"claim:{held.id}"}
    assert any(v > 0 for v in result.context_values.values())
    assert any(a.original_text == "Python" and not a.eligible for a in result.snapshot.assertions)


async def test_profile_years_union_excludes_untied_and_low(container, alice, monkeypatch):
    monkeypatch.setattr(Judge, "verdicts", semantic)
    repo = container.repos.for_user(alice)
    for start, end in [("2020-01", "2021-12"), ("2021-01", "2022-12")]:
        p = await repo.insert_position(
            dict(employer="Example", title="Engineer", start_month=start, end_month=end)
        )
        await container.data.create_claim(
            alice, type=ClaimType.EXPERIENCE, text="Python delivery", text_lang="en", position=p.id
        )
    await container.data.create_claim(
        alice, type=ClaimType.EXPERIENCE, text="Python untied", text_lang="en"
    )
    await container.data.create_claim(
        alice, type=ClaimType.SKILL, text="Python learnable", text_lang="en", learnable=True
    )
    svc = service(container)
    assert hasattr(svc, "coverage"), "Full-profile coverage API missing"
    result = await svc.coverage(alice, job("years_experience", years=4), AS_OF)
    assert result.result.total == 75  # 36 unique months / 48, not 48/48
    assert len(result.result.requirements[0].winners) == 2


async def test_fixed_record_snapshot_retains_position_active_and_stale_sources(
    container, alice, monkeypatch
):
    monkeypatch.setattr(Judge, "verdicts", semantic)
    repo = container.repos.for_user(alice)
    p = await repo.insert_position(
        dict(employer="Example", title="Engineer", start_month="2020-01", end_month=None)
    )
    doc = await repo.insert_document(
        dict(
            kind=DocumentKind.CV,
            filename="position.pdf",
            sha256="b" * 64,
            mime_type="application/pdf",
            extracted_text="Engineer",
            received_at=TEST_NOW,
        )
    )
    await repo.insert_fact_source(RecordType.POSITION, p.id, doc.id)
    svc = service(container)
    assert hasattr(svc, "coverage"), "Full-profile coverage API missing"
    before = await svc.coverage(alice, job(), AS_OF)
    a = next(a for a in before.snapshot.assertions if a.ref == f"position:{p.id}")
    assert a.sources[0].state == "active" and a.sources[0].document_name == "position.pdf"
    await repo.update_position(p.id, {"title": "Lead"})
    after = await svc.coverage(alice, job(), AS_OF)
    assert (
        next(a for a in after.snapshot.assertions if a.ref == f"position:{p.id}").sources[0].state
        == "stale"
    )
    assert before.snapshot.fingerprint != after.snapshot.fingerprint


async def test_reused_facts_translate_from_authoritative_original_each_time(
    container, alice, fake_llm
):
    from cvopt.services.profile import translate_claims

    claim = (
        await container.data.create_claim(
            alice, type=ClaimType.SKILL, text="Original", text_lang="de"
        )
    ).claim
    facts = await service(container).load(alice)
    runner = container.runners.for_user(alice)
    fake_llm.script("end_turn", {"translations": [{"ref": "T1", "text": "English version"}]})
    fake_llm.script(
        "end_turn", {"verdicts": [{"ref": "T1", "facts_preserved": True, "reason": "synthetic"}]}
    )
    await translate_claims(facts, "en", runner, container.settings)
    assert facts.claims[claim.id].text == "English version"
    await translate_claims(facts, "de", runner, container.settings)
    assert facts.claims[claim.id].text == "Original"
    assert facts.original_claims[claim.id].text == "Original"


async def test_stale_fixed_qualification_context_matches_without_support(container, alice):
    repo = container.repos.for_user(alice)
    lg = await repo.insert_language(dict(language_code="en", level="C2"))
    doc = await repo.insert_document(
        dict(
            kind=DocumentKind.CV,
            filename="languages.pdf",
            sha256="c" * 64,
            mime_type="application/pdf",
            extracted_text="English C2",
            received_at=TEST_NOW,
        )
    )
    await repo.insert_fact_source(RecordType.LANGUAGE, lg.id, doc.id)
    await repo.update_language(lg.id, {"level": "B1"})
    result = await service(container).coverage(
        alice, job("language", language_code="en", language_level="C2"), AS_OF
    )
    assert result.result.total < 100
    assert any(value == 1 for value in result.context_values.values())


async def test_relevant_context_includes_source_details_when_target_has_no_match(
    container, alice, monkeypatch
):
    monkeypatch.setattr(Judge, "verdicts", semantic)
    proposed = await container.data.create_claim(
        alice, type=ClaimType.SKILL, text="Python", text_lang="de"
    )
    await container.data.update_claim(alice, proposed.claim.id, {"text": "Python scope"})
    current = await container.data.get_claim(alice, proposed.claim.id)
    for evidence in current.evidence:
        if evidence.state == "active":
            await container.data.remove_evidence(alice, evidence.id)
    profile = await service(container).coverage(alice, job(), AS_OF)
    from cvopt.domain.report import build_report
    from cvopt.domain.scoring import ScoringParams, score
    from cvopt.services.reports import explain_report

    target = score([], job().requirements, lambda r, e: 0, ScoringParams(), AS_OF)
    report, _ = build_report(job(), target, [], {}, {}, [], [], AS_OF, 6)
    report = explain_report(report, job(), profile, target, [], {}, [], [])
    assert report.requirements[0].labels == ["evidence", "unsupported in profile"]
    from cvopt.domain.report import report_csv

    assert "self_declared" in report_csv(report) and "stale" in report_csv(report)
    assert any(
        a.ref == f"claim:{proposed.claim.id}" and a.sources[0].state == "stale"
        for a in getattr(report, "profile_assertions", [])
    )


async def test_coverage_rejects_facts_loaded_for_another_user(container, alice, bob):
    from cvopt.errors import InputError

    facts = await service(container).load(alice)
    with pytest.raises(InputError, match="another user"):
        await service(container).coverage(bob, job(), AS_OF, facts=facts)


async def test_fallback_headline_provability_uses_active_position_sources(container, alice):
    from cvopt.domain.candidates import Candidate
    from cvopt.services.generation import _Pipeline

    repo = container.repos.for_user(alice)
    position = await repo.insert_position(
        dict(employer="Example", title="Engineer", start_month="2020-01", end_month=None)
    )
    doc = await repo.insert_document(
        dict(
            kind=DocumentKind.REFERENCE_LETTER,
            filename="reference.pdf",
            sha256="d" * 64,
            mime_type="application/pdf",
            extracted_text="Engineer",
            received_at=TEST_NOW,
        )
    )
    await repo.insert_fact_source(RecordType.POSITION, position.id, doc.id, excerpt="Engineer")
    pipeline = _Pipeline(container.generation, alice, job(), AS_OF)
    facts = await pipeline.load()
    candidate = Candidate(
        id="head",
        slot="headline",
        position_id=None,
        text="Engineer",
        source_claim_ids=[],
        source_record_refs=[f"position:{position.id}"],
        fidelity="unmodified",
        provability="medium",
        lines=1,
    )
    assert pipeline.record_sources(facts, candidate)[0].provability.value == "high"
    await repo.update_position(position.id, {"title": "Lead"})
    candidate = candidate.model_copy(update={"text": "Lead"})
    assert (
        pipeline.record_sources(await pipeline.load(), candidate)[0].provability.value == "medium"
    )


async def test_position_only_profile_supports_text_and_owned_years(container, alice, monkeypatch):
    monkeypatch.setattr(Judge, "verdicts", semantic)
    repo = container.repos.for_user(alice)
    p = await repo.insert_position(
        dict(
            employer="Example", title="Python engineer", start_month="2020-01", end_month="2021-12"
        )
    )
    text = await service(container).coverage(alice, job(), AS_OF)
    assert text.result.total == 100
    assert {e.id for e in text.elements} == {f"position:{p.id}"}
    years = await service(container).coverage(alice, job("years_experience", years=4), AS_OF)
    assert years.result.total == 50
    assert years.result.requirements[0].winners == {f"position:{p.id}": 1}
    e = years.elements[0]
    assert e.position_ref == p.id and e.text == "Python engineer · Example" and e.placement == 1


async def test_award_profile_supports_actual_text_without_years(container, alice, monkeypatch):
    monkeypatch.setattr(Judge, "verdicts", semantic)
    repo = container.repos.for_user(alice)
    award = await repo.insert_award(
        dict(
            title="Python delivery award",
            awarder="Example",
            awarded_month="2020-01",
            summary="Recognized delivery",
        )
    )
    text = await service(container).coverage(alice, job(), AS_OF)
    assert text.result.total == 100
    element = text.elements[0]
    assert element.id == f"award:{award.id}" and element.position_ref is None
    assert element.text == "Python delivery award · Example · Recognized delivery"
    assert (
        await service(container).coverage(alice, job("years_experience", years=1), AS_OF)
    ).result.total == 0


@pytest.mark.parametrize("kind", ["position", "award"])
async def test_stale_position_and_award_context_retains_requirement_relation(
    container, alice, monkeypatch, kind
):
    monkeypatch.setattr(Judge, "verdicts", semantic)
    repo = container.repos.for_user(alice)
    if kind == "position":
        record = await repo.insert_position(
            dict(
                employer="Example",
                title="Python engineer",
                start_month="2020-01",
                end_month="2021-12",
            )
        )
    else:
        record = await repo.insert_award(
            dict(title="Python delivery award", awarder="Example", summary="Recognized work")
        )
    doc = await repo.insert_document(
        dict(
            kind=DocumentKind.CV,
            filename="old.pdf",
            sha256="e" * 64,
            mime_type="application/pdf",
            extracted_text="Python engineer and delivery award",
            received_at=TEST_NOW,
        )
    )
    await repo.insert_fact_source(RecordType(kind), record.id, doc.id)
    await getattr(repo, f"update_{kind}")(record.id, {"title": "Unrelated"})
    requirement = job("years_experience", years=4) if kind == "position" else job()
    result = await service(container).coverage(alice, requirement, AS_OF)
    assert result.result.total == 0
    assert any(v > 0 for v in result.context_values.values())
    assert all(e.id.startswith("stale:") for e in result.context_elements)


@pytest.mark.parametrize("position_support", ["manual", "stale", "active"])
async def test_dated_claim_explains_owning_position_proof_separately(
    container, alice, monkeypatch, position_support
):
    from cvopt.domain.report import build_report
    from cvopt.domain.scoring import ScoringParams, score
    from cvopt.services.render import labels, report_details
    from cvopt.services.reports import explain_report

    monkeypatch.setattr(Judge, "verdicts", semantic)
    repo = container.repos.for_user(alice)
    position = await repo.insert_position(
        dict(employer="Example", title="Engineer", start_month="2020-01", end_month="2022-12")
    )
    claim = (
        await container.data.create_claim(
            alice,
            type=ClaimType.EXPERIENCE,
            text="Python delivery",
            text_lang="en",
            position=position.id,
        )
    ).claim
    doc = await repo.insert_document(
        dict(
            kind=DocumentKind.REFERENCE_LETTER,
            filename="proof.pdf",
            sha256="f" * 64,
            mime_type="application/pdf",
            extracted_text="Python delivery. Engineer 2020-01 to 2022-12.",
            received_at=TEST_NOW,
        )
    )
    await container.data.add_evidence(
        alice,
        claim.id,
        kind=EvidenceKind.REFERENCE_LETTER,
        document=doc.id,
        excerpt="Python delivery",
    )
    if position_support != "manual":
        await repo.insert_fact_source(
            RecordType.POSITION, position.id, doc.id, excerpt="Engineer 2020-01 to 2022-12."
        )
        if position_support == "stale":
            await repo.update_position(position.id, {"title": "Engineer II"})
    years = job("years_experience", years=3)
    profile = await service(container).coverage(alice, years, AS_OF)
    assert profile.result.total == 100
    target = score([], years.requirements, lambda r, e: 0, ScoringParams(), AS_OF)
    report, _ = build_report(years, target, [], {}, {}, [], [], AS_OF, 6)
    report = explain_report(report, years, profile, target, [], {}, [], [])
    support = next(
        s for s in report.requirements[0].supporting_elements if s.element_id == f"claim:{claim.id}"
    )
    assert support.provenance_refs == [f"claim:{claim.id}", f"position:{position.id}"]
    assert support.position_ref == f"position:{position.id}"
    assert support.start_month == "2020-01" and support.end_month == "2022-12"
    assert ("evidence" in report.requirements[0].labels) == (position_support != "active")
    shown = report_details(report, labels("en"))["report_requirements"][0].support
    assert any("A2" in line and "2020-01" in line and "2022-12" in line for line in shown)
    from cvopt.domain.cv import CVElement
    from cvopt.domain.provenance import StatementProvenance

    target_element = CVElement(
        id="/work/0/highlights/0",
        kind="highlight",
        text="Python delivery",
        position_ref="/work/0",
        start_month="2020-01",
        end_month="2022-12",
        placement=1,
    )
    target_values = {("R1", target_element.id): 1.0}
    target_score = score(
        [target_element],
        years.requirements,
        lambda r, e: target_values.get((r, e), 0),
        ScoringParams(),
        AS_OF,
    )
    target_report, _ = build_report(years, target_score, [target_element], {}, {}, [], [], AS_OF, 6)
    claim_source = next(a for a in profile.snapshot.assertions if a.ref == f"claim:{claim.id}")
    target_report = explain_report(
        target_report,
        years,
        profile,
        target_score,
        [target_element],
        target_values,
        [
            StatementProvenance(
                element_id=target_element.id,
                text=target_element.text,
                source_claim_ids=[claim.id],
                sources=[claim_source],
            )
        ],
        [],
    )
    target_support = target_report.requirements[0].target_statements[0]
    assert target_support.provenance_refs == [f"claim:{claim.id}", f"position:{position.id}"]
    assert target_support.position_ref == f"position:{position.id}"
    assert target_support.start_month == "2020-01" and target_support.end_month == "2022-12"
    from cvopt.domain.report import report_csv

    csv = report_csv(target_report)
    assert "profile_periods" in csv and f"position:{position.id}:2020-01:2022-12" in csv
    # The same claim's text-only relevance must not acquire unrelated date proof.
    textjob = job()
    textprofile = await service(container).coverage(alice, textjob, AS_OF)
    texttarget = score([], textjob.requirements, lambda r, e: 0, ScoringParams(), AS_OF)
    textreport, _ = build_report(textjob, texttarget, [], {}, {}, [], [], AS_OF, 6)
    textreport = explain_report(textreport, textjob, textprofile, texttarget, [], {}, [], [])
    assert textreport.requirements[0].supporting_elements[0].provenance_refs == [
        f"claim:{claim.id}"
    ]
    assert "evidence" not in textreport.requirements[0].labels
