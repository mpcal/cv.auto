"""Run commands: new (F3), generate (F4), go (both)."""

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from cvopt.cli.common import console, run_async

run_app = typer.Typer(help="Create and generate runs.", no_args_is_help=True)

Url = Annotated[str | None, typer.Option(help="JD web page.")]
Pdf = Annotated[Path | None, typer.Option(help="JD as a PDF.")]
Text = Annotated[Path | None, typer.Option(help="JD as a text file.")]


def _new(ctx: typer.Context, url: str | None, pdf: Path | None, text: Path | None) -> str:
    with console.status("Reading the job description…"):
        res = run_async(
            ctx, lambda c, u: c.jd.create_run(u, url=url, pdf=pdf, text=text), writes=True
        )
    console.print(
        f"Run [bold]{res.name}[/bold]: {res.jd.title} at {res.jd.company} ({res.jd.language})",
        highlight=False,
    )
    table = Table()
    for col in ("id", "importance", "category", "requirement"):
        table.add_column(col)
    for r in res.jd.requirements:
        table.add_row(r.id, r.importance.value, r.category.value, r.text)
    console.print(table)
    for item in res.jd.logistics:
        console.print(f"Logistics: {item}", highlight=False)
    console.print(f"Edit {res.path / 'requirements.json'} to adjust, if needed.", highlight=False)
    return res.name


@run_app.command("new")
def run_new(ctx: typer.Context, url: Url = None, pdf: Pdf = None, text: Text = None) -> None:
    """Read a JD and create a run folder with its requirements."""
    _new(ctx, url, pdf, text)


@run_app.command("generate")
def run_generate(
    ctx: typer.Context, run: Annotated[str, typer.Argument(help="Run folder or prefix.")]
) -> None:
    """Generate the optimized CV, report and annotated PDF for a run."""
    with console.status("Generating… (many model calls the first time)"):
        res = run_async(ctx, lambda c, u: c.generation.generate(u, run), writes=True)
    console.print(
        f"Score [bold]{res.score:.1f}[/bold] / 100. Files in {res.run_dir}:", highlight=False
    )
    for path in res.files:
        console.print(f"  {path.name}", highlight=False)


@run_app.command("go")
def run_go(ctx: typer.Context, url: Url = None, pdf: Pdf = None, text: Text = None) -> None:
    """run new, then run generate."""
    run_generate(ctx, _new(ctx, url, pdf, text))
