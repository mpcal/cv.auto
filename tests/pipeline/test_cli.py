"""The interactive review through the CLI: accept, reject with dependents, edit, skip,
quit and resume, driven by scripted terminal input."""

import asyncio
import json
from pathlib import Path

import pytest
from typer.testing import CliRunner

from cvopt.adapters.db.repository import UserRepository
from cvopt.bootstrap import Container
from cvopt.cli.main import app
from cvopt.config import Settings
from cvopt.context import UserContext
from cvopt.domain.enums import (
    ClaimType,
    Decision,
    DocumentKind,
    EvidenceKind,
    ProposalAction,
    RecordType,
)
from cvopt.domain.review import (
    CreateClaim,
    CreatePosition,
    EvidenceDraft,
    LinkFactSource,
    Note,
    Payload,
)
from tests.conftest import TEST_NOW
from tests.scenarios import CV

runner = CliRunner()


def cli(settings: Settings, *args: str, input: str = "", code: int = 0) -> str:
    result = runner.invoke(
        app,
        ["--user", "alice@example.com", "--data-root", str(settings.app.data_root), *args],
        input=input,
    )
    assert result.exit_code == code, result.output
    return result.output


async def acli(settings: Settings, *args: str, input: str = "", code: int = 0) -> str:
    """The CLI runs its own event loop, so async tests call it from a worker thread."""
    return await asyncio.to_thread(cli, settings, *args, input=input, code=code)


async def test_review_accept_reject_edit_skip_quit_resume(
    golden: Container, settings: Settings, alice: UserContext
) -> None:
    res = await golden.ingestion.ingest_document(alice, CV, DocumentKind.CV)
    assert res.batch_id
    items = await golden.ingestion.list_items(alice, res.batch_id)
    first, second = items[0], items[1]
    assert second.action is ProposalAction.SET_PROFILE_FIELD
    position = next(i for i in items if i.action is ProposalAction.CREATE_POSITION)
    dependents = [
        i for i in items if getattr(i.payload, "position_id", None) == f"$item:{position.seq}"
    ]
    assert dependents and first.seq == 1 and position.seq > 4

    # Session 1: accept #1, edit #2 (keep the field name, change the value), skip #3, quit.
    out = await acli(settings, "review", res.batch_id, input="a\ne\n\nEdited Value\ns\nq\n")
    assert "Item 1 of" in out and "Resume with: cvopt review" in out
    decided = {i.seq: i for i in await golden.ingestion.list_items(alice, res.batch_id)}
    assert decided[1].decision is Decision.ACCEPT
    assert decided[2].decision is Decision.ACCEPT
    assert decided[2].payload.value == "Edited Value"  # type: ignore[union-attr]
    assert decided[3].decision is decided[4].decision is Decision.PENDING

    # Session 2 (resume): the skipped #3 comes first; accept up to the position, reject it
    # (its claims go with it), then accept all remaining and apply.
    n_before = position.seq - 3  # pending items #3 .. #position-1
    script = "a\n" * n_before + "r\n" + "A\ny\n" + "y\n"
    out = await acli(settings, "review", res.batch_id[-8:], input=script)
    assert "Item 1 of" in out and f"#{3}" in out
    assert out.count("also rejected") == len(dependents)
    assert "Applied" in out

    profile = json.loads(await acli(settings, "profile", "show", "--json"))
    assert second.payload.field and profile[second.payload.field] == "Edited Value"  # type: ignore[union-attr]
    positions = json.loads(await acli(settings, "position", "list", "--json"))
    assert len(positions) == 2
    assert position.payload.employer not in {p["employer"] for p in positions}  # type: ignore[union-attr]
    claims = json.loads(await acli(settings, "claim", "list", "--json"))
    texts = {c["text"] for c in claims}
    assert not texts & {d.payload.text for d in dependents}  # type: ignore[union-attr]
    batches = json.loads(await acli(settings, "review", "list", "--json"))
    assert [b["status"] for b in batches] == ["applied"]


@pytest.fixture
async def note_batch(container: Container, alice: UserContext) -> str:
    repo = container.repos.for_user(alice)
    assert isinstance(repo, UserRepository)
    doc = await repo.insert_document(
        {
            "kind": DocumentKind.OTHER,
            "filename": "n.txt",
            "sha256": "ee" * 32,
            "mime_type": "text/plain",
            "received_at": TEST_NOW,
        }
    )
    batch = await repo.insert_batch(doc.id)
    for seq in (1, 2):
        await repo.insert_item(batch.id, seq, Note(text=f"note {seq}"))
    return batch.id


async def test_skipped_items_come_round_again_and_apply_can_wait(
    note_batch: str, settings: Settings, container: Container, alice: UserContext
) -> None:
    out = await acli(settings, "review", note_batch, input="s\na\na\nn\n")
    assert out.count("note 1") == 2  # shown, skipped, shown again
    assert "Batch left open" in out
    out = await acli(settings, "review", note_batch, input="y\n")  # nothing pending: apply
    assert "Applied 2 item(s)" in out
    await acli(settings, "review", note_batch, code=1)  # closed now


async def test_review_shows_titles_for_ids(
    settings: Settings, container: Container, alice: UserContext
) -> None:
    repo = container.repos.for_user(alice)
    stored = await repo.insert_position(
        {"employer": "Vontobel", "title": "Analyst", "start_month": "2019-03", "end_month": None}
    )
    doc = await repo.insert_document(
        {
            "kind": DocumentKind.REFERENCE_LETTER,
            "filename": "r.txt",
            "sha256": "ff" * 32,
            "mime_type": "text/plain",
            "received_at": TEST_NOW,
        }
    )
    batch = await repo.insert_batch(doc.id)
    new = CreatePosition(employer="Acme", title="Lead", start_month="2023-01", end_month=None)
    claim = CreateClaim(
        type=ClaimType.SKILL,
        text="Python",
        text_lang="en",
        position_id="$item:2",
        keywords=[],
        evidence=EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER),
    )
    link = LinkFactSource(record_type=RecordType.POSITION, record_id=stored.id)
    payloads: list[Payload] = [link, new, claim]
    for seq, payload in enumerate(payloads, start=1):
        await repo.insert_item(batch.id, seq, payload)
    out = await acli(settings, "review", batch.id, input="s\ns\nq\n")
    assert "Analyst · Vontobel (2019-03 – current)" in out and stored.id in out
    assert "Lead · Acme (2023-01 – current) (new, item #2)  $item:2" in out


def test_review_needs_a_known_batch(settings: Settings) -> None:
    cli(settings, "review", "nosuchbatch", code=1)
    cli(settings, "review", "discard", code=2)


def test_ingest_unknown_file_type(settings: Settings, tmp_path: Path) -> None:
    f = tmp_path / "x.xls"
    f.write_bytes(b"x")
    assert "Unsupported file type" in cli(settings, "ingest", str(f), "--kind", "cv", code=1)
