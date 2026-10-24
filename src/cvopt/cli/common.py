"""Shared CLI state, the async runner and output helpers."""

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any

import typer
from pydantic import BaseModel
from rich.console import Console
from rich.table import Table

from cvopt.bootstrap import Container, build_container
from cvopt.config import Settings
from cvopt.context import UserContext
from cvopt.errors import CvoptError, InputError

log = logging.getLogger("cvopt.cli")
console = Console()


@dataclass(frozen=True)
class AppState:
    settings: Settings
    user_or_none: UserContext | None

    @property
    def user(self) -> UserContext:
        if self.user_or_none is None:
            raise InputError("No user given: pass --user, set CVOPT_USER or app.default_user.")
        return self.user_or_none


def fail(err: CvoptError) -> typer.Exit:
    console.print(f"[red]{err}[/red]", highlight=False)
    return typer.Exit(err.exit_code)


def run_async[T](
    ctx: typer.Context, op: Callable[[Container, UserContext], Awaitable[T]], *, writes: bool
) -> T:
    state: AppState = ctx.obj

    async def main() -> T:
        user = state.user
        c = await build_container(state.settings)
        try:
            if writes:
                with c.files.lock(user):  # one writer per user
                    return await op(c, user)
            return await op(c, user)
        finally:
            await c.engine.dispose()

    try:
        return asyncio.run(main())
    except CvoptError as err:
        raise fail(err) from err
    except Exception as err:
        log.exception("internal error")
        console.print("[red]Internal error; see the log.[/red]")
        raise typer.Exit(4) from err


def short(id_: str | None) -> str:
    return id_[-8:] if id_ else ""


def print_json(data: Any) -> None:
    typer.echo(json.dumps(data, indent=2, ensure_ascii=False, default=str))


def print_records(
    title: str, records: Sequence[BaseModel], columns: Sequence[str], *, as_json: bool
) -> None:
    """Rich table with ids shortened, or the full records as JSON."""
    if as_json:
        print_json([r.model_dump(mode="json") for r in records])
        return
    table = Table(title=title)
    for col in columns:
        table.add_column(col)
    for r in records:
        row = r.model_dump(mode="json")
        cells = [
            short(row[c]) if c == "id" or c.endswith("_id") else _cell(row.get(c)) for c in columns
        ]
        table.add_row(*cells)
    console.print(table)


def _cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, list):
        return ", ".join(map(str, value))
    return str(value)


def split_list(value: str | None) -> list[str] | None:
    return None if value is None else [v.strip() for v in value.split(",") if v.strip()]


def end_month(value: str | None) -> dict[str, Any]:
    """`--end current` means an open position."""
    if value is None:
        return {}
    return {"end_month": None if value.strip().lower() == "current" else value}


def given(**fields: Any) -> dict[str, Any]:
    return {k: v for k, v in fields.items() if v is not None}
