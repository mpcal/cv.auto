"""Assertion snapshots and historical support state.

Revision ID: 0004
Revises: 0003
"""

import json
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa
from alembic import op
from pydantic import BaseModel, ValidationError

from cvopt.domain.assertions import (
    check_documentary_assertion,
    check_evidence_support,
    check_fact_support,
    claim_assertion,
)
from cvopt.domain.enums import EvidenceKind, RecordType
from cvopt.domain.records import FACT_MODELS, Claim, SourceDocument
from cvopt.domain.rules import claim_provability

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    for table in ("evidence", "fact_source"):
        op.add_column(table, sa.Column("assertion", sa.Text(), nullable=True))
        op.add_column(
            table, sa.Column("state", sa.String(8), nullable=False, server_default="stale")
        )
        op.add_column(
            table,
            sa.Column(
                "snapshot_origin", sa.String(24), nullable=False, server_default="legacy_backfill"
            ),
        )
    op.add_column("fact_source", sa.Column("excerpt", sa.Text(), nullable=False, server_default=""))
    with op.batch_alter_table(
        "fact_source", naming_convention={"uq": "uq_%(table_name)s_%(column_0_N_name)s"}
    ) as batch:
        batch.drop_constraint(
            "uq_fact_source_user_id_record_type_record_id_document_id", type_="unique"
        )

    conn = op.get_bind()
    metadata = sa.MetaData()
    metadata.reflect(conn)

    def rows(table: str) -> list[Mapping[str, Any]]:
        return [dict(row) for row in conn.execute(sa.select(metadata.tables[table])).mappings()]

    def parsed[M: BaseModel](model: type[M], row: Mapping[str, Any]) -> M:
        data = {k: v for k, v in row.items() if k in model.model_fields}
        for key in ("keywords", "highlights", "links"):
            if key in data:
                data[key] = json.loads(data[key])
        return model.model_validate(data)

    documents = {(r["user_id"], r["id"]): r for r in rows("source_document")}
    claims = {(r["user_id"], r["id"]): r for r in rows("claim")}
    facts = {kind: {(r["user_id"], r["id"]): r for r in rows(kind.value)} for kind in RecordType}
    active_kinds: dict[tuple[str, str], list[EvidenceKind]] = {}
    for row in rows("evidence"):
        key = (row["user_id"], row["claim_id"])
        assertion = None
        state = "stale"
        try:
            assertion = claim_assertion(parsed(Claim, claims[key])).model_dump(mode="json")
            # A missing optional id is valid; a supplied unresolved id is not.
            doc_row = (
                documents[(row["user_id"], row["document_id"])]
                if row["document_id"] is not None
                else None
            )
            doc = parsed(SourceDocument, doc_row) if doc_row else None
            kind = EvidenceKind(row["kind"])
            check_evidence_support(kind, doc, row["excerpt"], row["reference_name"])
            check_documentary_assertion(parsed(Claim, claims[key]), kind, doc, row["excerpt"])
            state = "active"
            active_kinds.setdefault(key, []).append(kind)
        except (ValueError, ValidationError, KeyError, TypeError):
            pass  # Retain malformed links for inspection, never as usable support.
        conn.execute(
            sa.update(metadata.tables["evidence"])
            .where(metadata.tables["evidence"].c.id == row["id"])
            .values(assertion=json.dumps(assertion) if assertion else None, state=state)
        )
    for row in rows("fact_source"):
        assertion = None
        state = "stale"
        try:
            record_kind = RecordType(row["record_type"])
            fact = parsed(
                FACT_MODELS[record_kind], facts[record_kind][(row["user_id"], row["record_id"])]
            )
            assertion = fact.model_dump(mode="json")
            doc = parsed(SourceDocument, documents[(row["user_id"], row["document_id"])])
            check_fact_support(doc, row["excerpt"])
            state = "active"
        except (ValueError, ValidationError, KeyError, TypeError):
            pass
        conn.execute(
            sa.update(metadata.tables["fact_source"])
            .where(metadata.tables["fact_source"].c.id == row["id"])
            .values(assertion=json.dumps(assertion) if assertion else None, state=state)
        )
    for key, row in claims.items():
        kinds = active_kinds.get(key, [])
        values = {"provability": claim_provability(kinds).value}
        if not kinds:
            values["status"] = "proposed"
        conn.execute(
            sa.update(metadata.tables["claim"])
            .where(metadata.tables["claim"].c.id == row["id"])
            .values(**values)
        )


def downgrade() -> None:
    raise RuntimeError(
        "Migration 0004 is forward-only: the old schema cannot represent "
        "retained assertion history."
    )
