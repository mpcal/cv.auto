"""Account commands (F8): export and delete-user."""

from datetime import date
from pathlib import Path
from typing import Annotated

import typer

from cvopt.cli.common import console, run_async


def export(
    ctx: typer.Context,
    out: Annotated[Path | None, typer.Option("-o", "--out", help="Archive path (.zip).")] = None,
) -> None:
    """Export all your records, documents and runs into one zip archive."""
    target = out or Path(f"cvopt-export-{date.today().isoformat()}.zip")
    path = run_async(ctx, lambda c, u: c.account.export_user(u, target), writes=False)
    console.print(f"Wrote {path}", highlight=False)


def delete_user(
    ctx: typer.Context,
    confirm: Annotated[str, typer.Option(help="Your email, typed again.")],
) -> None:
    """Delete all your records, documents and runs. This cannot be undone."""
    run_async(ctx, lambda c, u: c.account.delete_user(u, confirm), writes=True)
    console.print("All data of this user was deleted.")
