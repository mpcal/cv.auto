"""cvopt score (F5)."""

from datetime import date, datetime
from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from cvopt.cli.common import console, print_json, run_async
from cvopt.domain.report import report_csv
from cvopt.services.render import load_annotations
from cvopt.services.scoring import standalone_report


def score(
    ctx: typer.Context,
    cv: Annotated[Path, typer.Argument(help="A JSON Resume file.")],
    requirements: Annotated[Path, typer.Option(help="requirements.json of a run.")],
    as_of: Annotated[
        datetime | None, typer.Option(formats=["%Y-%m-%d"], help="Date for recency.")
    ] = None,
    annotations: Annotated[Path | None, typer.Option(help="annotations.json of the CV.")] = None,
    report: Annotated[
        bool, typer.Option(help="Write score-report.json and .csv next to the CV.")
    ] = False,
    as_json: Annotated[
        bool, typer.Option("--json", help="Print the score result as JSON.")
    ] = False,
) -> None:
    """Score a CV against a requirement list."""
    day: date | None = as_of.date() if as_of else None
    with console.status("Scoring…"):
        s = run_async(ctx, lambda c, u: c.scoring.score(u, cv, requirements, day), writes=True)
    if report:
        settings = ctx.obj.settings
        notes = load_annotations(annotations) if annotations else None
        rep = standalone_report(s, notes, settings.report.timeline_gap_months)
        (cv.parent / "score-report.json").write_text(
            rep.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (cv.parent / "score-report.csv").write_text(report_csv(rep), encoding="utf-8")
        console.print(f"Wrote {cv.parent / 'score-report.json'} and .csv", highlight=False)
    if as_json:
        print_json(s.result.model_dump(mode="json"))
        return
    texts = {e.id: e.text for e in s.elements}
    reqs = {r.id: r for r in s.jd.requirements}
    console.print(
        f"Score [bold]{s.result.total:.1f}[/bold] / 100 against {s.jd.title} at "
        f"{s.jd.company} (as of {s.as_of})",
        highlight=False,
    )
    table = Table(title="Requirements")
    for col in ("id", "importance", "coverage", "gap pp", "requirement", "won by"):
        table.add_column(col)
    for r in s.result.requirements:
        req = reqs[r.requirement_id]
        winners = "; ".join(texts[i] for i in r.winners)
        table.add_row(
            r.requirement_id,
            req.importance.value,
            f"{r.coverage:.2f}",
            f"{r.gap_points:.1f}",
            req.text,
            winners,
        )
    console.print(table)
    top = sorted(s.result.impacts.items(), key=lambda kv: (-kv[1], kv[0]))
    impacts = Table(title="Impact per statement")
    impacts.add_column("pp", justify="right")
    impacts.add_column("element")
    for eid, pp in top:
        impacts.add_row(f"{pp:.1f}", f"{texts[eid]}  [dim]{eid}[/dim]")
    console.print(impacts)
