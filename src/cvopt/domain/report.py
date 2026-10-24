import csv
import io
from collections.abc import Callable, Sequence
from datetime import date

from cvopt.domain.base import Model
from cvopt.domain.cv import CVElement, months_between
from cvopt.domain.enums import Fidelity, Provability, RowKind
from cvopt.domain.jd import JobDescription
from cvopt.domain.scoring import ScoreResult


class ReportRow(Model):
    row: int
    kind: RowKind
    claim: str
    fidelity: Fidelity | None
    provability: Provability | None
    impact: float
    requirements: list[str]
    source: str


class Report(Model):
    """report.json (P1 §9.3)."""

    title: str
    company: str
    created: date
    total: float
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
) -> tuple[Report, dict[str, int]]:
    """The report and the row number of every element that has a row."""
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
                        row=0,
                        kind=RowKind.IN_CV,
                        claim=e.text,
                        fidelity=info.fidelity,
                        provability=info.provability,
                        impact=impact,
                        requirements=wins.get(e.id, []),
                        source=info.source,
                    ),
                )
            )
        elif impact > 0:
            pending.append(
                (
                    e.id,
                    ReportRow(
                        row=0,
                        kind=RowKind.IN_CV,
                        claim=e.text,
                        fidelity=Fidelity.UNMODIFIED,
                        provability=fact_provability.get(e.id, Provability.MEDIUM),
                        impact=impact,
                        requirements=wins.get(e.id, []),
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
    return buf.getvalue()
