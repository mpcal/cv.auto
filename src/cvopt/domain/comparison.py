"""Current-profile role ranking and conservative recurring requirement identity."""

import csv
import io
import json
from datetime import date
from typing import Any

from cvopt.domain.base import Model
from cvopt.domain.jd import Requirement
from cvopt.domain.provenance import ProfileSnapshot
from cvopt.domain.report import RequirementSupport
from cvopt.domain.text import normalize


class SavedTarget(Model):
    score: float | None = None
    generated_at: str | None = None
    stale: dict[str, bool | None]


class ComparisonRequirement(Model):
    requirement: Requirement
    coverage: float
    weighted_missing_points: float
    supporting_elements: list[RequirementSupport] = []


class ComparedRole(Model):
    run_id: str
    name: str
    title: str
    company: str
    requirements_fingerprint: str
    profile_supported_score: float
    must_gap_points: float
    saved_target: SavedTarget
    requirements: list[ComparisonRequirement]


class GapInstance(Model):
    run_id: str
    run_name: str
    requirement_id: str
    importance: str
    weighted_missing_points: float


class RecurringGap(Model):
    signature: str
    requirement: Requirement
    role_count: int
    weighted_missing_points: float
    instances: list[GapInstance]


class Comparison(Model):
    as_of: date
    profile_snapshot_fingerprint: str
    profile_snapshot: ProfileSnapshot
    scoring_context: dict[str, Any]
    roles: list[ComparedRole]
    recurring_gaps: list[RecurringGap]
    usage: dict[str, Any]
    gap_units: str = (
        "Weighted missing percentage points per role, summed across instances. "
        "Each role's scored requirement weights normalize to 100 points."
    )
    uncertainty: str = (
        "Coverage describes recorded support, not actual capability. Saved target scores "
        "retain their generation date and never contribute to this ranking."
    )


def requirement_signature(requirement: Requirement) -> str:
    def normalized(value: Any) -> Any:
        if isinstance(value, str):
            return normalize(value)
        if isinstance(value, list):
            return sorted(set(normalized(v) for v in value))
        return value

    return json.dumps(
        {
            k: normalized(v)
            for k, v in requirement.model_dump(mode="json").items()
            if k not in ("id", "importance")
        },
        sort_keys=True,
        ensure_ascii=False,
    )


def recurring_gaps(roles: list[ComparedRole]) -> list[RecurringGap]:
    grouped: dict[str, tuple[Requirement, list[GapInstance]]] = {}
    for role in roles:
        for row in role.requirements:
            if row.weighted_missing_points <= 1e-9:
                continue
            req = row.requirement
            _, instances = grouped.setdefault(requirement_signature(req), (req, []))
            instances.append(
                GapInstance(
                    run_id=role.run_id,
                    run_name=role.name,
                    requirement_id=req.id,
                    importance=req.importance.value,
                    weighted_missing_points=row.weighted_missing_points,
                )
            )
    result = []
    for signature, (req, instances) in grouped.items():
        count = len({i.run_name for i in instances})
        if count >= 2:
            result.append(
                RecurringGap(
                    signature=signature,
                    requirement=req,
                    role_count=count,
                    weighted_missing_points=sum(i.weighted_missing_points for i in instances),
                    instances=instances,
                )
            )
    return sorted(result, key=lambda g: (-g.weighted_missing_points, g.signature))


def comparison_csv(comparison: Comparison) -> str:
    stream = io.StringIO()
    writer = csv.writer(stream)
    writer.writerow(
        [
            "rank",
            "run_id",
            "run_name",
            "title",
            "company",
            "as_of",
            "profile_supported_score",
            "must_gap_points",
            "saved_target_score",
            "target_generated_at",
            "stale_profile",
            "stale_requirements",
            "stale_config",
            "stale_date",
            "profile_snapshot_fingerprint",
        ]
    )
    for rank, role in enumerate(comparison.roles, 1):
        target = role.saved_target
        writer.writerow(
            [
                rank,
                role.run_id,
                role.name,
                role.title,
                role.company,
                comparison.as_of,
                role.profile_supported_score,
                role.must_gap_points,
                target.score,
                target.generated_at,
                *(target.stale[k] for k in ("profile", "requirements", "config", "date")),
                comparison.profile_snapshot_fingerprint,
            ]
        )
    writer.writerow([])
    writer.writerow(
        [
            "run_id",
            "run_name",
            "requirement_id",
            "requirement",
            "category",
            "importance",
            "coverage",
            "weighted_missing_pp",
            "support_refs",
        ]
    )
    for role in comparison.roles:
        for row in role.requirements:
            req = row.requirement
            writer.writerow(
                [
                    role.run_id,
                    role.name,
                    req.id,
                    req.text,
                    req.category.value,
                    req.importance.value,
                    row.coverage,
                    row.weighted_missing_points,
                    " | ".join(
                        dict.fromkeys(
                            ref
                            for support in row.supporting_elements
                            for ref in support.provenance_refs
                        )
                    ),
                ]
            )
    writer.writerow([])
    writer.writerow(
        [
            "recurring_requirement",
            "distinct_roles",
            "summed_weighted_missing_pp",
            "run_id",
            "run_name",
            "requirement_id",
            "importance",
            "weighted_missing_pp",
        ]
    )
    for gap in comparison.recurring_gaps:
        for instance in gap.instances:
            writer.writerow(
                [
                    gap.requirement.text,
                    gap.role_count,
                    gap.weighted_missing_points,
                    instance.run_id,
                    instance.run_name,
                    instance.requirement_id,
                    instance.importance,
                    instance.weighted_missing_points,
                ]
            )
    return stream.getvalue()
