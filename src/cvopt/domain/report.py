import csv
import io
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

from cvopt.domain.base import Model, Month
from cvopt.domain.cv import CVElement, months_between
from cvopt.domain.enums import Fidelity, Importance, Provability, RequirementCategory, RowKind
from cvopt.domain.jd import JobDescription
from cvopt.domain.provenance import AssertionProvenance, SourceProvenance, StatementProvenance
from cvopt.domain.scoring import ScoreResult


class ReportRow(Model):
    element_id: str | None = None
    row: int
    kind: RowKind
    claim: str
    fidelity: Fidelity | None
    provability: Provability | None
    impact: float
    requirements: list[str]
    source: str


class RequirementSupport(Model):
    element_id: str
    text: str
    value: float
    winning_share: float = 0.0
    provenance_refs: list[str] = []
    position_ref: str | None = None
    start_month: Month | None = None
    end_month: Month | None = None


class RequirementExplanation(Model):
    requirement_id: str
    requirement_text: str
    category: RequirementCategory
    importance: Importance
    profile_coverage: float | None = None
    target_coverage: float
    supporting_elements: list[RequirementSupport] = []
    target_statements: list[RequirementSupport] = []
    unconfirmed_context: list[RequirementSupport] = []
    labels: list[str] = []
    next_actions: list[str] = []


METRIC_EXPLANATIONS = {
    "target_cv_score": (
        "Requirement match of the ideal target CV, including aspirational content and placement."
    ),
    "profile_supported_score": (
        "Requirement match of complete confirmed held medium/high claims and stored fixed facts, "
        "at neutral placement without a page limit. Self declarations remain self declarations."
    ),
    "target_minus_profile": (
        "Signed target CV score minus profile-supported score; either measure may be higher."
    ),
}


class Report(Model):
    """report.json (P1 §9.3)."""

    title: str
    company: str
    created: date
    total: float
    profile_supported_score: float | None = None
    target_minus_profile: float | None = None
    profile_snapshot_fingerprint: str | None = None
    target_cv_fingerprint: str | None = None
    requirements_fingerprint: str | None = None
    scoring_context: dict[str, Any] = {}
    metric_explanations: dict[str, str] = METRIC_EXPLANATIONS
    requirements: list[RequirementExplanation] = []
    provenance: list[StatementProvenance] = []
    profile_assertions: list[AssertionProvenance] = []
    uncertainty: str = (
        "Coverage describes recorded support, not actual capability. "
        "Unconfirmed context never contributes to profile coverage."
    )
    gap_points: float
    by_fidelity: dict[str, float]
    by_provability: dict[str, float]
    logistics: list[str]
    timeline_gaps: list[str]
    rows: list[ReportRow]


# --- building the report (P1 §9.3) --------------------------------------------------

STATEMENT_KINDS = {"headline", "summary", "highlight", "skill"}
KIND_ORDER = {RowKind.IN_CV: 0, RowKind.GAP: 1, RowKind.DROPPED: 2}


class StatementInfo(Model):
    """What the report knows about one CV statement; fidelity and provability are None
    when the statement's text no longer matches its annotation."""

    fidelity: Fidelity | None
    provability: Provability | None
    source: str


class DroppedClaim(Model):
    text: str
    reason: str  # budget, superseded, ...
    requirements: list[str]


def timeline_gaps(ranges: Sequence[tuple[str, str]], min_months: int) -> list[str]:
    """Gaps longer than min_months between positions, as "MM/YYYY – MM/YYYY (n months)"."""
    out: list[str] = []
    covered_until: str | None = None
    for start, end in sorted(ranges):
        if covered_until is not None:
            gap = months_between(covered_until, start) - 1
            if gap > min_months:
                out.append(
                    f"{_mm(next_month(covered_until))} – {_mm(prev_month(start))} ({gap} months)"
                )
        covered_until = end if covered_until is None else max(covered_until, end)
    return out


def _mm(month: str) -> str:
    return f"{month[5:]}/{month[:4]}"


def next_month(m: str) -> str:
    y, mo = map(int, m.split("-"))
    return f"{y + (mo == 12):04d}-{mo % 12 + 1:02d}"


def prev_month(m: str) -> str:
    y, mo = map(int, m.split("-"))
    return f"{y - (mo == 1):04d}-{(mo - 2) % 12 + 1:02d}"


def build_report(
    jd: JobDescription,
    result: ScoreResult,
    elements: Sequence[CVElement],
    statements: dict[str, StatementInfo],
    fact_provability: dict[str, Provability],
    dropped: Sequence[DroppedClaim],
    position_ranges: Sequence[tuple[str, str]],
    created: date,
    gap_months: int,
    values: dict[tuple[str, str], float] | None = None,
) -> tuple[Report, dict[str, int]]:
    """The report and the row number of every element that has a row."""
    relations: dict[str, list[str]] = {}
    for (rid, eid), value in (values or {}).items():
        if value > 0:
            relations.setdefault(eid, []).append(rid)
    wins: dict[str, list[str]] = {}
    for r in result.requirements:
        for eid in r.winners:
            wins.setdefault(eid, []).append(r.requirement_id)
    pending: list[tuple[str | None, ReportRow]] = []
    for e in elements:
        impact = result.impacts.get(e.id, 0.0)
        if e.kind.value in STATEMENT_KINDS:
            info = statements.get(e.id, StatementInfo(fidelity=None, provability=None, source=""))
            pending.append(
                (
                    e.id,
                    ReportRow(
                        element_id=e.id,
                        row=0,
                        kind=RowKind.IN_CV,
                        claim=e.text,
                        fidelity=info.fidelity,
                        provability=info.provability,
                        impact=impact,
                        requirements=relations.get(e.id, wins.get(e.id, [])),
                        source=info.source,
                    ),
                )
            )
        else:
            pending.append(
                (
                    e.id,
                    ReportRow(
                        element_id=e.id,
                        row=0,
                        kind=RowKind.IN_CV,
                        claim=e.text,
                        fidelity=Fidelity.UNMODIFIED,
                        provability=fact_provability.get(e.id, Provability.MEDIUM),
                        impact=impact,
                        requirements=relations.get(e.id, wins.get(e.id, [])),
                        source="stored record",
                    ),
                )
            )
    texts = {r.id: r.text for r in jd.requirements}
    for r in result.requirements:
        if r.coverage < 1 - 1e-9:
            pending.append(
                (
                    None,
                    ReportRow(
                        row=0,
                        kind=RowKind.GAP,
                        claim=texts[r.requirement_id],
                        fidelity=None,
                        provability=None,
                        impact=r.gap_points,
                        requirements=[r.requirement_id],
                        source="",
                    ),
                )
            )
    for d in dropped:
        pending.append(
            (
                None,
                ReportRow(
                    row=0,
                    kind=RowKind.DROPPED,
                    claim=d.text,
                    fidelity=None,
                    provability=None,
                    impact=0.0,
                    requirements=d.requirements,
                    source=d.reason,
                ),
            )
        )
    order = sorted(
        range(len(pending)),
        key=lambda i: (KIND_ORDER[pending[i][1].kind], -pending[i][1].impact, i),
    )
    rows: list[ReportRow] = []
    numbers: dict[str, int] = {}
    for n, i in enumerate(order, start=1):
        ref, row = pending[i]
        rows.append(row.model_copy(update={"row": n}))
        if ref:
            numbers[ref] = n
    in_cv = [r for r in rows if r.kind is RowKind.IN_CV]

    def split(key: Callable[[ReportRow], str]) -> dict[str, float]:
        out: dict[str, float] = {}
        for r in in_cv:
            out[key(r)] = out.get(key(r), 0.0) + r.impact
        return {k: round(v, 2) for k, v in sorted(out.items()) if v > 0}

    report = Report(
        title=jd.title,
        company=jd.company,
        created=created,
        total=result.total,
        requirements=[
            RequirementExplanation(
                requirement_id=r.id,
                requirement_text=r.text,
                category=r.category,
                importance=r.importance,
                target_coverage=rr.coverage,
                target_statements=[
                    RequirementSupport(
                        element_id=e.id,
                        text=e.text,
                        value=(values or {}).get(
                            (r.id, e.id), rr.coverage if e.id in rr.winners else 0
                        ),
                        winning_share=rr.winners.get(e.id, 0),
                    )
                    for e in elements
                    if (values or {}).get((r.id, e.id), rr.winners.get(e.id, 0)) > 0
                ],
            )
            for r in jd.requirements
            for rr in result.requirements
            if rr.requirement_id == r.id
        ],
        gap_points=sum(r.gap_points for r in result.requirements),
        by_fidelity=split(lambda r: r.fidelity.value if r.fidelity else "unknown"),
        by_provability=split(lambda r: r.provability.value if r.provability else "unknown"),
        logistics=jd.logistics,
        timeline_gaps=timeline_gaps(position_ranges, gap_months),
        rows=rows,
    )
    return report, numbers


def report_csv(report: Report) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["metric", "value", "explanation"])
    for name, value in (
        ("target_cv_score", report.total),
        ("profile_supported_score", report.profile_supported_score),
        ("target_minus_profile", report.target_minus_profile),
    ):
        w.writerow(
            [
                name,
                "" if value is None else f"{value:.2f}",
                report.metric_explanations.get(name, ""),
            ]
        )
    w.writerow([])
    w.writerow(
        [
            "requirement_id",
            "requirement_text",
            "category",
            "importance",
            "profile_coverage",
            "target_coverage",
            "support_refs",
            "profile_periods",
            "profile_winners",
            "target_statements",
            "target_periods",
            "target_winners",
            "unconfirmed_context",
            "gap_labels",
            "next_actions",
        ]
    )
    for req in report.requirements:
        w.writerow(
            [
                req.requirement_id,
                req.requirement_text,
                req.category.value,
                req.importance.value,
                "" if req.profile_coverage is None else f"{req.profile_coverage:.4f}",
                f"{req.target_coverage:.4f}",
                " | ".join(
                    dict.fromkeys(
                        ref
                        for s in req.supporting_elements
                        for ref in (s.provenance_refs or [s.element_id])
                    )
                ),
                " | ".join(
                    dict.fromkeys(
                        f"{s.position_ref}:{s.start_month}:{s.end_month}"
                        for s in req.supporting_elements
                        if s.position_ref
                    )
                ),
                " | ".join(
                    f"{s.element_id}:{s.winning_share:.4f}"
                    for s in req.supporting_elements
                    if s.winning_share
                ),
                " | ".join(f"{s.element_id}: {s.text}" for s in req.target_statements),
                " | ".join(
                    dict.fromkeys(
                        f"{s.position_ref}:{s.start_month}:{s.end_month}"
                        for s in req.target_statements
                        if s.position_ref
                    )
                ),
                " | ".join(
                    f"{s.element_id}:{s.winning_share:.4f}"
                    for s in req.target_statements
                    if s.winning_share
                ),
                " | ".join(f"{s.element_id}: {s.text}" for s in req.unconfirmed_context),
                " | ".join(req.labels),
                " | ".join(req.next_actions),
            ]
        )
    w.writerow([])
    w.writerow(
        [
            "row",
            "claim",
            "fidelity",
            "provability",
            "impact_pp",
            "requirements",
            "source",
            "row_kind",
        ]
    )
    for r in report.rows:
        w.writerow(
            [
                r.row,
                r.claim,
                r.fidelity.value if r.fidelity else "",
                r.provability.value if r.provability else "",
                f"{r.impact:.2f}",
                " ".join(r.requirements),
                r.source,
                r.kind.value,
            ]
        )
    w.writerow([])
    w.writerow(
        [
            "element_id",
            "source_claim_ids",
            "source_record_refs",
            "inferred_from",
            "transformation_reason",
            "assertion_ref",
            "original_wording",
            "source_id",
            "source_kind",
            "source_state",
            "document_id",
            "document_name",
            "excerpt",
            "snapshot_origin",
        ]
    )
    for statement in report.provenance:
        for assertion in statement.sources:
            sources: Sequence[SourceProvenance | None] = assertion.sources or [None]
            for source in sources:
                w.writerow(
                    [
                        statement.element_id,
                        " ".join(statement.source_claim_ids),
                        " ".join(statement.source_record_refs),
                        " ".join(statement.inferred_from),
                        statement.transformation_reason,
                        assertion.ref,
                        assertion.original_text,
                        source.id if source else "",
                        source.kind if source else "",
                        source.state if source else "",
                        source.document_id if source else "",
                        source.document_name if source else "",
                        source.excerpt if source else "",
                        source.snapshot_origin if source else "",
                    ]
                )
    w.writerow([])
    w.writerow(
        [
            "assertion_ref",
            "original_wording",
            "eligible",
            "status",
            "provability",
            "source_id",
            "source_kind",
            "source_state",
            "document_id",
            "document_name",
            "excerpt",
            "snapshot_origin",
        ]
    )
    for assertion in report.profile_assertions:
        attached: Sequence[SourceProvenance | None] = assertion.sources or [None]
        for source in attached:
            w.writerow(
                [
                    assertion.ref,
                    assertion.original_text,
                    assertion.eligible,
                    assertion.status,
                    assertion.provability.value,
                    source.id if source else "",
                    source.kind if source else "",
                    source.state if source else "",
                    source.document_id if source else "",
                    source.document_name if source else "",
                    source.excerpt if source else "",
                    source.snapshot_origin if source else "",
                ]
            )
    return buf.getvalue()
