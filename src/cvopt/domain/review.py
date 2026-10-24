"""Review batches and proposal items, with one payload model per action (P2 §8.2).

Ids inside payloads may reference another item of the same batch as `$item:<seq>`.
"""

from datetime import datetime
from typing import Annotated, Literal

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

ProfileField = Literal["name", "email", "phone", "city", "country", "website"]


class SetProfileField(Model):
    action: Literal[ProposalAction.SET_PROFILE_FIELD] = ProposalAction.SET_PROFILE_FIELD
    field: ProfileField
    value: str


class CreatePosition(Model):
    action: Literal[ProposalAction.CREATE_POSITION] = ProposalAction.CREATE_POSITION
    employer: str
    employer_location: str = ""
    title: str
    start_month: Month
    end_month: Month | None


class UpdatePosition(Model):
    """Fills empty fields only; None leaves a field untouched."""

    action: Literal[ProposalAction.UPDATE_POSITION] = ProposalAction.UPDATE_POSITION
    position_id: str
    employer_location: str | None = None
    title: str | None = None
    start_month: Month | None = None
    end_month: Month | None = None


class CreateEducation(Model):
    action: Literal[ProposalAction.CREATE_EDUCATION] = ProposalAction.CREATE_EDUCATION
    institution: str
    area: str = ""
    degree_type: str = ""
    degree_level: DegreeLevel
    start_month: Month | None = None
    end_month: Month | None = None
    grade: str = ""


class CreateLanguage(Model):
    action: Literal[ProposalAction.CREATE_LANGUAGE] = ProposalAction.CREATE_LANGUAGE
    language_code: str
    level: CefrLevel


class CreateCertificate(Model):
    action: Literal[ProposalAction.CREATE_CERTIFICATE] = ProposalAction.CREATE_CERTIFICATE
    name: str
    issuer: str = ""
    issued_month: Month | None = None
    url: str = ""


class LinkFactSource(Model):
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
    record_type: Literal["profile", "position", "education", "language", "certificate", "claim"]
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
    | CreateLanguage
    | CreateCertificate
    | LinkFactSource
    | CreateClaim
    | AddEvidence
    | Conflict
    | Note,
    Field(discriminator="action"),
]


class ProposalItem(Model):
    id: str
    batch_id: str
    seq: int
    target_id: str | None = None
    payload: Payload
    decision: Decision = Decision.PENDING
    note: str = ""

    @property
    def action(self) -> ProposalAction:
        return self.payload.action


class ReviewBatch(Model):
    id: str
    document_id: str
    status: BatchStatus = BatchStatus.OPEN
    applied_at: datetime | None = None
