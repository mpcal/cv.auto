"""Interactive review of a batch, item by item (P2 §8.3)."""

import json
from typing import Any

import typer
from pydantic import BaseModel, ValidationError
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from cvopt.bootstrap import Container
from cvopt.cli.common import console, short
from cvopt.context import UserContext
from cvopt.domain.enums import BatchStatus, Decision
from cvopt.domain.records import Certificate, Claim, Education, Language, Position
from cvopt.domain.review import (
    CreateCertificate,
    CreateClaim,
    CreateEducation,
    CreateLanguage,
    CreatePosition,
    ProposalItem,
)
from cvopt.errors import ConflictError

CHOICES = ["a", "r", "e", "s", "A", "q"]
HELP = "[a]ccept  [r]eject  [e]dit  [s]kip  [A]ccept all remaining  [q]uit"


def _fmt(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(map(str, value))
    return "" if value is None else str(value)


def _label(record: BaseModel) -> str:
    """A human-readable title for a stored record or the record an item proposes."""
    match record:
        case Position() | CreatePosition():
            end = record.end_month or "current"
            return f"{record.title} · {record.employer} ({record.start_month} – {end})"
        case Education() | CreateEducation():
            degree = record.degree_type or record.degree_level.value
            return " · ".join(filter(None, [degree, record.area, record.institution]))
        case Language() | CreateLanguage():
            return f"{record.language_code} {record.level}"
        case Certificate() | CreateCertificate():
            return " · ".join(filter(None, [record.name, record.issuer]))
        case Claim() | CreateClaim():
            return record.text if len(record.text) <= 80 else record.text[:79] + "…"
    return ""


async def record_labels(
    c: Container, user: UserContext, items: list[ProposalItem]
) -> dict[str, str]:
    """Labels for the ids a payload may name: stored records and `$item:<seq>` references."""
    records: list[BaseModel] = [
        *await c.data.list_positions(user),
        *await c.data.list_education(user),
        *await c.data.list_languages(user),
        *await c.data.list_certificates(user),
        *[cw.claim for cw in await c.data.list_claims(user)],
    ]
    labels = {r.id: _label(r) for r in records}  # type: ignore[attr-defined]
    for i in items:
        if label := _label(i.payload):
            labels[f"$item:{i.seq}"] = f"{label} (new, item #{i.seq})"
    return labels


def show_item(item: ProposalItem, n: int, total: int, labels: dict[str, str]) -> None:
    table = Table.grid(padding=(0, 2))
    data = item.payload.model_dump(mode="json", exclude={"action"})
    evidence = data.pop("evidence", None)
    for key, value in data.items():
        if key.endswith("_id") and value in labels:
            table.add_row(f"[bold]{key}[/bold]", f"{labels[value]}  [dim]{value}[/dim]")
        else:
            table.add_row(f"[bold]{key}[/bold]", _fmt(value))
    if evidence:
        table.add_row("[bold]evidence[/bold]", evidence["kind"])
        if evidence.get("excerpt"):
            table.add_row("[bold]excerpt[/bold]", f"[italic]{evidence['excerpt']}[/italic]")
    title = f"Item {n} of {total} · #{item.seq} · {item.action.value}"
    console.print(Panel(table, title=title, title_align="left"))


def _editable(model: BaseModel, prefix: str = "") -> list[tuple[str, Any]]:
    """Fields in order, skipping the action and ids other than position_id."""
    fields: list[tuple[str, Any]] = []
    for name in type(model).model_fields:
        value = getattr(model, name)
        if name == "action" or (name.endswith("_id") and name != "position_id"):
            continue
        if isinstance(value, BaseModel):
            fields += _editable(value, f"{prefix}{name}.")
        else:
            fields.append((f"{prefix}{name}", value))
    return fields


def _set(data: dict[str, Any], dotted: str, value: Any) -> None:
    *path, last = dotted.split(".")
    for key in path:
        data = data[key]
    data[last] = value


def _parse(raw: str, current: Any) -> Any:
    if isinstance(current, list):
        return [v.strip() for v in raw.split(",") if v.strip()]
    if raw.strip().lower() == "none":
        return None
    return raw


def edit_payload(item: ProposalItem) -> dict[str, Any]:
    """Asks for each field in turn; Enter keeps it. Each value is validated at once."""
    model = item.payload
    data = model.model_dump(mode="json")
    console.print("Edit: Enter keeps a value; lists are comma-separated; 'none' clears.")
    for name, current in _editable(model):
        while True:
            raw = Prompt.ask(name, default=_fmt(current), show_default=True)
            candidate = json.loads(json.dumps(data))
            _set(candidate, name, _parse(raw, current))
            try:
                type(model).model_validate(candidate)
            except ValidationError as err:
                console.print(f"[red]{err.errors()[0]['msg']}[/red]")
                continue
            data = candidate
            break
    return data


async def walk(
    c: Container,
    user: UserContext,
    batch_id: str,
    pending: list[ProposalItem],
    labels: dict[str, str],
) -> str:
    for n, item in enumerate(pending, start=1):
        show_item(item, n, len(pending), labels)
        console.print(HELP, markup=False)
        choice = Prompt.ask("Decision", choices=CHOICES, default="a", case_sensitive=True)
        if choice == "a":
            await c.ingestion.decide_item(user, batch_id, item.seq, Decision.ACCEPT)
        elif choice == "r":
            res = await c.ingestion.decide_item(user, batch_id, item.seq, Decision.REJECT)
            for dep in res.rejected_dependents:
                console.print(f"  also rejected #{dep.seq} {dep.action.value} (depends on it)")
        elif choice == "e":
            payload = edit_payload(item)
            await c.ingestion.decide_item(user, batch_id, item.seq, Decision.ACCEPT, payload)
        elif choice == "A":
            remaining = len(pending) - n + 1
            if Confirm.ask(f"Accept all {remaining} remaining item(s)?"):
                await c.ingestion.accept_all(user, batch_id)
                return "again"
            return await walk(c, user, batch_id, pending[n - 1 :], labels)
        elif choice == "q":
            console.print(f"Stopped. Resume with: cvopt review {short(batch_id)}")
            return "quit"
        # "s" leaves the item pending; it comes round again after the others
    return "again"


async def review_batch(c: Container, user: UserContext, batch_ref: str, accept_all: bool) -> None:
    batch = await c.ingestion.get_batch(user, batch_ref)
    if batch.status is not BatchStatus.OPEN:
        raise ConflictError(f"Batch {short(batch.id)} is {batch.status.value}; nothing to review.")
    labels = await record_labels(c, user, await c.ingestion.list_items(user, batch.id))
    while pending := [
        i for i in await c.ingestion.list_items(user, batch.id) if i.decision is Decision.PENDING
    ]:
        if accept_all:
            await c.ingestion.accept_all(user, batch.id)
            break
        try:
            if await walk(c, user, batch.id, pending, labels) == "quit":
                return
        except KeyboardInterrupt:
            console.print(f"\nInterrupted. Resume with: cvopt review {short(batch.id)}")
            raise typer.Exit(130) from None
    items = await c.ingestion.list_items(user, batch.id)
    accepted = sum(i.decision is Decision.ACCEPT for i in items)
    console.print(
        f"Batch {short(batch.id)}: {accepted} accepted, {len(items) - accepted} rejected."
    )
    if accept_all or Confirm.ask("Apply now?", default=True):
        res = await c.ingestion.apply_batch(user, batch.id)
        console.print(f"Applied {res.applied} item(s); {res.rejected} not applied.")
    else:
        console.print(f"Batch left open. Apply later with: cvopt review {short(batch.id)}")
