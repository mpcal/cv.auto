"""Legacy evidence is backfilled without inventing quotations or re-verification."""

import json

from alembic import command
from sqlalchemy import MetaData, create_engine, insert, select

from cvopt.adapters.db.engine import alembic_config


def test_legacy_backfill_validates_support_and_recomputes_claims(tmp_path):
    url = f"sqlite:///{tmp_path / 'old.db'}"
    cfg = alembic_config(url)
    command.upgrade(cfg, "0003")
    engine = create_engine(url)
    old = MetaData()
    old.reflect(engine)
    common = dict(user_id="alice@example.com", created_at="2026-01-01", updated_at="2026-01-01")
    with engine.begin() as conn:
        conn.execute(
            insert(old.tables["users"]),
            dict(user_id=common["user_id"], user_key="alice", created_at=common["created_at"]),
        )
        for cid in ["good", "noquote", "wrongkind", "missingdoc", "unnamed"]:
            conn.execute(
                insert(old.tables["claim"]),
                dict(
                    **common,
                    id=cid,
                    type="skill",
                    text="Rust",
                    text_lang="en",
                    position_id=None,
                    keywords="[]",
                    origin="extracted",
                    status="confirmed",
                    provability="high",
                ),
            )
        for did, kind in [("letter", "reference_letter"), ("cv", "cv")]:
            conn.execute(
                insert(old.tables["source_document"]),
                dict(
                    **common,
                    id=did,
                    kind=kind,
                    filename="x.txt",
                    sha256=did,
                    mime_type="text/plain",
                    language="en",
                    issuer="",
                    position_id=None,
                    extracted_text="Rust",
                    received_at="2026-01-01T00:00:00Z",
                ),
            )
        for cid, kind, did, quote in [
            ("good", "reference_letter", "letter", "Rust"),
            ("noquote", "reference_letter", "letter", ""),
            ("wrongkind", "reference_letter", "cv", "Rust"),
            ("missingdoc", "own_cv", None, ""),
            ("unnamed", "reference_person", None, ""),
        ]:
            conn.execute(
                insert(old.tables["evidence"]),
                dict(
                    **common,
                    id=cid,
                    claim_id=cid,
                    kind=kind,
                    document_id=did,
                    excerpt=quote,
                    reference_name="",
                    reference_contact="",
                ),
            )
        conn.execute(
            insert(old.tables["language"]), dict(**common, id="en", language_code="en", level="B2")
        )
        for fid, rid, did in [
            ("valid", "en", "cv"),
            ("orphan", "absent", "cv"),
            ("unquoted", "en", "letter"),
        ]:
            conn.execute(
                insert(old.tables["fact_source"]),
                dict(**common, id=fid, record_type="language", record_id=rid, document_id=did),
            )
    command.upgrade(cfg, "head")
    new = MetaData()
    new.reflect(engine)
    with engine.connect() as conn:
        evidence = {
            r["id"]: dict(r) for r in conn.execute(select(new.tables["evidence"])).mappings()
        }
        assert evidence["good"]["state"] == "active"
        assert evidence["good"]["snapshot_origin"] == "legacy_backfill"
        assert json.loads(evidence["good"]["assertion"])["text"] == "Rust"
        assert all(
            evidence[c]["state"] == "stale"
            for c in ["noquote", "wrongkind", "missingdoc", "unnamed"]
        )
        claims = {r["id"]: r for r in conn.execute(select(new.tables["claim"])).mappings()}
        assert claims["good"]["provability"] == "high"
        assert all(
            claims[c]["provability"] == "none" and claims[c]["status"] == "proposed"
            for c in ["noquote", "wrongkind", "missingdoc", "unnamed"]
        )
        links = {r["id"]: r for r in conn.execute(select(new.tables["fact_source"])).mappings()}
        assert links["valid"]["state"] == "active"
        assert links["orphan"]["state"] == links["unquoted"]["state"] == "stale"
        assert links["unquoted"]["excerpt"] == ""
    engine.dispose()


def test_downgrade_refuses_before_mutating_history(tmp_path):
    import sqlite3

    import pytest

    path = tmp_path / "current.db"
    cfg = alembic_config(f"sqlite:///{path}")
    command.upgrade(cfg, "head")
    engine = create_engine(f"sqlite:///{path}")
    metadata = MetaData()
    metadata.reflect(engine)
    common = dict(user_id="alice@example.com", created_at="2026-01-01", updated_at="2026-01-01")
    with engine.begin() as conn:
        conn.execute(
            insert(metadata.tables["users"]),
            dict(user_id=common["user_id"], user_key="alice", created_at=common["created_at"]),
        )
        conn.execute(
            insert(metadata.tables["claim"]),
            dict(
                **common,
                id="claim",
                type="skill",
                text="Rust",
                text_lang="en",
                position_id=None,
                keywords="[]",
                status="confirmed",
                origin="manual",
                provability="medium",
            ),
        )
        for eid, state in [("historical", "stale"), ("current", "active")]:
            conn.execute(
                insert(metadata.tables["evidence"]),
                dict(
                    **common,
                    id=eid,
                    claim_id="claim",
                    kind="self_declared",
                    document_id=None,
                    excerpt="",
                    reference_name="",
                    reference_contact="",
                    state=state,
                    snapshot_origin="captured",
                    assertion=json.dumps(
                        dict(type="skill", text="Rust", text_lang="en", position_id=None)
                    ),
                ),
            )
    engine.dispose()
    with sqlite3.connect(path) as conn:
        before = list(conn.iterdump())
    with pytest.raises(RuntimeError, match="history"):
        command.downgrade(cfg, "0003")
    with sqlite3.connect(path) as conn:
        assert list(conn.iterdump()) == before


def legacy_support_database(tmp_path, *, evidence_rows, fact_rows=()):
    """Malformed legacy fixtures bypass old SQLite FKs, as damaged legacy files can."""
    path = tmp_path / "malformed.db"
    cfg = alembic_config(f"sqlite:///{path}")
    command.upgrade(cfg, "0003")
    engine = create_engine(f"sqlite:///{path}")
    metadata = MetaData()
    metadata.reflect(engine)
    common = dict(user_id="alice@example.com", created_at="2026-01-01", updated_at="2026-01-01")
    with engine.begin() as conn:
        for uid, key in [("alice@example.com", "alice"), ("bob@example.com", "bob")]:
            conn.execute(
                insert(metadata.tables["users"]),
                dict(user_id=uid, user_key=key, created_at=common["created_at"]),
            )
        for did, uid in [("bobdoc", "bob@example.com"), ("alicedoc", "alice@example.com")]:
            conn.execute(
                insert(metadata.tables["source_document"]),
                dict(
                    **{**common, "user_id": uid},
                    id=did,
                    kind="cv",
                    filename="x.txt",
                    sha256=did,
                    mime_type="text/plain",
                    language="en",
                    issuer="",
                    position_id=None,
                    extracted_text="Rust",
                    received_at="2026-01-01T00:00:00Z",
                ),
            )
        for cid in sorted({r["claim_id"] for r in evidence_rows}):
            conn.execute(
                insert(metadata.tables["claim"]),
                dict(
                    **common,
                    id=cid,
                    type="skill",
                    text="Rust",
                    text_lang="en",
                    position_id=None,
                    keywords="[]",
                    origin="extracted",
                    status="confirmed",
                    provability="high",
                ),
            )
        for row in evidence_rows:
            conn.execute(
                insert(metadata.tables["evidence"]),
                dict(**common, excerpt="", reference_name="Carol", reference_contact="", **row),
            )
        for row in fact_rows:
            conn.execute(insert(metadata.tables["fact_source"]), dict(**common, **row))
    engine.dispose()
    command.upgrade(cfg, "head")
    return path


async def test_migration_rejects_supplied_unresolved_optional_documents(tmp_path):
    from sqlalchemy.ext.asyncio import create_async_engine

    from cvopt.adapters.clock import FixedClock
    from cvopt.adapters.db.repository import UserRepository
    from cvopt.context import UserContext
    from cvopt.services.data import recompute_provability
    from tests.conftest import TEST_NOW

    rows = [
        dict(id=cid, claim_id=cid, kind=kind, document_id=did)
        for cid, kind, did in [
            ("missing_person", "reference_person", "absent"),
            ("foreign_person", "reference_person", "bobdoc"),
            ("missing_self", "self_declared", "absent"),
            ("missing_learnable", "learnable_declared", "absent"),
            ("valid_person", "reference_person", None),
        ]
    ]
    path = legacy_support_database(tmp_path, evidence_rows=rows)
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    repo = UserRepository(engine, UserContext.from_email("alice@example.com"), FixedClock(TEST_NOW))
    try:
        for cid in ["missing_person", "foreign_person", "missing_self", "missing_learnable"]:
            claim = await repo.get_claim(cid)
            assert claim.provability.value == "none"
            assert claim.status.value == "proposed"
            [ev] = await repo.list_evidence([cid])
            assert ev.state == "stale"
            assert await repo.list_evidence([cid], active_only=True) == []
            [recomputed] = await recompute_provability(repo, [cid])
            assert recomputed.provability.value == "none"
        assert (await repo.get_claim("valid_person")).provability.value == "high"
        assert len(await repo.list_evidence(["valid_person"], active_only=True)) == 1
        exported = {r["id"]: r for r in (await repo.export_rows())["evidence"]}
        assert exported["missing_person"]["document_id"] == "absent"
        assert exported["foreign_person"]["document_id"] == "bobdoc"
    finally:
        await engine.dispose()


async def test_migrated_unknown_attachment_kinds_do_not_block_typed_queries(tmp_path):
    from sqlalchemy.ext.asyncio import create_async_engine

    from cvopt.adapters.clock import FixedClock
    from cvopt.adapters.db.repository import RepositoryFactory
    from cvopt.adapters.files import LocalFileStore
    from cvopt.context import UserContext
    from cvopt.domain.enums import ClaimStatus
    from cvopt.services.data import DataService, recompute_provability
    from tests.conftest import TEST_NOW

    path = legacy_support_database(
        tmp_path,
        evidence_rows=[
            dict(id="bad", claim_id="claim", kind="unknown_kind", document_id=None),
            dict(id="good", claim_id="claim", kind="self_declared", document_id=None),
        ],
        fact_rows=[
            dict(
                id="badfact",
                record_type="unknown_type",
                record_id="missing",
                document_id="alicedoc",
            )
        ],
    )
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    user = UserContext.from_email("alice@example.com")
    repos = RepositoryFactory(engine, FixedClock(TEST_NOW))
    repo = repos.for_user(user)
    service = DataService(repos, LocalFileStore(tmp_path))
    try:
        assert [e.id for e in await repo.list_evidence(["claim"], active_only=True)] == ["good"]
        assert [e.id for e in await repo.list_evidence(["claim"])] == ["good"]
        assert await repo.list_fact_sources(active_only=True) == []
        assert await repo.list_fact_sources() == []
        [invalid] = await repo.list_invalid_evidence(["claim"])
        assert invalid.id == "bad" and invalid.state == "invalid"
        assert invalid.raw["kind"] == "unknown_kind"
        [badfact] = await repo.list_invalid_fact_sources()
        assert badfact.raw["record_type"] == "unknown_type"
        [recomputed] = await recompute_provability(repo, ["claim"])
        assert recomputed.provability.value == "medium"
        assert (await service.confirm_claim(user, "claim")).status is ClaimStatus.CONFIRMED
        [listed] = await service.list_claims(user)
        assert listed.invalid_evidence[0].id == "bad"
        assert (await service.get_claim(user, "claim")).invalid_evidence[0].id == "bad"
        assert (
            await service.update_claim(user, "claim", {"text": "Rust programming"})
        ).provability.value == "medium"
        exported = await repo.export_rows()
        assert next(r for r in exported["evidence"] if r["id"] == "bad")["kind"] == "unknown_kind"
        assert exported["fact_source"][0]["record_type"] == "unknown_type"
    finally:
        await engine.dispose()
