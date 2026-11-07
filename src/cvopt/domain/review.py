"""Review batches and proposal items, with one payload model per action (P2 §8.2).

Ids inside payloads may reference another item of the same batch as `$item:<seq>`.
"""

from datetime import datetime
from typing import Annotated, Any, Literal

from pydantic import Field

from cvopt.domain.base import CefrLevel, Model, Month
from cvopt.domain.enums import (
    BatchStatus,
    ClaimType,
    Decision,
    DegreeLevel,
    EvidenceKind,
    ProposalAction,
    RecordType,
)
from cvopt.domain.records import FACT_MODELS, ClaimAssertion, EducationLink, FactAssertion

ProfileField = Literal["name", "email", "phone", "city", "country", "website", "linkedin", "github"]


class SetProfileField(Model):
    action: Literal[ProposalAction.SET_PROFILE_FIELD] = ProposalAction.SET_PROFILE_FIELD
    field: ProfileField
    value: str


class CreatePosition(Model):
    excerpt: str = ""
    action: Literal[ProposalAction.CREATE_POSITION] = ProposalAction.CREATE_POSITION
    employer: str
    employer_location: str = ""
    title: str
    start_month: Month
    end_month: Month | None


class UpdatePosition(Model):
    """Fills empty fields only; None leaves a field untouched."""

    excerpt: str = ""
    action: Literal[ProposalAction.UPDATE_POSITION] = ProposalAction.UPDATE_POSITION
    position_id: str
    employer_location: str | None = None
    title: str | None = None
    start_month: Month | None = None
    end_month: Month | None = None


class CreateEducation(Model):
    excerpt: str = ""
    action: Literal[ProposalAction.CREATE_EDUCATION] = ProposalAction.CREATE_EDUCATION
    institution: str
    area: str = ""
    degree_type: str = ""
    degree_level: DegreeLevel
    start_month: Month | None = None
    end_month: Month | None = None
    grade: str = ""
    summary: str = ""
    highlights: list[str] = []
    links: list[EducationLink] = []


class UpdateEducation(Model):
    """Fill missing descriptive fields and append previously unseen details."""

    excerpt: str = ""
    action: Literal[ProposalAction.UPDATE_EDUCATION] = ProposalAction.UPDATE_EDUCATION
    education_id: str
    grade: str = ""
    summary: str = ""
    highlights: list[str] = []
    links: list[EducationLink] = []


class CreateLanguage(Model):
    excerpt: str = ""
    action: Literal[ProposalAction.CREATE_LANGUAGE] = ProposalAction.CREATE_LANGUAGE
    language_code: str
    level: CefrLevel


class CreateCertificate(Model):
    excerpt: str = ""
    action: Literal[ProposalAction.CREATE_CERTIFICATE] = ProposalAction.CREATE_CERTIFICATE
    name: str
    issuer: str = ""
    issued_month: Month | None = None
    url: str = ""


class CreateAward(Model):
    excerpt: str = ""
    action: Literal[ProposalAction.CREATE_AWARD] = ProposalAction.CREATE_AWARD
    title: str
    awarder: str = ""
    awarded_month: Month | None = None
    summary: str = ""


class UpdateCertificate(Model):
    """Fill missing fields without replacing established values."""

    action: Literal[ProposalAction.UPDATE_CERTIFICATE] = ProposalAction.UPDATE_CERTIFICATE
    certificate_id: str
    issuer: str = ""
    issued_month: Month | None = None
    url: str = ""
    excerpt: str = ""


class UpdateAward(Model):
    action: Literal[ProposalAction.UPDATE_AWARD] = ProposalAction.UPDATE_AWARD
    award_id: str
    awarder: str = ""
    awarded_month: Month | None = None
    summary: str = ""
    excerpt: str = ""


class LinkFactSource(Model):
    excerpt: str = ""
    action: Literal[ProposalAction.LINK_FACT_SOURCE] = ProposalAction.LINK_FACT_SOURCE
    record_type: RecordType
    record_id: str


class EvidenceDraft(Model):
    """Evidence to attach; its document is the batch's document."""

    kind: EvidenceKind
    excerpt: str = ""
    reference_name: str = ""
    reference_contact: str = ""


class CreateClaim(Model):
    action: Literal[ProposalAction.CREATE_CLAIM] = ProposalAction.CREATE_CLAIM
    type: ClaimType
    text: str
    text_lang: str
    position_id: str | None
    keywords: list[str]
    evidence: EvidenceDraft


class AddEvidence(Model):
    action: Literal[ProposalAction.ADD_EVIDENCE] = ProposalAction.ADD_EVIDENCE
    claim_id: str
    evidence: EvidenceDraft


class Conflict(Model):
    action: Literal[ProposalAction.CONFLICT] = ProposalAction.CONFLICT
    record_type: Literal[
        "profile", "position", "education", "language", "certificate", "award", "claim"
    ]
    record_id: str
    field: str
    stored_value: str
    document_value: str


class Note(Model):
    action: Literal[ProposalAction.NOTE] = ProposalAction.NOTE
    text: str


Payload = Annotated[
    SetProfileField
    | CreatePosition
    | UpdatePosition
    | CreateEducation
    | UpdateEducation
    | CreateLanguage
    | CreateCertificate
    | CreateAward
    | UpdateCertificate
    | UpdateAward
    | LinkFactSource
    | CreateClaim
    | AddEvidence
    | Conflict
    | Note,
    Field(discriminator="action"),
]


class ReviewConflict(Model):
    reason: str
    prior_target_id: str | None = None
    current_target_id: str | None = None
    prior_assertion: dict[str, Any] | None = None
    current_assertion: dict[str, Any] | None = None
    source_excerpt: str = ""


class ReviewContext(Model):
    """Protected provenance; never accepted from an ordinary payload edit."""

    origin: Literal["captured", "legacy_unknown"] = "legacy_unknown"
    original_payload: Payload | None = None
    original_target_id: str | None = None
    original_target_assertion: dict[str, Any] | None = None
    target_id: str | None = None
    target_assertion: dict[str, Any] | None = None
    target_fingerprint: str | None = None
    conflict: ReviewConflict | None = None


def review_target(payload: Payload) -> tuple[RecordType | Literal["claim"], str] | None:
    if isinstance(payload, AddEvidence):
        return "claim", payload.claim_id
    if isinstance(payload, LinkFactSource):
        return payload.record_type, payload.record_id
    if isinstance(payload, UpdatePosition):
        return RecordType.POSITION, payload.position_id
    if isinstance(payload, UpdateEducation):
        return RecordType.EDUCATION, payload.education_id
    if isinstance(payload, UpdateCertificate):
        return RecordType.CERTIFICATE, payload.certificate_id
    if isinstance(payload, UpdateAward):
        return RecordType.AWARD, payload.award_id
    return None


def created_assertion(payload: Payload, record_id: str) -> ClaimAssertion | FactAssertion | None:
    """Assertion created by a proposal, using a stable batch reference before apply."""
    if isinstance(payload, CreateClaim):
        return ClaimAssertion(
            **payload.model_dump(include={"type", "text", "text_lang", "position_id"})
        )
    record_types = {
        CreatePosition: RecordType.POSITION,
        CreateEducation: RecordType.EDUCATION,
        CreateLanguage: RecordType.LANGUAGE,
        CreateCertificate: RecordType.CERTIFICATE,
        CreateAward: RecordType.AWARD,
    }
    if record_type := record_types.get(type(payload)):
        return FACT_MODELS[record_type].model_validate(
            {**payload.model_dump(exclude={"action", "excerpt"}), "id": record_id}
        )
    return None


class ProposalItem(Model):
    id: str
    batch_id: str
    seq: int
    target_id: str | None = None
    payload: Payload
    decision: Decision = Decision.PENDING
    note: str = ""
    review_context: ReviewContext | None = None

    @property
    def action(self) -> ProposalAction:
        return self.payload.action


class ReviewBatch(Model):
    id: str
    document_id: str
    status: BatchStatus = BatchStatus.OPEN
    applied_at: datetime | None = None
