"""Run commands: new (F3), generate (F4), go (both)."""

from pathlib import Path
from typing import Annotated

import typer
from rich.table import Table

from cvopt.bootstrap import Container
from cvopt.cli.common import console, print_json, run_async, stderr_console
from cvopt.context import UserContext
from cvopt.services.discovery import RunSummary

run_app = typer.Typer(help="Create and generate runs.", no_args_is_help=True)

Url = Annotated[str | None, typer.Option(help="JD web page.")]
Pdf = Annotated[Path | None, typer.Option(help="JD as a PDF.")]
Text = Annotated[Path | None, typer.Option(help="JD as a text file.")]


def _new(ctx: typer.Context, url: str | None, pdf: Path | None, text: Path | None) -> str:
    with stderr_console.status("Reading the job description…"):
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
    with stderr_console.status("Generating… (many model calls the first time)"):
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


JsonOpt = Annotated[bool, typer.Option("--json", help="Print JSON.")]
OutOpt = Annotated[Path | None, typer.Option("--out", help="Also save JSON to this path.")]


def _discovered(
    rows: list[RunSummary], as_json: bool, out: Path | None, *, single: bool = False
) -> None:
    data = [r.model_dump(mode="json") for r in rows]
    value = data[0] if single else data
    if out is not None:
        import json

        out.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        stderr_console.print(f"Wrote {out}", highlight=False)
    if as_json:
        print_json(value)
        return
    table = Table()
    for col in ("run", "publication", "last attempt", "published at", "target", "profile"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            r.name,
            r.publication_status,
            str(r.last_attempt["status"]) if r.last_attempt else "unavailable",
            r.published_at or "",
            str(r.target_score) if r.target_score is not None else "",
            str(r.profile_supported_score) if r.profile_supported_score is not None else "",
        )
    console.print(table)
    if single:
        for name, path in rows[0].artifacts.items():
            console.print(f"{name}: {path}", highlight=False)
        usage = rows[0].usage
        operation = usage.get("operation", {}) if usage else {}
        total = operation.get("total_usd")
        console.print(
            f"New generation model cost (USD): {total if total is not None else 'unavailable'}"
        )
        console.print(f"Attempt diagnostics: {rows[0].attempt_diagnostics}")


@run_app.command("list")
def run_list(ctx: typer.Context, as_json: JsonOpt = False, out: OutOpt = None) -> None:
    """List your runs, their publications and latest attempts."""

    async def read(c: Container, u: UserContext) -> list[RunSummary]:
        return c.runs.list(u)

    _discovered(run_async(ctx, read, writes=False), as_json, out)


@run_app.command("show")
def run_show(ctx: typer.Context, run: str, as_json: JsonOpt = False, out: OutOpt = None) -> None:
    """Show a run's saved scores, usage and both PDF paths."""

    async def read(c: Container, u: UserContext) -> RunSummary:
        return c.runs.show(u, run)

    _discovered([run_async(ctx, read, writes=False)], as_json, out, single=True)


@run_app.command("compare")
def run_compare(
    ctx: typer.Context,
    runs: Annotated[list[str], typer.Argument(help="At least two distinct owned runs.")],
    as_of: Annotated[
        str | None, typer.Option(help="Common profile date (YYYY-MM-DD); default today.")
    ] = None,
    as_json: JsonOpt = False,
    out: Annotated[
        Path | None, typer.Option(help="Export comparison.json and comparison.csv here.")
    ] = None,
) -> None:
    """Rank roles by current recorded profile support; retain saved target scores."""
    from datetime import date

    from cvopt.domain.comparison import Comparison, comparison_csv
    from cvopt.errors import InputError

    try:
        common_date = date.fromisoformat(as_of) if as_of else None
    except ValueError as err:
        from cvopt.cli.common import fail

        raise fail(InputError("--as-of must be a date in YYYY-MM-DD format.")) from err

    async def compare(c: Container, user: UserContext) -> Comparison:
        return await c.comparison.compare(user, runs, as_of=common_date)

    result = run_async(ctx, compare, writes=True)
    if out is not None:
        out.mkdir(parents=True, exist_ok=True)
        (out / "comparison.json").write_text(
            result.model_dump_json(indent=2) + "\n", encoding="utf-8"
        )
        (out / "comparison.csv").write_text(comparison_csv(result), encoding="utf-8")
        stderr_console.print(f"Wrote comparison.json and comparison.csv to {out}", highlight=False)
    if as_json:
        print_json(result.model_dump(mode="json"))
        return
    table = Table(title=f"Current recorded profile support as of {result.as_of}")
    for column in (
        "run",
        "role",
        "supported /100",
        "must gap pp",
        "saved target",
        "generated",
        "stale",
    ):
        table.add_column(column)
    for role in result.roles:
        target = role.saved_target
        stale = ", ".join(
            f"{k}:{'unknown' if v is None else 'yes' if v else 'no'}"
            for k, v in target.stale.items()
        )
        table.add_row(
            role.name,
            f"{role.title} at {role.company}",
            f"{role.profile_supported_score:.2f}",
            f"{role.must_gap_points:.2f}",
            str(target.score) if target.score is not None else "unavailable",
            target.generated_at or "unavailable",
            stale,
        )
    console.print(table)
    console.print(result.uncertainty, highlight=False)
    console.print(result.gap_units, highlight=False)
    for gap in result.recurring_gaps:
        console.print(
            f"{gap.requirement.text}: {gap.weighted_missing_points:.2f} "
            f"summed missing pp across {gap.role_count} roles",
            highlight=False,
        )
    cost = result.usage.get("total_usd")
    console.print(f"Comparison model cost (USD): {cost if cost is not None else 'unavailable'}")
