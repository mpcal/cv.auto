"""Typer app, global options and error mapping."""

import logging
import os
from pathlib import Path
from typing import Annotated

import typer

from cvopt import __version__
from cvopt.cli.commands.account import delete_user, export
from cvopt.cli.commands.data import APPS
from cvopt.cli.commands.ingest import ingest, review
from cvopt.cli.commands.render import render
from cvopt.cli.commands.run import run_app
from cvopt.cli.commands.score import score
from cvopt.cli.common import AppState, console, fail
from cvopt.config import load_settings
from cvopt.context import UserContext
from cvopt.errors import CvoptError
from cvopt.logging import setup_logging

log = logging.getLogger(__name__)
app = typer.Typer(no_args_is_help=True, add_completion=False)
for name, sub in APPS.items():
    app.add_typer(sub, name=name)
app.command()(ingest)
app.command()(review)
app.add_typer(run_app, name="run")
app.command()(score)
app.command()(render)
app.command()(export)
app.command("delete-user")(delete_user)


@app.callback()
def main(
    ctx: typer.Context,
    user: Annotated[str | None, typer.Option(help="User email.")] = None,
    data_root: Annotated[Path | None, typer.Option(help="Data directory.")] = None,
    config: Annotated[Path | None, typer.Option(help="Config file.")] = None,
    debug: Annotated[bool, typer.Option(help="Log at DEBUG level.")] = False,
) -> None:
    try:
        settings = load_settings(config, data_root)
        email = user or os.environ.get("CVOPT_USER") or settings.app.default_user
        state = AppState(settings, UserContext.from_email(email) if email else None)
    except CvoptError as err:
        raise fail(err) from err
    setup_logging(
        settings.app.data_root / "cvopt.log", "DEBUG" if debug else settings.app.log_level
    )
    log.debug("cli start", extra={"command": ctx.invoked_subcommand})
    ctx.obj = state


@app.command()
def version() -> None:
    """Print the cvopt version."""
    console.print(f"cvopt {__version__}")
