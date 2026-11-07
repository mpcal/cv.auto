"""Review provenance survives upgrade/export and cannot become active source proof."""

import json
import sqlite3
import zipfile

import pytest
from alembic import command
from sqlalchemy import MetaData, create_engine, insert

from cvopt.adapters.db.engine import alembic_config
from cvopt.domain.assertions import claim_assertion
from cvopt.domain.enums import Decision, EvidenceKind, RecordType
from cvopt.domain.review import AddEvidence, EvidenceDraft, ReviewConflict
from cvopt.errors import InputError, NotFoundError
from tests.pipeline.test_review_safety import batch_with, claim


def test_upgrade_keeps_legacy_proposals_unknown_and_preserves_review_history(tmp_path):
    path = tmp_path / "legacy.sqlite"
    cfg = alembic_config(f"sqlite:///{path}")
    command.upgrade(cfg, "0004")
    engine = create_engine(f"sqlite:///{path}")
    metadata = MetaData()
    metadata.reflect(engine)
    common = dict(user_id="alice@example.com", created_at="2026-01-01", updated_at="2026-01-01")
    payload = (
        '{"action": "add_evidence", "claim_id": "legacy-claim", "evidence": {"kind": "own_cv"}}'
    )
    with engine.begin() as conn:
        conn.execute(
            insert(metadata.tables["users"]),
            dict(user_id=common["user_id"], user_key="alice", created_at=common["created_at"]),
        )
        conn.execute(
            insert(metadata.tables["source_document"]),
            dict(
                **common,
                id="doc",
                kind="cv",
                filename="cv.txt",
                sha256="a" * 64,
                mime_type="text/plain",
                language="en",
                issuer="",
                position_id=None,
                extracted_text="Led two teams",
                received_at="2026-01-01",
            ),
        )
        conn.execute(
            insert(metadata.tables["review_batch"]),
            dict(**common, id="batch", document_id="doc", status="open", applied_at=None),
        )
        conn.execute(
            insert(metadata.tables["proposal_item"]),
            dict(
                **common,
                id="proposal",
                batch_id="batch",
                seq=1,
                action="add_evidence",
                target_id=None,
                payload=payload,
                decision="accept",
                note="previously edited",
            ),
        )
    engine.dispose()
    command.upgrade(cfg, "head")
    with sqlite3.connect(path) as conn:
        row = conn.execute(
            "SELECT payload, review_context, decision, note FROM proposal_item"
        ).fetchone()
        assert row == (payload, None, "accept", "previously edited")
        # New provenance must survive a refused downgrade, including exact stored JSON.
        conn.execute(
            "UPDATE proposal_item SET review_context = ?", ('{"origin":"legacy_unknown"}',)
        )
        conn.commit()
        before = list(conn.iterdump())
    with pytest.raises(RuntimeError, match="history"):
        command.downgrade(cfg, "0004")
    with sqlite3.connect(path) as conn:
        assert list(conn.iterdump()) == before


async def test_export_contains_decoded_original_and_renewed_review_context(
    container, alice, tmp_path
):
    c = await claim(container, alice)
    repo, batch = await batch_with(
        container,
        alice,
        AddEvidence(
            claim_id=c.id,
            evidence=EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER, excerpt="Led two teams"),
        ),
    )
    await repo.update_claim(c.id, {"text": "Managed two teams"})
    from cvopt.errors import ConflictError

    with pytest.raises(ConflictError):
        await container.ingestion.accept_all(alice, batch.id)
    await container.ingestion.decide_item(alice, batch.id, 1, Decision.ACCEPT)
    out = await container.account.export_user(alice, tmp_path / "export.zip")
    with zipfile.ZipFile(out) as archive:
        rows = json.loads(archive.read("records.json"))
    [item] = rows["proposal_item"]
    assert item["review_context"]["original_target_assertion"]["text"] == "Led two teams"
    assert item["review_context"]["target_assertion"]["text"] == "Managed two teams"
    assert item["review_context"]["original_payload"]["action"] == "add_evidence"


async def test_historical_hooks_cannot_authorize_caller_supplied_active_snapshots(container, alice):
    c = await claim(container, alice)
    repo, batch = await batch_with(container, alice)
    ev = await repo.insert_stale_evidence(
        dict(
            claim_id=c.id,
            document_id=batch.document_id,
            kind=EvidenceKind.REFERENCE_LETTER,
            excerpt="Led two teams",
            state="active",
            snapshot_origin="legacy_backfill",
        ),
        claim_assertion(c),
    )
    assert ev.state == "stale" and ev.snapshot_origin == "captured"
    assert ev.id not in {e.id for e in await repo.list_evidence(active_only=True)}
    pos = await repo.insert_position(
        dict(employer="Acme", title="Lead", start_month="2020-01", end_month=None)
    )
    source = await repo.insert_stale_fact_source(
        RecordType.POSITION, pos.id, batch.document_id, pos, excerpt="Led two teams"
    )
    assert source.state == "stale" and not await repo.list_fact_sources(active_only=True)
    assert not await repo.fact_source_documents(RecordType.POSITION, pos.id)
    with pytest.raises(InputError):
        await repo.insert_stale_evidence(
            dict(
                claim_id=c.id,
                document_id=batch.document_id,
                kind=EvidenceKind.REFERENCE_LETTER,
                excerpt="fabricated quote",
            ),
            claim_assertion(c),
        )


async def test_review_context_refresh_and_conflict_hooks_are_owned(container, alice, bob):
    c = await claim(container, alice)
    repo, batch = await batch_with(
        container,
        alice,
        AddEvidence(
            claim_id=c.id,
            evidence=EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER, excerpt="Led two teams"),
        ),
    )
    [item] = await repo.list_items(batch.id)
    other = container.repos.for_user(bob)
    with pytest.raises(NotFoundError):
        await other.refresh_item_target(item.id, item.payload)
    with pytest.raises(NotFoundError):
        await other.mark_item_conflict(item.id, ReviewConflict(reason="foreign write"))
    assert (await repo.list_items(batch.id))[0] == item
