"""UserRepository: the only way to the database, bound to one user."""

import json
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy import ColumnElement, Table, delete, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from ulid import ULID

from cvopt.adapters.db import schema as s
from cvopt.context import UserContext
from cvopt.domain.enums import (
    BatchStatus,
    ClaimStatus,
    ClaimType,
    Decision,
    Provability,
    RecordType,
)
from cvopt.domain.records import (
    Certificate,
    Claim,
    Education,
    Evidence,
    Language,
    Position,
    Profile,
    SourceDocument,
)
from cvopt.domain.review import Payload, ProposalItem, ReviewBatch
from cvopt.errors import InputError, NotFoundError
from cvopt.ports import Clock

MIN_SUFFIX = 6
COMMON = {"user_id", "created_at", "updated_at"}
JSON_COLUMNS = {"keywords", "profiles", "payload"}


def dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def to_row(model: BaseModel) -> dict[str, Any]:
    row = model.model_dump(mode="json")
    return {k: dumps(v) if k in JSON_COLUMNS else v for k, v in row.items()}


def from_row[M: BaseModel](model: type[M], row: dict[str, Any]) -> M:
    data = {k: json.loads(v) if k in JSON_COLUMNS else v for k, v in row.items()}
    return model.model_validate({k: v for k, v in data.items() if k not in COMMON})


class UserRepository:
    def __init__(self, engine: AsyncEngine, user: UserContext, clock: Clock) -> None:
        self._engine = engine
        self._user = user
        self._clock = clock
        self._conn: AsyncConnection | None = None
        self._user_ensured = False

    # --- plumbing ----------------------------------------------------------

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[None]:
        """One connection and transaction; nested calls reuse it."""
        if self._conn is not None:
            yield
            return
        async with self._engine.begin() as conn:
            self._conn = conn
            try:
                yield
            finally:
                self._conn = None

    @asynccontextmanager
    async def _connect(self) -> AsyncIterator[AsyncConnection]:
        async with self.transaction():
            assert self._conn is not None
            yield self._conn

    def _mine(self, table: Table) -> ColumnElement[bool]:
        return table.c.user_id == self._user.user_id  # used by every select, update, delete

    def _now(self) -> str:
        return self._clock.now().isoformat()

    async def _ensure_user(self, conn: AsyncConnection) -> None:
        if not self._user_ensured:
            await conn.execute(
                sqlite_insert(s.users)
                .values(
                    user_id=self._user.user_id, user_key=self._user.user_key, created_at=self._now()
                )
                .on_conflict_do_nothing()
            )
            self._user_ensured = True

    async def _rows(self, table: Table, *where: ColumnElement[bool]) -> list[dict[str, Any]]:
        async with self._connect() as conn:
            q = select(table).where(self._mine(table), *where).order_by(*table.primary_key.columns)
            return [dict(r._mapping) for r in await conn.execute(q)]

    async def _resolve(self, table: Table, ref: str) -> str:
        """A full id or a unique suffix of at least MIN_SUFFIX characters."""
        async with self._connect() as conn:
            q = select(table.c.id).where(self._mine(table))
            exact = await conn.scalar(q.where(table.c.id == ref))
            if exact is not None:
                return str(exact)
            if len(ref) >= MIN_SUFFIX:
                ids = list(await conn.scalars(q.where(table.c.id.endswith(ref)).limit(2)))
                if len(ids) == 1:
                    return str(ids[0])
                if ids:
                    raise InputError(f"Ambiguous {table.name} id: {ref}")
        raise NotFoundError(f"No {table.name} with id {ref}")

    async def _get[M: BaseModel](self, table: Table, model: type[M], ref: str) -> M:
        rid = await self._resolve(table, ref)
        return from_row(model, (await self._rows(table, table.c.id == rid))[0])

    async def _list[M: BaseModel](
        self, table: Table, model: type[M], *where: ColumnElement[bool]
    ) -> list[M]:
        return [from_row(model, r) for r in await self._rows(table, *where)]

    async def _insert[M: BaseModel](
        self, table: Table, model: type[M], values: dict[str, Any]
    ) -> M:
        """Validates the values as `model` before writing; raises InputError if invalid."""
        new = _validated(model, {"id": str(ULID()), **values})
        now = self._now()
        async with self._connect() as conn:
            await self._ensure_user(conn)
            row = {"user_id": self._user.user_id, "created_at": now, "updated_at": now}
            await conn.execute(insert(table).values(**row, **to_row(new)))
        return new

    async def _update[M: BaseModel](
        self, table: Table, model: type[M], ref: str, values: dict[str, Any]
    ) -> M:
        async with self.transaction():
            rid = await self._resolve(table, ref)
            current = from_row(model, (await self._rows(table, table.c.id == rid))[0])
            new = _validated(model, {**current.model_dump(), **values})
            async with self._connect() as conn:
                await conn.execute(
                    update(table)
                    .where(self._mine(table), table.c.id == rid)
                    .values(updated_at=self._now(), **to_row(new))
                )
        return new

    async def _delete(self, table: Table, *where: ColumnElement[bool]) -> int:
        async with self._connect() as conn:
            result = await conn.execute(delete(table).where(self._mine(table), *where))
            return result.rowcount

    async def _delete_by_ref(self, table: Table, ref: str) -> str:
        rid = await self._resolve(table, ref)
        await self._delete(table, table.c.id == rid)
        return rid

    # --- LLM cache -----------------------------------------------------------

    async def cache_get(self, key: str) -> str | None:
        async with self._connect() as conn:
            q = select(s.llm_cache.c.response).where(
                self._mine(s.llm_cache), s.llm_cache.c.key == key
            )
            hit = await conn.scalar(q)
            return None if hit is None else str(hit)

    async def cache_put(
        self, key: str, task: str, prompt_version: str, model: str, response: str
    ) -> None:
        async with self._connect() as conn:
            await self._ensure_user(conn)
            await conn.execute(
                sqlite_insert(s.llm_cache)
                .values(
                    user_id=self._user.user_id,
                    key=key,
                    task=task,
                    prompt_version=prompt_version,
                    model=model,
                    response=response,
                    created_at=self._now(),
                )
                .on_conflict_do_nothing()
            )

    async def cache_rows(self) -> list[dict[str, Any]]:
        """Every cache row of this user (recordings, export)."""
        return await self._rows(s.llm_cache)

    # --- profile -----------------------------------------------------------

    async def get_profile(self) -> Profile:
        rows = await self._rows(s.profile)
        return from_row(Profile, rows[0]) if rows else Profile()

    async def save_profile(self, profile: Profile) -> Profile:
        now = self._now()
        async with self._connect() as conn:
            await self._ensure_user(conn)
            row = to_row(profile)
            stmt = sqlite_insert(s.profile).values(
                user_id=self._user.user_id, created_at=now, updated_at=now, **row
            )
            await conn.execute(
                stmt.on_conflict_do_update(
                    index_elements=["user_id"], set_={"updated_at": now, **row}
                )
            )
        return profile

    # --- fact records --------------------------------------------------------

    async def list_positions(self) -> list[Position]:
        return await self._list(s.position, Position)

    async def get_position(self, ref: str) -> Position:
        return await self._get(s.position, Position, ref)

    async def insert_position(self, values: dict[str, Any]) -> Position:
        return await self._insert(s.position, Position, values)

    async def update_position(self, ref: str, values: dict[str, Any]) -> Position:
        return await self._update(s.position, Position, ref, values)

    async def delete_position(self, ref: str) -> str:
        return await self._delete_by_ref(s.position, ref)

    async def list_education(self) -> list[Education]:
        return await self._list(s.education, Education)

    async def get_education(self, ref: str) -> Education:
        return await self._get(s.education, Education, ref)

    async def insert_education(self, values: dict[str, Any]) -> Education:
        return await self._insert(s.education, Education, values)

    async def update_education(self, ref: str, values: dict[str, Any]) -> Education:
        return await self._update(s.education, Education, ref, values)

    async def delete_education(self, ref: str) -> str:
        return await self._delete_by_ref(s.education, ref)

    async def list_languages(self) -> list[Language]:
        return await self._list(s.language, Language)

    async def get_language(self, ref: str) -> Language:
        return await self._get(s.language, Language, ref)

    async def insert_language(self, values: dict[str, Any]) -> Language:
        return await self._insert(s.language, Language, values)

    async def update_language(self, ref: str, values: dict[str, Any]) -> Language:
        return await self._update(s.language, Language, ref, values)

    async def delete_language(self, ref: str) -> str:
        return await self._delete_by_ref(s.language, ref)

    async def list_certificates(self) -> list[Certificate]:
        return await self._list(s.certificate, Certificate)

    async def get_certificate(self, ref: str) -> Certificate:
        return await self._get(s.certificate, Certificate, ref)

    async def insert_certificate(self, values: dict[str, Any]) -> Certificate:
        return await self._insert(s.certificate, Certificate, values)

    async def update_certificate(self, ref: str, values: dict[str, Any]) -> Certificate:
        return await self._update(s.certificate, Certificate, ref, values)

    async def delete_certificate(self, ref: str) -> str:
        return await self._delete_by_ref(s.certificate, ref)

    # --- fact sources ----------------------------------------------------------

    async def insert_fact_source(
        self, record_type: RecordType, record_id: str, document_id: str
    ) -> None:
        now = self._now()
        async with self._connect() as conn:
            await self._ensure_user(conn)
            await conn.execute(
                sqlite_insert(s.fact_source)
                .values(
                    id=str(ULID()),
                    user_id=self._user.user_id,
                    created_at=now,
                    updated_at=now,
                    record_type=record_type.value,
                    record_id=record_id,
                    document_id=document_id,
                )
                .on_conflict_do_nothing()
            )

    async def fact_source_documents(self, record_type: RecordType, record_id: str) -> list[str]:
        rows = await self._rows(
            s.fact_source,
            s.fact_source.c.record_type == record_type.value,
            s.fact_source.c.record_id == record_id,
        )
        return [r["document_id"] for r in rows]

    async def delete_fact_sources(self, record_type: RecordType, record_id: str) -> None:
        await self._delete(
            s.fact_source,
            s.fact_source.c.record_type == record_type.value,
            s.fact_source.c.record_id == record_id,
        )

    # --- claims and evidence ---------------------------------------------------

    async def list_claims(
        self,
        *,
        type: ClaimType | None = None,
        position_id: str | None = None,
        provability: Provability | None = None,
        status: ClaimStatus | None = None,
        ids: Iterable[str] | None = None,
    ) -> list[Claim]:
        c = s.claim.c
        where = [
            cond
            for cond in (
                c.type == type.value if type else None,
                c.position_id == position_id if position_id else None,
                c.provability == provability.value if provability else None,
                c.status == status.value if status else None,
                c.id.in_(list(ids)) if ids is not None else None,
            )
            if cond is not None
        ]
        return await self._list(s.claim, Claim, *where)

    async def count_claims(self, position_id: str) -> int:
        async with self._connect() as conn:
            q = select(func.count()).where(
                self._mine(s.claim), s.claim.c.position_id == position_id
            )
            return int(await conn.scalar(q) or 0)

    async def get_claim(self, ref: str) -> Claim:
        return await self._get(s.claim, Claim, ref)

    async def insert_claim(self, values: dict[str, Any]) -> Claim:
        return await self._insert(s.claim, Claim, values)

    async def update_claim(self, ref: str, values: dict[str, Any]) -> Claim:
        return await self._update(s.claim, Claim, ref, values)

    async def delete_claim(self, ref: str) -> str:
        return await self._delete_by_ref(s.claim, ref)  # evidence cascades

    async def list_evidence(self, claim_ids: Iterable[str] | None = None) -> list[Evidence]:
        where = [s.evidence.c.claim_id.in_(list(claim_ids))] if claim_ids is not None else []
        return await self._list(s.evidence, Evidence, *where)

    async def get_evidence(self, ref: str) -> Evidence:
        return await self._get(s.evidence, Evidence, ref)

    async def insert_evidence(self, values: dict[str, Any]) -> Evidence:
        return await self._insert(s.evidence, Evidence, values)

    async def delete_evidence(self, ref: str) -> str:
        return await self._delete_by_ref(s.evidence, ref)

    async def claims_with_document_evidence(self, document_id: str) -> list[str]:
        rows = await self._rows(s.evidence, s.evidence.c.document_id == document_id)
        return sorted({r["claim_id"] for r in rows})

    # --- documents -------------------------------------------------------------

    async def list_documents(self) -> list[SourceDocument]:
        return await self._list(s.source_document, SourceDocument)

    async def get_document(self, ref: str) -> SourceDocument:
        return await self._get(s.source_document, SourceDocument, ref)

    async def find_document_by_sha256(self, sha256: str) -> SourceDocument | None:
        docs = await self._list(
            s.source_document, SourceDocument, s.source_document.c.sha256 == sha256
        )
        return docs[0] if docs else None

    async def insert_document(self, values: dict[str, Any]) -> SourceDocument:
        return await self._insert(s.source_document, SourceDocument, values)

    async def delete_document(self, ref: str) -> str:
        return await self._delete_by_ref(s.source_document, ref)  # evidence, links cascade

    async def detach_documents_from_position(self, position_id: str) -> None:
        async with self._connect() as conn:
            await conn.execute(
                update(s.source_document)
                .where(
                    self._mine(s.source_document), s.source_document.c.position_id == position_id
                )
                .values(position_id=None, updated_at=self._now())
            )

    # --- review batches ---------------------------------------------------------

    async def insert_batch(self, document_id: str) -> ReviewBatch:
        return await self._insert(
            s.review_batch, ReviewBatch, {"document_id": document_id, "status": BatchStatus.OPEN}
        )

    async def get_batch(self, ref: str) -> ReviewBatch:
        return await self._get(s.review_batch, ReviewBatch, ref)

    async def list_batches(self, status: BatchStatus | None = None) -> list[ReviewBatch]:
        where = [s.review_batch.c.status == status.value] if status else []
        return await self._list(s.review_batch, ReviewBatch, *where)

    async def update_batch(self, ref: str, values: dict[str, Any]) -> ReviewBatch:
        return await self._update(s.review_batch, ReviewBatch, ref, values)

    async def insert_item(
        self, batch_id: str, seq: int, payload: Payload, target_id: str | None = None
    ) -> ProposalItem:
        item = _validated(
            ProposalItem,
            {
                "id": str(ULID()),
                "batch_id": batch_id,
                "seq": seq,
                "target_id": target_id,
                "payload": payload,
            },
        )
        now = self._now()
        async with self._connect() as conn:
            await self._ensure_user(conn)
            await conn.execute(
                insert(s.proposal_item).values(
                    user_id=self._user.user_id,
                    created_at=now,
                    updated_at=now,
                    action=item.action.value,
                    **to_row(item),
                )
            )
        return item

    async def list_items(self, batch_id: str) -> list[ProposalItem]:
        rows = await self._rows(s.proposal_item, s.proposal_item.c.batch_id == batch_id)
        items = [
            from_row(ProposalItem, {k: v for k, v in r.items() if k != "action"}) for r in rows
        ]
        return sorted(items, key=lambda i: i.seq)

    async def update_item(
        self, item_id: str, decision: Decision, payload: Payload | None = None, note: str = ""
    ) -> None:
        values: dict[str, Any] = {"decision": decision.value, "updated_at": self._now()}
        if payload is not None:
            values["payload"] = dumps(payload.model_dump(mode="json"))
        if note:
            values["note"] = note
        async with self._connect() as conn:
            await conn.execute(
                update(s.proposal_item)
                .where(self._mine(s.proposal_item), s.proposal_item.c.id == item_id)
                .values(**values)
            )

    # --- account ------------------------------------------------------------------

    async def export_rows(self) -> dict[str, list[dict[str, Any]]]:
        """Every row of this user, per table (for the export archive)."""
        out: dict[str, list[dict[str, Any]]] = {}
        async with self._connect() as conn:
            for table in s.metadata.sorted_tables:
                q = select(table).where(self._mine(table)).order_by(*table.primary_key.columns)
                out[table.name] = [dict(r._mapping) for r in await conn.execute(q)]
        return out

    async def delete_user(self) -> None:
        """Deletes the users row; the foreign keys cascade to every other table."""
        async with self._connect() as conn:
            await conn.execute(delete(s.users).where(self._mine(s.users)))
            self._user_ensured = False


def _validated[M: BaseModel](model: type[M], values: dict[str, Any]) -> M:
    try:
        return model.model_validate(values)
    except ValidationError as err:
        raise InputError(_describe(err)) from err


def _describe(err: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in err.errors())


class RepositoryFactory:
    def __init__(self, engine: AsyncEngine, clock: Clock) -> None:
        self._engine = engine
        self._clock = clock

    def for_user(self, user: UserContext) -> UserRepository:
        return UserRepository(self._engine, user, self._clock)
