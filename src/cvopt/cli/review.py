"""Interactive review of a batch, item by item (P2 §8.3)."""

import json
from typing import Any

from pydantic import BaseModel, ValidationError
from rich.panel import Panel
from rich.prompt import Confirm, Prompt
from rich.table import Table

from cvopt.bootstrap import Container
from cvopt.cli.common import console, short
from cvopt.context import UserContext
from cvopt.domain.enums import BatchStatus, Decision
from cvopt.domain.review import ProposalItem
from cvopt.errors import ConflictError

CHOICES = ["a", "r", "e", "s", "A", "q"]
HELP = "[a]ccept  [r]eject  [e]dit  [s]kip  [A]ccept all remaining  [q]uit"


def _fmt(value: Any) -> str:
    if isinstance(value, list):
        return ", ".join(map(str, value))
    return "" if value is None else str(value)


def show_item(item: ProposalItem, n: int, total: int) -> None:
    table = Table.grid(padding=(0, 2))
    data = item.payload.model_dump(mode="json", exclude={"action"})
    evidence = data.pop("evidence", None)
    for key, value in data.items():
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


async def walk(c: Container, user: UserContext, batch_id: str, pending: list[ProposalItem]) -> str:
    for n, item in enumerate(pending, start=1):
        show_item(item, n, len(pending))
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
            return await walk(c, user, batch_id, pending[n - 1 :])
        elif choice == "q":
            console.print(f"Stopped. Resume with: cvopt review {short(batch_id)}")
            return "quit"
        # "s" leaves the item pending; it comes round again after the others
    return "again"


async def review_batch(c: Container, user: UserContext, batch_ref: str, accept_all: bool) -> None:
    batch = await c.ingestion.get_batch(user, batch_ref)
    if batch.status is not BatchStatus.OPEN:
        raise ConflictError(f"Batch {short(batch.id)} is {batch.status.value}; nothing to review.")
    while pending := [
        i for i in await c.ingestion.list_items(user, batch.id) if i.decision is Decision.PENDING
    ]:
        if accept_all:
            await c.ingestion.accept_all(user, batch.id)
            break
        if await walk(c, user, batch.id, pending) == "quit":
            return
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
