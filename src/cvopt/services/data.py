"""DataService (F2): manual management of the profile, fact records, claims and evidence."""

from dataclasses import dataclass
from pathlib import PurePath
from typing import Any

from cvopt.context import UserContext
from cvopt.domain.enums import ClaimStatus, ClaimType, EvidenceKind, Origin, Provability, RecordType
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
from cvopt.domain.rules import claim_provability
from cvopt.errors import ConflictError, InputError
from cvopt.ports import FileStore, Repository, RepositoryFactory

MANUAL_EVIDENCE = {EvidenceKind.SELF_DECLARED, EvidenceKind.LEARNABLE_DECLARED}


@dataclass(frozen=True)
class ClaimWithEvidence:
    claim: Claim
    evidence: list[Evidence]


async def recompute_provability(repo: Repository, claim_ids: list[str]) -> list[Claim]:
    """Re-derive provability; a claim left without evidence returns to `proposed`."""
    evidence = await repo.list_evidence(claim_ids)
    updated = []
    for cid in claim_ids:
        kinds = [e.kind for e in evidence if e.claim_id == cid]
        values: dict[str, Any] = {"provability": claim_provability(kinds)}
        if not kinds:
            values["status"] = ClaimStatus.PROPOSED
        updated.append(await repo.update_claim(cid, values))
    return updated


class DataService:
    def __init__(self, repos: RepositoryFactory, files: FileStore) -> None:
        self._repos = repos
        self._files = files

    # --- profile -------------------------------------------------------------

    async def get_profile(self, user: UserContext) -> Profile:
        return await self._repos.for_user(user).get_profile()

    async def update_profile(self, user: UserContext, fields: dict[str, Any]) -> Profile:
        repo = self._repos.for_user(user)
        async with repo.transaction():
            current = await repo.get_profile()
            unknown = set(fields) - set(Profile.model_fields)
            if unknown:
                raise InputError(f"Unknown profile field: {', '.join(sorted(unknown))}")
            new = Profile.model_validate({**current.model_dump(), **fields})
            return await repo.save_profile(new)

    # --- positions -------------------------------------------------------------

    async def list_positions(self, user: UserContext) -> list[Position]:
        return await self._repos.for_user(user).list_positions()

    async def get_position(self, user: UserContext, ref: str) -> Position:
        return await self._repos.for_user(user).get_position(ref)

    async def create_position(self, user: UserContext, fields: dict[str, Any]) -> Position:
        return await self._repos.for_user(user).insert_position(fields)

    async def update_position(
        self, user: UserContext, ref: str, fields: dict[str, Any]
    ) -> Position:
        return await self._repos.for_user(user).update_position(ref, fields)

    async def delete_position(self, user: UserContext, ref: str) -> str:
        repo = self._repos.for_user(user)
        async with repo.transaction():
            pos = await repo.get_position(ref)
            n = await repo.count_claims(pos.id)
            if n:
                raise ConflictError(
                    f"Position {pos.id} has {n} claim(s); move or delete them first."
                )
            await repo.detach_documents_from_position(pos.id)
            await repo.delete_fact_sources(RecordType.POSITION, pos.id)
            return await repo.delete_position(pos.id)

    # --- education, languages, certificates -------------------------------------

    async def list_education(self, user: UserContext) -> list[Education]:
        return await self._repos.for_user(user).list_education()

    async def create_education(self, user: UserContext, fields: dict[str, Any]) -> Education:
        return await self._repos.for_user(user).insert_education(fields)

    async def update_education(
        self, user: UserContext, ref: str, fields: dict[str, Any]
    ) -> Education:
        return await self._repos.for_user(user).update_education(ref, fields)

    async def delete_education(self, user: UserContext, ref: str) -> str:
        repo = self._repos.for_user(user)
        async with repo.transaction():
            rid = await repo.delete_education(ref)
            await repo.delete_fact_sources(RecordType.EDUCATION, rid)
            return rid

    async def list_languages(self, user: UserContext) -> list[Language]:
        return await self._repos.for_user(user).list_languages()

    async def create_language(self, user: UserContext, fields: dict[str, Any]) -> Language:
        return await self._repos.for_user(user).insert_language(fields)

    async def update_language(
        self, user: UserContext, ref: str, fields: dict[str, Any]
    ) -> Language:
        return await self._repos.for_user(user).update_language(ref, fields)

    async def delete_language(self, user: UserContext, ref: str) -> str:
        repo = self._repos.for_user(user)
        async with repo.transaction():
            rid = await repo.delete_language(ref)
            await repo.delete_fact_sources(RecordType.LANGUAGE, rid)
            return rid

    async def list_certificates(self, user: UserContext) -> list[Certificate]:
        return await self._repos.for_user(user).list_certificates()

    async def create_certificate(self, user: UserContext, fields: dict[str, Any]) -> Certificate:
        return await self._repos.for_user(user).insert_certificate(fields)

    async def update_certificate(
        self, user: UserContext, ref: str, fields: dict[str, Any]
    ) -> Certificate:
        return await self._repos.for_user(user).update_certificate(ref, fields)

    async def delete_certificate(self, user: UserContext, ref: str) -> str:
        repo = self._repos.for_user(user)
        async with repo.transaction():
            rid = await repo.delete_certificate(ref)
            await repo.delete_fact_sources(RecordType.CERTIFICATE, rid)
            return rid

    # --- claims ------------------------------------------------------------------

    async def list_claims(
        self,
        user: UserContext,
        *,
        type: ClaimType | None = None,
        position: str | None = None,
        provability: Provability | None = None,
        status: ClaimStatus | None = None,
    ) -> list[ClaimWithEvidence]:
        repo = self._repos.for_user(user)
        async with repo.transaction():
            position_id = (await repo.get_position(position)).id if position else None
            claims = await repo.list_claims(
                type=type, position_id=position_id, provability=provability, status=status
            )
            evidence = await repo.list_evidence([c.id for c in claims])
        return [ClaimWithEvidence(c, [e for e in evidence if e.claim_id == c.id]) for c in claims]

    async def get_claim(self, user: UserContext, ref: str) -> ClaimWithEvidence:
        repo = self._repos.for_user(user)
        async with repo.transaction():
            claim = await repo.get_claim(ref)
            return ClaimWithEvidence(claim, await repo.list_evidence([claim.id]))

    async def create_claim(
        self,
        user: UserContext,
        *,
        type: ClaimType,
        text: str,
        text_lang: str,
        position: str | None = None,
        keywords: list[str] | None = None,
        learnable: bool = False,
    ) -> ClaimWithEvidence:
        """A manual claim: confirmed, with `self_declared` or `learnable_declared` evidence."""
        kind = EvidenceKind.LEARNABLE_DECLARED if learnable else EvidenceKind.SELF_DECLARED
        repo = self._repos.for_user(user)
        async with repo.transaction():
            position_id = (await repo.get_position(position)).id if position else None
            claim = await repo.insert_claim(
                {
                    "type": type,
                    "text": text,
                    "text_lang": text_lang,
                    "position_id": position_id,
                    "keywords": keywords or [],
                    "status": ClaimStatus.CONFIRMED,
                    "origin": Origin.MANUAL,
                    "provability": claim_provability([kind]),
                }
            )
            ev = await repo.insert_evidence({"claim_id": claim.id, "kind": kind})
        return ClaimWithEvidence(claim, [ev])

    async def update_claim(self, user: UserContext, ref: str, fields: dict[str, Any]) -> Claim:
        """Edits type, text, language, position or keywords; provability stays derived."""
        allowed = {"type", "text", "text_lang", "position_id", "keywords", "status"}
        if unknown := set(fields) - allowed:
            raise InputError(f"Cannot edit claim field: {', '.join(sorted(unknown))}")
        repo = self._repos.for_user(user)
        async with repo.transaction():
            if fields.get("position_id"):
                fields = {
                    **fields,
                    "position_id": (await repo.get_position(fields["position_id"])).id,
                }
            return await repo.update_claim(ref, fields)

    async def delete_claim(self, user: UserContext, ref: str) -> str:
        return await self._repos.for_user(user).delete_claim(ref)

    # --- evidence -------------------------------------------------------------------

    async def add_evidence(
        self,
        user: UserContext,
        claim_ref: str,
        *,
        kind: EvidenceKind,
        document: str | None = None,
        excerpt: str = "",
        reference_name: str = "",
        reference_contact: str = "",
    ) -> ClaimWithEvidence:
        if kind is EvidenceKind.REFERENCE_PERSON and not reference_name:
            raise InputError("reference_person evidence needs a reference name.")
        repo = self._repos.for_user(user)
        async with repo.transaction():
            claim = await repo.get_claim(claim_ref)
            document_id = (await repo.get_document(document)).id if document else None
            await repo.insert_evidence(
                {
                    "claim_id": claim.id,
                    "kind": kind,
                    "document_id": document_id,
                    "excerpt": excerpt,
                    "reference_name": reference_name,
                    "reference_contact": reference_contact,
                }
            )
            [claim] = await recompute_provability(repo, [claim.id])
            return ClaimWithEvidence(claim, await repo.list_evidence([claim.id]))

    async def remove_evidence(self, user: UserContext, ref: str) -> ClaimWithEvidence:
        repo = self._repos.for_user(user)
        async with repo.transaction():
            ev = await repo.get_evidence(ref)
            await repo.delete_evidence(ev.id)
            [claim] = await recompute_provability(repo, [ev.claim_id])
            return ClaimWithEvidence(claim, await repo.list_evidence([claim.id]))

    # --- documents ------------------------------------------------------------------

    async def list_documents(self, user: UserContext) -> list[SourceDocument]:
        return await self._repos.for_user(user).list_documents()

    async def delete_document(self, user: UserContext, ref: str) -> list[Claim]:
        """Deletes the document with its evidence and links; returns the affected claims."""
        repo = self._repos.for_user(user)
        async with repo.transaction():
            doc = await repo.get_document(ref)
            affected = await repo.claims_with_document_evidence(doc.id)
            await repo.delete_document(doc.id)
            claims = await recompute_provability(repo, affected)
        ext = PurePath(doc.filename).suffix
        self._files.document_path(user, doc.id, ext).unlink(missing_ok=True)
        return claims
