"""Full-profile reporting regressions; all match values here are synthetic."""

import importlib
from datetime import date

import pytest
from pydantic import ValidationError

from cvopt.domain.cv import CVElement
from cvopt.domain.enums import ElementKind, RequirementCategory
from cvopt.domain.jd import JobDescription, Requirement
from cvopt.domain.report import Report, StatementInfo, build_report, report_csv
from cvopt.domain.scoring import ScoringParams, score

AS_OF = date(2026, 6, 30)


def requirement(**updates):
    return Requirement.model_validate(
        {
            "id": "R1",
            "text": "Python",
            "category": "hard_skill",
            "importance": "must",
            "keywords": ["Python"],
            "synonyms": [],
            **updates,
        }
    )


def jd(reqs=None):
    return JobDescription(
        title="Engineer",
        company="Example",
        language="en",
        requirements=reqs if reqs is not None else [requirement()],
        logistics=[],
    )


@pytest.mark.parametrize(
    "updates",
    [
        {"id": " "},
        {"years": 0},
        {"years": -1},
        {"years": float("nan")},
        {"years": float("inf")},
        {"language_level": "C3"},
    ],
)
def test_invalid_scoring_prerequisites_rejected(updates):
    with pytest.raises(ValidationError):
        requirement(**updates)


def test_duplicate_requirement_ids_rejected():
    with pytest.raises(ValidationError):
        jd([requirement(), requirement(text="SQL")])


def test_legacy_report_unknown_profile_is_unavailable():
    report = Report(
        title="Old",
        company="",
        created=AS_OF,
        total=42,
        gap_points=58,
        by_fidelity={},
        by_provability={},
        logistics=[],
        timeline_gaps=[],
        rows=[],
    )
    assert getattr(report, "profile_supported_score", "missing") is None
    assert getattr(report, "target_minus_profile", "missing") is None


def profile_module():
    try:
        return importlib.import_module("cvopt.services.profile")
    except ModuleNotFoundError:
        pytest.fail("Reusable full-profile service is missing")


def test_held_long_untied_claims_count_without_budget_or_placement():
    from cvopt.domain.enums import ClaimStatus, ClaimType, Origin, Provability
    from cvopt.domain.records import Claim, Profile
    from cvopt.services.generation import Facts

    claims = {
        k: Claim(
            id=k,
            type=ClaimType.SKILL if k != "untied" else ClaimType.ACHIEVEMENT,
            text="Python " + "full original " * 80,
            text_lang="en",
            position_id=None,
            keywords=[],
            status=ClaimStatus.CONFIRMED,
            origin=Origin.MANUAL,
            provability=p,
        )
        for k, p in [
            ("held", Provability.MEDIUM),
            ("low", Provability.LOW),
            ("untied", Provability.HIGH),
        ]
    }
    facts = Facts(
        profile=Profile(),
        positions=[],
        education=[],
        languages=[],
        certificates=[],
        awards=[],
        claims=claims,
        original_text={k: c.text for k, c in claims.items()},
        original_claims=claims,
        fact_provability={},
    )
    elements = profile_module().profile_elements(facts, AS_OF, "en")
    assert {e.id for e in elements} == {"claim:held", "claim:untied"}
    assert all(e.placement == 1 and len(e.text) > 500 for e in elements)
    assert next(e for e in elements if e.id == "claim:untied").kind is ElementKind.HIGHLIGHT
    assert score(elements, jd().requirements, lambda r, e: 1, ScoringParams(), AS_OF).total == 100
    years = requirement(category=RequirementCategory.YEARS_EXPERIENCE, years=2)
    assert score(elements, [years], lambda r, e: 1, ScoringParams(), AS_OF).total == 0


def test_csv_leads_metrics_requirements_and_keeps_zero_impact_selected():
    e = CVElement(id="skill", text="Python", kind=ElementKind.SKILL, placement=1)
    result = score([e], jd().requirements, lambda r, e: 0, ScoringParams(), AS_OF)
    report, rows = build_report(
        jd(),
        result,
        [e],
        {"skill": StatementInfo(fidelity=None, provability=None, source="source")},
        {},
        [],
        [],
        AS_OF,
        6,
    )
    csv = report_csv(report)
    assert "target_cv_score" in csv and "profile_supported_score" in csv
    assert "requirement_text" in csv
    assert rows["skill"] > 0 and any(r.claim == "Python" and r.impact == 0 for r in report.rows)


def test_explanations_keep_positive_nonwinners_signed_difference_and_actual_causes():
    module = profile_module()
    try:
        reports = importlib.import_module("cvopt.services.reports")
    except ModuleNotFoundError:
        pytest.fail("Requirement explanation service is missing")
    from cvopt.domain.enums import Provability
    from cvopt.domain.provenance import (
        AssertionProvenance,
        ProfileSnapshot,
        SourceProvenance,
        StatementProvenance,
    )
    from cvopt.domain.records import Profile
    from cvopt.domain.report import DroppedClaim

    a = AssertionProvenance(
        ref="claim:a",
        original_text="Python",
        eligible=True,
        provability=Provability.MEDIUM,
        sources=[
            SourceProvenance(
                id="e", kind="self_declared", state="active", snapshot_origin="captured"
            )
        ],
    )
    snapshot = ProfileSnapshot(
        profile=Profile(),
        claims=[],
        positions=[],
        education=[],
        languages=[],
        certificates=[],
        awards=[],
        assertions=[a],
        fingerprint="snapshot",
    )
    p = [CVElement(id="claim:a", text="Python", kind=ElementKind.SKILL, placement=1)]
    profile_score = score(p, jd().requirements, lambda r, e: 1, ScoringParams(), AS_OF)
    supported = module.ProfileCoverage(
        profile_score, p, {("R1", "claim:a"): 1}, [], {}, snapshot, AS_OF, {}
    )
    target = [
        CVElement(id="one", text="Python", kind=ElementKind.SKILL, placement=0.7),
        CVElement(id="two", text="Python tools", kind=ElementKind.SKILL, placement=0.7),
    ]
    target_values = {("R1", "one"): 1, ("R1", "two"): 0.6}
    target_score = score(
        target, jd().requirements, lambda r, e: target_values[r, e], ScoringParams(), AS_OF
    )
    report, _ = build_report(jd(), target_score, target, {}, {}, [], [], AS_OF, 6)
    enriched = reports.explain_report(
        report,
        jd(),
        supported,
        target_score,
        target,
        target_values,
        [
            StatementProvenance(
                element_id="two", text="Python tools", inferred_from=["a"], sources=[a]
            )
        ],
        [DroppedClaim(text="Python", reason="no gain", requirements=["R1"])],
    )
    assert enriched.profile_supported_score == 100
    assert enriched.target_minus_profile == -30
    explanation = enriched.requirements[0]
    assert {s.element_id for s in explanation.target_statements} == {"one", "two"}
    assert (
        next(s for s in explanation.target_statements if s.element_id == "two").winning_share == 0
    )
    assert explanation.labels == ["evidence", "transferable"]
    assert "omitted for space" not in explanation.labels
    budget = reports.explain_report(
        report,
        jd(),
        supported,
        target_score,
        target,
        target_values,
        [],
        [DroppedClaim(text="Python", reason="budget", requirements=["R1"])],
    )
    assert budget.requirements[0].labels == ["evidence", "omitted for space"]


def test_partial_support_not_unsupported_and_wording_label_needs_gain():
    module = profile_module()
    try:
        reports = importlib.import_module("cvopt.services.reports")
    except ModuleNotFoundError:
        pytest.fail("Requirement explanation service is missing")
    from cvopt.domain.enums import Provability
    from cvopt.domain.provenance import AssertionProvenance, ProfileSnapshot, StatementProvenance
    from cvopt.domain.records import Profile

    a = AssertionProvenance(
        ref="claim:a", original_text="Programming", eligible=True, provability=Provability.HIGH
    )
    snapshot = ProfileSnapshot(
        profile=Profile(),
        claims=[],
        positions=[],
        education=[],
        languages=[],
        certificates=[],
        awards=[],
        assertions=[a],
    )
    els = [CVElement(id="claim:a", text="Programming", kind=ElementKind.SKILL, placement=1)]
    ps = score(els, jd().requirements, lambda r, e: 0.3, ScoringParams(), AS_OF)
    profile = module.ProfileCoverage(ps, els, {("R1", "claim:a"): 0.3}, [], {}, snapshot, AS_OF, {})
    target = [CVElement(id="one", text="Python", kind=ElementKind.SKILL, placement=1)]
    ts = score(target, jd().requirements, lambda r, e: 1, ScoringParams(), AS_OF)
    report, _ = build_report(jd(), ts, target, {}, {}, [], [], AS_OF, 6)
    enriched = reports.explain_report(
        report,
        jd(),
        profile,
        ts,
        target,
        {("R1", "one"): 1},
        [
            StatementProvenance(
                element_id="one",
                text="Python",
                source_claim_ids=["a"],
                transformation_reason="rephrased",
                sources=[a],
            )
        ],
        [],
    )
    assert enriched.requirements[0].labels == ["wording", "partial support"]
    assert len(enriched.requirements[0].next_actions) == 2


def test_selected_fixed_fact_with_zero_impact_retains_content_row():
    element = CVElement(
        id="/education/0", text="Stored diploma", kind=ElementKind.EDUCATION, placement=1
    )
    result = score([element], jd().requirements, lambda r, e: 0, ScoringParams(), AS_OF)
    report, rows = build_report(jd(), result, [element], {}, {}, [], [], AS_OF, 6)
    assert rows["/education/0"] > 0
    assert any(r.claim == "Stored diploma" and r.kind.value == "in_cv" for r in report.rows)


def test_learnable_rephrasing_does_not_claim_held_wording_support():
    from cvopt.domain.provenance import AssertionProvenance, ProfileSnapshot, StatementProvenance
    from cvopt.domain.records import Profile
    from cvopt.services.profile import ProfileCoverage
    from cvopt.services.reports import explain_report

    a = AssertionProvenance(
        ref="claim:low", original_text="Python", eligible=False, provability="low"
    )
    snapshot = ProfileSnapshot(
        profile=Profile(),
        claims=[],
        positions=[],
        education=[],
        languages=[],
        certificates=[],
        awards=[],
        assertions=[a],
    )
    ps = score([], jd().requirements, lambda r, e: 0, ScoringParams(), AS_OF)
    profile = ProfileCoverage(ps, [], {}, [], {}, snapshot, AS_OF, {})
    target = [CVElement(id="s", text="Python", kind=ElementKind.SKILL, placement=1)]
    ts = score(target, jd().requirements, lambda r, e: 1, ScoringParams(), AS_OF)
    report, _ = build_report(jd(), ts, target, {}, {}, [], [], AS_OF, 6)
    result = explain_report(
        report,
        jd(),
        profile,
        ts,
        target,
        {("R1", "s"): 1},
        [
            StatementProvenance(
                element_id="s",
                text="Python",
                source_claim_ids=["low"],
                transformation_reason="rephrased",
                sources=[a],
            )
        ],
        [],
    )
    assert "wording" not in result.requirements[0].labels
    assert "unsupported in profile" in result.requirements[0].labels


def test_duplicate_target_wording_keeps_each_rows_own_source_relationship():
    from cvopt.domain.provenance import AssertionProvenance, StatementProvenance
    from cvopt.services.render import labels, report_details

    elements = [
        CVElement(id="one", text="Python", kind=ElementKind.SKILL, placement=1),
        CVElement(id="two", text="Python", kind=ElementKind.HIGHLIGHT, placement=1),
    ]
    result = score(elements, jd().requirements, lambda r, e: 1, ScoringParams(), AS_OF)
    report, rows = build_report(jd(), result, elements, {}, {}, [], [], AS_OF, 6)
    a = AssertionProvenance(
        ref="claim:a", original_text="First source", eligible=True, provability="medium"
    )
    b = AssertionProvenance(
        ref="claim:b", original_text="Second source", eligible=True, provability="medium"
    )
    report = report.model_copy(
        update={
            "provenance": [
                StatementProvenance(element_id="one", text="Python", sources=[a]),
                StatementProvenance(element_id="two", text="Python", sources=[b]),
            ]
        }
    )
    refs = report_details(report, labels("en"))["report_row_sources"]
    assert refs[str(rows["one"])] == "A1"
    assert refs[str(rows["two"])] == "A2"


@pytest.mark.parametrize(
    "kind", ["reference_letter", "reference_person", "certificate_or_diploma", "fact_source"]
)
def test_current_high_support_suppresses_history_only_evidence_action(kind):
    from cvopt.domain.provenance import AssertionProvenance, ProfileSnapshot, SourceProvenance
    from cvopt.domain.records import Profile
    from cvopt.services.profile import ProfileCoverage
    from cvopt.services.reports import explain_report

    active = SourceProvenance(id="active", kind=kind, state="active", snapshot_origin="captured")
    stale = active.model_copy(update={"id": "old", "state": "stale"})
    declaration = SourceProvenance(
        id="declared", kind="self_declared", state="active", snapshot_origin="captured"
    )
    assertion = AssertionProvenance(
        ref="claim:a",
        original_text="Python",
        eligible=True,
        provability="high",
        sources=[active, stale, declaration],
    )
    snapshot = ProfileSnapshot(
        profile=Profile(),
        claims=[],
        positions=[],
        education=[],
        languages=[],
        certificates=[],
        awards=[],
        assertions=[assertion],
    )
    element = CVElement(id="claim:a", text="Python", kind=ElementKind.SKILL, placement=1)
    ps = score([element], jd().requirements, lambda r, e: 1, ScoringParams(), AS_OF)
    profile = ProfileCoverage(ps, [element], {("R1", "claim:a"): 1}, [], {}, snapshot, AS_OF, {})
    target = score([], jd().requirements, lambda r, e: 0, ScoringParams(), AS_OF)
    report, _ = build_report(jd(), target, [], {}, {}, [], [], AS_OF, 6)
    explained = explain_report(report, jd(), profile, target, [], {}, [], [])
    assert "evidence" not in explained.requirements[0].labels
    # Only the obsolete assertion matches this requirement; current HIGH proof of
    # another assertion does not repair the old context's missing current support.
    empty = score([], jd().requirements, lambda r, e: 0, ScoringParams(), AS_OF)
    context = element.model_copy(update={"id": "stale:old"})
    profile = ProfileCoverage(
        empty, [], {}, [context], {("R1", "stale:old"): 1}, snapshot, AS_OF, {}
    )
    explained = explain_report(report, jd(), profile, target, [], {}, [], [])
    assert explained.requirements[0].labels == ["evidence", "unsupported in profile"]


@pytest.mark.parametrize("end", [None, "2030-12"])
def test_qualifying_period_ends_at_common_scoring_date(end):
    from cvopt.domain.provenance import AssertionProvenance, ProfileSnapshot
    from cvopt.domain.records import Position, Profile
    from cvopt.services.profile import ProfileCoverage
    from cvopt.services.reports import explain_report

    position = Position(
        id="p", employer="Example", title="Engineer", start_month="2020-01", end_month=end
    )
    snapshot = ProfileSnapshot(
        profile=Profile(),
        claims=[],
        positions=[position],
        education=[],
        languages=[],
        certificates=[],
        awards=[],
        assertions=[
            AssertionProvenance(
                ref="claim:c", original_text="Python", eligible=True, provability="high"
            ),
            AssertionProvenance(
                ref="position:p", original_text="Engineer", eligible=True, provability="medium"
            ),
        ],
    )
    req = jd([requirement(category="years_experience", years=3)])
    element = CVElement(
        id="claim:c",
        text="Python",
        kind=ElementKind.HIGHLIGHT,
        position_ref="p",
        start_month="2020-01",
        end_month=end,
        placement=1,
    )
    result = score([element], req.requirements, lambda r, e: 1, ScoringParams(), AS_OF)
    profile = ProfileCoverage(
        result, [element], {("R1", "claim:c"): 1}, [], {}, snapshot, AS_OF, {}
    )
    report, _ = build_report(req, result, [], {}, {}, [], [], AS_OF, 6)
    explained = explain_report(report, req, profile, result, [], {}, [], [])
    hit = explained.requirements[0].supporting_elements[0]
    assert hit.position_ref == "position:p" and hit.start_month == "2020-01"
    assert hit.end_month == "2026-06"
