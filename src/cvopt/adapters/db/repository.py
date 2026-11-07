"""UserRepository: the only way to the database, bound to one user."""

import json
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from typing import Any, Literal

from pydantic import BaseModel, ValidationError
from sqlalchemy import ColumnElement, Table, delete, func, insert, select, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine
from ulid import ULID

from cvopt.adapters.db import schema as s
from cvopt.context import UserContext
from cvopt.domain.assertions import (
    assertion_fingerprint,
    check_documentary_assertion,
    check_evidence_support,
    check_fact_support,
    claim_assertion,
)
from cvopt.domain.enums import (
    BatchStatus,
    ClaimStatus,
    ClaimType,
    Decision,
    EvidenceKind,
    Provability,
    RecordType,
)
from cvopt.domain.records import (
    FACT_MODELS,
    Award,
    Certificate,
    Claim,
    ClaimAssertion,
    Education,
    Evidence,
    FactAssertion,
    FactSource,
    InvalidAttachment,
    Language,
    Position,
    Profile,
    SourceDocument,
)
from cvopt.domain.review import (
    Payload,
    ProposalItem,
    ReviewBatch,
    ReviewConflict,
    ReviewContext,
    created_assertion,
    review_target,
)
from cvopt.domain.rules import claim_provability
from cvopt.errors import InputError, NotFoundError
from cvopt.ports import Clock

MIN_SUFFIX = 6
COMMON = {"user_id", "created_at", "updated_at"}
JSON_COLUMNS = {
    "keywords",
    "profiles",
    "payload",
    "highlights",
    "links",
    "assertion",
    "review_context",
}


def dumps(value: object) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False)


def to_row(model: BaseModel) -> dict[str, Any]:
    row = model.model_dump(mode="json")
    return {k: dumps(v) if k in JSON_COLUMNS else v for k, v in row.items()}


def from_row[M: BaseModel](model: type[M], row: dict[str, Any]) -> M:
    data = {k: json.loads(v) if k in JSON_COLUMNS and v is not None else v for k, v in row.items()}
    ignored = COMMON | ({"action"} if model is ProposalItem else set())
    return model.model_validate({k: v for k, v in data.items() if k not in ignored})


def attachment_from_row[M: BaseModel](
    model: type[M], row: dict[str, Any], attachment_type: Literal["evidence", "fact_source"]
) -> M | InvalidAttachment:
    try:
        return from_row(model, row)
    except (ValueError, TypeError) as err:
        # Never put Pydantic input values or arbitrary field names in diagnostics.
        # The untouched raw row is available only in explicit JSON/history exports.
        if isinstance(err, ValidationError):
            fields = []
            for error in err.errors(include_input=False, include_context=False)[:8]:
                field = str(error["loc"][0]) if error["loc"] else "record"
                if field not in model.model_fields:
                    field = "record" if not error["loc"] else "unexpected_field"
                fields.append(f"{field} ({error['type']})")
            detail = ", ".join(dict.fromkeys(fields))
        else:
            detail = "assertion (invalid_json)"
        return InvalidAttachment(
            id=row["id"],
            attachment_type=attachment_type,
            validation_error=f"Invalid {attachment_type} fields: {detail}",
            raw=row,
        )


def export_assertion(value: Any) -> Any:
    if isinstance(value, str):
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            pass  # Preserve damaged snapshot bytes as raw text, never discard history.
    return value


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

    async def _read_attachments[M: BaseModel](
        self, table: Table, model: type[M], *where: ColumnElement[bool]
    ) -> tuple[list[M], list[InvalidAttachment]]:
        typed: list[M] = []
        invalid: list[InvalidAttachment] = []
        attachment_type: Literal["evidence", "fact_source"] = (
            "evidence" if table is s.evidence else "fact_source"
        )
        for row in await self._rows(table, *where):
            attachment = attachment_from_row(model, row, attachment_type)
            if isinstance(attachment, InvalidAttachment):
                invalid.append(attachment)
            else:
                typed.append(attachment)
        return typed, invalid

    async def _get_attachment[M: BaseModel](self, table: Table, model: type[M], ref: str) -> M:
        rid = await self._resolve(table, ref)
        typed, invalid = await self._read_attachments(table, model, table.c.id == rid)
        if invalid:
            guidance = (
                "; remove malformed evidence with evidence rm." if table is s.evidence else "."
            )
            raise InputError(
                f"{invalid[0].validation_error}; inspect invalid-history diagnostics "
                f"or export the attachment{guidance}"
            )
        return typed[0]

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
            if (
                isinstance(current, Position | Education | Language | Certificate | Award)
                and isinstance(new, Position | Education | Language | Certificate | Award)
                and assertion_fingerprint(current) != assertion_fingerprint(new)
            ):
                async with self._connect() as conn:
                    await conn.execute(
                        update(s.fact_source)
                        .where(
                            self._mine(s.fact_source),
                            s.fact_source.c.record_type == table.name,
                            s.fact_source.c.record_id == rid,
                        )
                        .values(state="stale", updated_at=self._now())
                    )
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

    async def cache_delete(self, key: str) -> None:
        await self._delete(s.llm_cache, s.llm_cache.c.key == key)

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

    async def list_awards(self) -> list[Award]:
        return await self._list(s.award, Award)

    async def get_award(self, ref: str) -> Award:
        return await self._get(s.award, Award, ref)

    async def insert_award(self, values: dict[str, Any]) -> Award:
        return await self._insert(s.award, Award, values)

    async def update_award(self, ref: str, values: dict[str, Any]) -> Award:
        return await self._update(s.award, Award, ref, values)

    async def delete_award(self, ref: str) -> str:
        return await self._delete_by_ref(s.award, ref)

    # --- fact sources ----------------------------------------------------------

    async def _fact(self, record_type: RecordType, ref: str) -> FactAssertion:
        return await self._get(s.metadata.tables[record_type.value], FACT_MODELS[record_type], ref)

    async def insert_fact_source(
        self, record_type: RecordType, record_id: str, document_id: str, *, excerpt: str = ""
    ) -> FactSource:
        async with self.transaction():
            fact = await self._fact(record_type, record_id)
            doc = await self.get_document(document_id)
            try:
                check_fact_support(doc, excerpt)
            except ValueError as err:
                raise InputError(str(err)) from err
            for link in await self.list_fact_sources(record_type, fact.id, active_only=True):
                if link.document_id == doc.id and link.excerpt == excerpt:
                    return link
            return await self._insert(
                s.fact_source,
                FactSource,
                dict(
                    record_type=record_type,
                    record_id=fact.id,
                    document_id=doc.id,
                    excerpt=excerpt,
                    assertion=fact,
                    state="active",
                    snapshot_origin="captured",
                ),
            )

    async def get_fact_source(self, ref: str) -> FactSource:
        return await self._get_attachment(s.fact_source, FactSource, ref)

    async def list_fact_sources(
        self,
        record_type: RecordType | None = None,
        record_id: str | None = None,
        *,
        active_only: bool = False,
    ) -> list[FactSource]:
        where = []
        if record_type is not None:
            where.append(s.fact_source.c.record_type == record_type.value)
        if record_id is not None:
            where.append(s.fact_source.c.record_id == record_id)
        if active_only:
            where.append(s.fact_source.c.state == "active")
        links, _ = await self._read_attachments(s.fact_source, FactSource, *where)
        if not active_only:
            return links
        valid = []
        for link in links:
            if link.state != "active" or link.assertion is None:
                continue
            try:
                fact = await self._fact(link.record_type, link.record_id)
                check_fact_support(await self.get_document(link.document_id), link.excerpt)
                if assertion_fingerprint(fact) == assertion_fingerprint(link.assertion):
                    valid.append(link)
            except (ValueError, InputError, NotFoundError):
                continue
        return valid

    async def list_invalid_fact_sources(
        self, record_type: RecordType | None = None, record_id: str | None = None
    ) -> list[InvalidAttachment]:
        where = []
        if record_type is not None:
            where.append(s.fact_source.c.record_type == record_type.value)
        if record_id is not None:
            where.append(s.fact_source.c.record_id == record_id)
        _, invalid = await self._read_attachments(s.fact_source, FactSource, *where)
        return invalid

    async def fact_source_documents(self, record_type: RecordType, record_id: str) -> list[str]:
        return sorted(
            {
                link.document_id
                for link in await self.list_fact_sources(record_type, record_id, active_only=True)
            }
        )

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
        async with self.transaction():
            current = await self.get_claim(ref)
            new = _validated(Claim, {**current.model_dump(), **values})
            if new.position_id:
                await self.get_position(new.position_id)
            if assertion_fingerprint(current) != assertion_fingerprint(new):
                async with self._connect() as conn:
                    await conn.execute(
                        update(s.evidence)
                        .where(self._mine(s.evidence), s.evidence.c.claim_id == current.id)
                        .values(state="stale", updated_at=self._now())
                    )
                values = {**values, "provability": Provability.NONE, "status": ClaimStatus.PROPOSED}
            else:
                kinds = [e.kind for e in await self.list_evidence([current.id], active_only=True)]
                values = {**values, "provability": claim_provability(kinds)}
                if values.get("status") == ClaimStatus.CONFIRMED and not kinds:
                    raise InputError("Confirmation requires valid active evidence.")
                if not kinds:
                    values["status"] = ClaimStatus.PROPOSED
            return await self._update(s.claim, Claim, current.id, values)

    async def delete_claim(self, ref: str) -> str:
        return await self._delete_by_ref(s.claim, ref)  # evidence cascades

    async def list_evidence(
        self, claim_ids: Iterable[str] | None = None, *, active_only: bool = False
    ) -> list[Evidence]:
        where: list[ColumnElement[bool]] = (
            [s.evidence.c.claim_id.in_(list(claim_ids))] if claim_ids is not None else []
        )
        if active_only:
            where.append(s.evidence.c.state == "active")
        evidence, _ = await self._read_attachments(s.evidence, Evidence, *where)
        if not active_only:
            return evidence
        valid = []
        for ev in evidence:
            if ev.state != "active" or ev.assertion is None:
                continue
            try:
                claim = await self.get_claim(ev.claim_id)
                doc = (
                    await self.get_document(ev.document_id) if ev.document_id is not None else None
                )
                check_evidence_support(ev.kind, doc, ev.excerpt, ev.reference_name)
                check_documentary_assertion(claim, ev.kind, doc, ev.excerpt)
                if assertion_fingerprint(claim) == assertion_fingerprint(ev.assertion):
                    valid.append(ev)
            except (ValueError, InputError, NotFoundError):
                continue
        return valid

    async def list_invalid_evidence(
        self, claim_ids: Iterable[str] | None = None
    ) -> list[InvalidAttachment]:
        where: list[ColumnElement[bool]] = (
            [s.evidence.c.claim_id.in_(list(claim_ids))] if claim_ids is not None else []
        )
        _, invalid = await self._read_attachments(s.evidence, Evidence, *where)
        return invalid

    async def get_evidence(self, ref: str) -> Evidence:
        return await self._get_attachment(s.evidence, Evidence, ref)

    async def evidence_claim_id(self, ref: str) -> str:
        """Owned linkage for deletion, without interpreting malformed evidence."""
        rid = await self._resolve(s.evidence, ref)
        row = (await self._rows(s.evidence, s.evidence.c.id == rid))[0]
        return (await self.get_claim(row["claim_id"])).id

    async def insert_evidence(self, values: dict[str, Any]) -> Evidence:
        async with self.transaction():
            claim = await self.get_claim(values["claim_id"])
            doc = (
                await self.get_document(values["document_id"])
                if values.get("document_id") is not None
                else None
            )
            kind = EvidenceKind(values["kind"])
            try:
                check_evidence_support(
                    kind, doc, values.get("excerpt", ""), values.get("reference_name", "")
                )
                check_documentary_assertion(claim, kind, doc, values.get("excerpt", ""))
            except ValueError as err:
                raise InputError(str(err)) from err
            return await self._insert(
                s.evidence,
                Evidence,
                {
                    **values,
                    "claim_id": claim.id,
                    "document_id": doc.id if doc else None,
                    "assertion": claim_assertion(claim),
                    "state": "active",
                    "snapshot_origin": "captured",
                },
            )

    async def insert_stale_evidence(
        self, values: dict[str, Any], assertion: ClaimAssertion
    ) -> Evidence:
        """Retain an extracted assertion after a review edit; never authorize support."""
        async with self.transaction():
            claim = await self.get_claim(values["claim_id"])
            doc = await self.get_document(values["document_id"])
            kind = EvidenceKind(values["kind"])
            try:
                check_evidence_support(
                    kind, doc, values.get("excerpt", ""), values.get("reference_name", "")
                )
                check_documentary_assertion(assertion, kind, doc, values.get("excerpt", ""))
            except ValueError as err:
                raise InputError(str(err)) from err
            return await self._insert(
                s.evidence,
                Evidence,
                {
                    **values,
                    "claim_id": claim.id,
                    "document_id": doc.id,
                    "assertion": assertion,
                    "state": "stale",
                    "snapshot_origin": "captured",
                },
            )

    async def insert_stale_fact_source(
        self,
        record_type: RecordType,
        record_id: str,
        document_id: str,
        assertion: FactAssertion,
        *,
        excerpt: str = "",
    ) -> FactSource:
        """Historical-only counterpart to insert_fact_source; cannot become active."""
        async with self.transaction():
            fact = await self._fact(record_type, record_id)
            if not isinstance(assertion, FACT_MODELS[record_type]):
                raise InputError("Historical assertion type must match the fixed record.")
            doc = await self.get_document(document_id)
            try:
                check_fact_support(doc, excerpt)
            except ValueError as err:
                raise InputError(str(err)) from err
            return await self._insert(
                s.fact_source,
                FactSource,
                {
                    "record_type": record_type,
                    "record_id": fact.id,
                    "document_id": doc.id,
                    "excerpt": excerpt,
                    "assertion": assertion.model_copy(update={"id": fact.id}),
                    "state": "stale",
                    "snapshot_origin": "captured",
                },
            )

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
                "review_context": await self._target_context(
                    payload,
                    ReviewContext(origin="captured", original_payload=payload),
                    batch_id,
                    capture_original=True,
                ),
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

    async def _target_context(
        self,
        payload: Payload,
        context: ReviewContext,
        batch_id: str,
        projected: ClaimAssertion | FactAssertion | None = None,
        *,
        capture_original: bool = False,
    ) -> ReviewContext:
        target = None
        reference = review_target(payload)
        if reference is None:
            return context.model_copy(update={"conflict": None})
        record_type, ref = reference
        if ref.startswith("$item:"):
            seq = int(ref.removeprefix("$item:"))
            parent = next((i for i in await self.list_items(batch_id) if i.seq == seq), None)
            target = created_assertion(parent.payload, ref) if parent else None
            target_id = ref
        elif record_type == "claim":
            claim = await self.get_claim(ref)
            target = claim_assertion(claim)
            target_id = claim.id
        else:
            target = await self._fact(record_type, ref)
            target_id = target.id
        if projected is not None:
            target = projected
        if target is None:
            return context.model_copy(update={"conflict": None})
        if capture_original:
            context = context.model_copy(
                update={
                    "original_target_id": target_id,
                    "original_target_assertion": target.model_dump(mode="json"),
                }
            )
        return context.model_copy(
            update={
                "target_id": target_id,
                "target_assertion": target.model_dump(mode="json"),
                "target_fingerprint": assertion_fingerprint(target),
                "conflict": None,
            }
        )

    async def refresh_item_target(
        self,
        item_id: str,
        payload: Payload,
        *,
        projected: ClaimAssertion | FactAssertion | None = None,
    ) -> None:
        """Explicit renewed review; never replaces the original extraction."""
        item = await self._get(s.proposal_item, ProposalItem, item_id)
        context = await self._target_context(
            payload, item.review_context or ReviewContext(), item.batch_id, projected
        )
        async with self._connect() as conn:
            await conn.execute(
                update(s.proposal_item)
                .where(self._mine(s.proposal_item), s.proposal_item.c.id == item.id)
                .values(
                    review_context=dumps(context.model_dump(mode="json")), updated_at=self._now()
                )
            )

    async def mark_item_conflict(self, item_id: str, conflict: ReviewConflict) -> None:
        item = await self._get(s.proposal_item, ProposalItem, item_id)
        context = (item.review_context or ReviewContext()).model_copy(update={"conflict": conflict})
        async with self._connect() as conn:
            await conn.execute(
                update(s.proposal_item)
                .where(self._mine(s.proposal_item), s.proposal_item.c.id == item.id)
                .values(
                    review_context=dumps(context.model_dump(mode="json")),
                    decision=Decision.PENDING.value,
                    updated_at=self._now(),
                )
            )

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
                out[table.name] = [
                    {
                        k: export_assertion(v) if k in {"assertion", "review_context"} else v
                        for k, v in r._mapping.items()
                    }
                    for r in await conn.execute(q)
                ]
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
