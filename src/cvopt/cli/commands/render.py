"""cvopt render (F7)."""

from pathlib import Path
from typing import Annotated

import typer

from cvopt.bootstrap import Container
from cvopt.cli.common import console, run_async
from cvopt.context import UserContext


def render(
    ctx: typer.Context,
    cv: Annotated[Path, typer.Argument(help="A JSON Resume file.")],
    annotated: Annotated[bool, typer.Option(help="Mark statements and append the report.")] = False,
    annotations: Annotated[Path | None, typer.Option(help="annotations.json")] = None,
    report: Annotated[Path | None, typer.Option(help="report.json")] = None,
    out: Annotated[Path | None, typer.Option("-o", "--out", help="Output PDF.")] = None,
) -> None:
    """Render a CV to PDF, clean or annotated."""
    if annotated:  # default to the run folder's files next to the CV
        annotations = annotations or cv.parent / "annotations.json"
        report = report or cv.parent / "report.json"
    target = out or cv.with_name("cv-annotated.pdf" if annotated else "cv.pdf")

    async def op(c: Container, u: UserContext) -> int:
        res = c.render.render(
            cv,
            target,
            annotations_path=annotations if annotated else None,
            report_path=report if annotated else None,
        )
        return res.pages

    pages = run_async(ctx, op, writes=False)
    console.print(f"Wrote {target} ({pages} page{'s' if pages != 1 else ''}).", highlight=False)
