"""Ingestion commands (F1): ingest and review."""

from pathlib import Path
from typing import Annotated

import typer
from rich.prompt import Confirm
from rich.table import Table

from cvopt.bootstrap import Container
from cvopt.cli.common import console, print_json, run_async, short
from cvopt.cli.review import review_batch
from cvopt.context import UserContext
from cvopt.domain.enums import BatchStatus, Decision, DocumentKind


def ingest(
    ctx: typer.Context,
    file: Annotated[Path, typer.Argument(help="PDF, DOCX, image or text file.")],
    kind: Annotated[DocumentKind, typer.Option(help="What the document is.")],
    accept_all: Annotated[bool, typer.Option(help="Accept and apply without prompts.")] = False,
) -> None:
    """Ingest a document and review what it proposes."""

    async def op(c: Container, u: UserContext) -> None:
        with console.status(f"Reading {file.name}…"):
            res = await c.ingestion.ingest_document(u, file, kind)
        if res.duplicate_of:
            console.print(
                f"{file.name} was already ingested as document {short(res.duplicate_of)}; skipped."
            )
            open_batch = next(
                (
                    b
                    for b in await c.ingestion.list_batches(u, BatchStatus.OPEN)
                    if b.document_id == res.duplicate_of
                ),
                None,
            )
            if open_batch and (
                accept_all or Confirm.ask("Its review is unfinished. Resume?", default=True)
            ):
                await review_batch(c, u, open_batch.id, accept_all)
            elif open_batch:
                console.print(f"Resume later with: cvopt review {short(open_batch.id)}")
            return
        console.print(f"Stored document {short(res.document_id)}.")
        if not res.batch_id:
            console.print("Nothing to review.")
            return
        summary = ", ".join(f"{n} {action}" for action, n in res.counts.items())
        console.print(f"Review batch {short(res.batch_id)}: {summary}.")
        if accept_all or Confirm.ask("Review now?", default=True):
            await review_batch(c, u, res.batch_id, accept_all)
        else:
            console.print(f"Review later with: cvopt review {short(res.batch_id)}")

    run_async(ctx, op, writes=True)


def review(
    ctx: typer.Context,
    target: Annotated[str, typer.Argument(help="'list', 'discard', or a batch id.")] = "list",
    batch: Annotated[str | None, typer.Argument(help="Batch id for 'discard'.")] = None,
    accept_all: Annotated[bool, typer.Option(help="Accept and apply without prompts.")] = False,
    status: Annotated[BatchStatus | None, typer.Option(help="Filter for 'list'.")] = None,
    as_json: Annotated[bool, typer.Option("--json", help="JSON output for 'list'.")] = False,
) -> None:
    """cvopt review list | BATCH [--accept-all] | discard BATCH"""
    if target == "list":
        _list(ctx, status, as_json)
    elif target == "discard":
        if not batch:
            raise typer.BadParameter("discard needs a batch id")
        b = run_async(ctx, lambda c, u: c.ingestion.discard_batch(u, batch), writes=True)
        console.print(f"Discarded batch {short(b.id)}.")
    else:
        run_async(ctx, lambda c, u: review_batch(c, u, target, accept_all), writes=True)


def _list(ctx: typer.Context, status: BatchStatus | None, as_json: bool) -> None:
    async def op(c: Container, u: UserContext) -> list[dict[str, object]]:
        rows = []
        for b in await c.ingestion.list_batches(u, status):
            items = await c.ingestion.list_items(u, b.id)
            doc = next((d for d in await c.data.list_documents(u) if d.id == b.document_id), None)
            rows.append(
                {
                    **b.model_dump(mode="json"),
                    "document": doc.filename if doc else "",
                    "items": len(items),
                    "pending": sum(i.decision is Decision.PENDING for i in items),
                }
            )
        return rows

    rows = run_async(ctx, op, writes=False)
    if as_json:
        print_json(rows)
        return
    table = Table(title="Review batches")
    for col in ("id", "document", "status", "items", "pending"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            short(str(r["id"])),
            str(r["document"]),
            str(r["status"]),
            str(r["items"]),
            str(r["pending"]),
        )
    console.print(table)
