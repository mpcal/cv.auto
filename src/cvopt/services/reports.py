"""Explain independent profile and target scores using recorded provenance only."""

from collections.abc import Sequence

from cvopt.domain.cv import CVElement
from cvopt.domain.enums import RequirementCategory as C
from cvopt.domain.jd import JobDescription
from cvopt.domain.provenance import StatementProvenance
from cvopt.domain.report import DroppedClaim, Report, RequirementExplanation, RequirementSupport
from cvopt.domain.scoring import ScoreResult
from cvopt.services.profile import ProfileCoverage

ACTIONS = {
    "wording": "Use a supported rephrasing that makes the relevant experience explicit.",
    "evidence": "Review the recorded support and attach a current source where available.",
    "transferable": "Review whether the explicitly cited experience transfers to this requirement.",
    "partial support": "Clarify the scope of the recorded experience against the full requirement.",
    "unsupported in profile": (
        "Review whether relevant experience can be added to the confirmed profile."
    ),
    "qualification": (
        "Compare the recorded qualification or dated experience with the stated threshold."
    ),
    "omitted for space": (
        "Review whether this supported content should replace other content within the CV budget."
    ),
}


def explain_report(
    report: Report,
    jd: JobDescription,
    profile: ProfileCoverage,
    target: ScoreResult,
    elements: Sequence[CVElement],
    values: dict[tuple[str, str], float],
    provenance: list[StatementProvenance],
    dropped: Sequence[DroppedClaim],
) -> Report:
    assertions = {a.ref: a for a in profile.snapshot.assertions}
    statement_sources = {s.element_id: s for s in provenance}
    target_results = {r.requirement_id: r for r in target.requirements}
    profile_results = {r.requirement_id: r for r in profile.result.requirements}
    fixed_refs = {}
    for section, name in (
        ("education", "education"),
        ("languages", "language"),
        ("certificates", "certificate"),
    ):
        for i, record in enumerate(getattr(profile.snapshot, section)):
            fixed_refs[f"/{section}/{i}"] = f"{name}:{record.id}"
    position_refs = {p.id: f"position:{p.id}" for p in profile.snapshot.positions}
    position_refs.update(
        {f"/work/{i}": f"position:{p.id}" for i, p in enumerate(profile.snapshot.positions)}
    )

    def support(
        category: C, element: CVElement, value: float, share: float, refs: list[str]
    ) -> RequirementSupport:
        position = position_refs.get(element.position_ref or "")
        dated = (
            category is C.YEARS_EXPERIENCE
            and position is not None
            and element.start_month is not None
        )
        now = f"{profile.as_of:%Y-%m}"
        return RequirementSupport(
            element_id=element.id,
            text=element.text,
            value=value,
            winning_share=share,
            provenance_refs=list(dict.fromkeys([*refs, *([position] if dated else [])])),
            position_ref=position if dated else None,
            start_month=element.start_month if dated else None,
            end_month=min(element.end_month or now, now) if dated else None,
        )

    context_refs = {f"context:{a.ref}": a.ref for a in assertions.values()}
    context_refs.update({f"stale:{s.id}": a.ref for a in assertions.values() for s in a.sources})
    out = []
    for r in jd.requirements:
        if r.category is C.LOGISTICS:
            continue
        pr, tr = profile_results[r.id], target_results[r.id]
        supporting = [
            support(
                r.category,
                e,
                profile.values[r.id, e.id],
                pr.winners.get(e.id, 0),
                [fixed_refs.get(e.id, e.id)],
            )
            for e in profile.elements
            if profile.values.get((r.id, e.id), 0) > 0
        ]
        targets = [
            support(
                r.category,
                e,
                values[r.id, e.id],
                tr.winners.get(e.id, 0),
                [a.ref for a in statement_sources[e.id].sources]
                if e.id in statement_sources
                else [],
            )
            for e in elements
            if values.get((r.id, e.id), 0) > 0
        ]
        context = [
            RequirementSupport(
                element_id=e.id,
                text=e.text,
                value=profile.context_values[r.id, e.id],
                provenance_refs=[context_refs[e.id]] if e.id in context_refs else [],
            )
            for e in profile.context_elements
            if profile.context_values.get((r.id, e.id), 0) > 0
        ]
        related = {ref for hit in [*supporting, *context] for ref in hit.provenance_refs}
        related.update(ref for hit in targets for ref in hit.provenance_refs)
        labels = set()
        for hit in targets:
            statement = statement_sources.get(hit.element_id)
            if not statement:
                continue
            original = max(
                (
                    profile.values.get((r.id, f"claim:{cid}"), 0)
                    for cid in statement.source_claim_ids
                ),
                default=0,
            )
            if (
                statement.transformation_reason == "rephrased"
                and statement.source_claim_ids
                and all(
                    assertions.get(f"claim:{cid}") is not None
                    and assertions[f"claim:{cid}"].eligible
                    for cid in statement.source_claim_ids
                )
                and hit.value > original + 1e-9
            ):
                labels.add("wording")
            if statement.inferred_from:
                labels.add("transferable")
        current_refs = {ref for hit in [*supporting, *targets] for ref in hit.provenance_refs}
        context_only_refs = {ref for hit in context for ref in hit.provenance_refs} - current_refs
        for ref in related:
            assertion = assertions.get(ref)
            if assertion is None:
                continue
            current_high_proof = (
                ref in current_refs
                and assertion.provability.value == "high"
                and any(
                    s.state == "active"
                    and s.kind
                    in (
                        "reference_letter",
                        "reference_person",
                        "certificate_or_diploma",
                        "fact_source",
                    )
                    for s in assertion.sources
                )
            )
            if current_high_proof:
                continue
            if (
                ref in context_only_refs
                or assertion.provability.value != "high"
                or any(s.state != "active" for s in assertion.sources)
            ):
                labels.add("evidence")
                break
        if 0 < pr.coverage < 1 - 1e-9:
            labels.add("partial support")
        elif pr.coverage <= 0:
            labels.add("unsupported in profile")
        if (
            r.category in (C.EDUCATION, C.CERTIFICATION, C.LANGUAGE, C.YEARS_EXPERIENCE)
            and pr.coverage < 1 - 1e-9
        ):
            labels.add("qualification")
        if any(d.reason == "budget" and r.id in d.requirements for d in dropped):
            labels.add("omitted for space")
        ordered = [label for label in ACTIONS if label in labels]
        out.append(
            RequirementExplanation(
                requirement_id=r.id,
                requirement_text=r.text,
                category=r.category,
                importance=r.importance,
                profile_coverage=pr.coverage,
                target_coverage=tr.coverage,
                supporting_elements=supporting,
                target_statements=targets,
                unconfirmed_context=context,
                labels=ordered,
                next_actions=[ACTIONS[label] for label in ordered],
            )
        )
    return report.model_copy(
        update={
            "profile_supported_score": profile.result.total,
            "target_minus_profile": target.total - profile.result.total,
            "profile_snapshot_fingerprint": profile.snapshot.fingerprint,
            "requirements": out,
            "provenance": provenance,
            "profile_assertions": profile.snapshot.assertions,
        }
    )
