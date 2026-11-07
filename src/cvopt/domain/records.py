"""Profile, fact records, claims, evidence and source documents (P2 §4.3)."""

from datetime import datetime
from typing import Any, Literal

from pydantic import model_validator

from cvopt.domain.base import CefrLevel, Model, Month
from cvopt.domain.enums import (
    ClaimStatus,
    ClaimType,
    DegreeLevel,
    DocumentKind,
    EvidenceKind,
    Origin,
    Provability,
    RecordType,
)


class OnlineProfile(Model):
    network: str
    url: str


class Profile(Model):
    name: str = ""
    email: str = ""
    phone: str = ""
    city: str = ""
    country: str = ""
    website: str = ""
    linkedin: str = ""  # profile URL as written
    github: str = ""  # profile URL as written
    profiles: list[OnlineProfile] = []


class Position(Model):
    id: str
    employer: str
    employer_location: str = ""
    title: str
    start_month: Month
    end_month: Month | None  # None = current


class EducationLink(Model):
    label: str
    url: str


class Education(Model):
    id: str
    institution: str
    area: str = ""
    degree_type: str = ""  # as written
    degree_level: DegreeLevel
    start_month: Month | None = None
    end_month: Month | None = None
    grade: str = ""
    summary: str = ""
    highlights: list[str] = []
    links: list[EducationLink] = []


class Language(Model):
    id: str
    language_code: str  # ISO 639-1
    level: CefrLevel


class Certificate(Model):
    id: str
    name: str
    issuer: str = ""
    issued_month: Month | None = None
    url: str = ""


class Award(Model):
    id: str
    title: str
    awarder: str = ""
    awarded_month: Month | None = None
    summary: str = ""


class Claim(Model):
    id: str
    type: ClaimType
    text: str
    text_lang: str  # ISO 639-1
    position_id: str | None
    keywords: list[str]
    status: ClaimStatus
    origin: Origin
    provability: Provability  # always set by rules.claim_provability()


class ClaimAssertion(Model):
    """Material claim fields; search hints and derived state are excluded."""

    type: ClaimType
    text: str
    text_lang: str
    position_id: str | None


FactAssertion = Position | Education | Language | Certificate | Award


class Attachment(Model):
    state: Literal["active", "stale"] = "stale"
    snapshot_origin: Literal["captured", "legacy_backfill"] = "legacy_backfill"


class InvalidAttachment(Model):
    """Structurally unreadable history; never usable as assertion support."""

    id: str
    attachment_type: Literal["evidence", "fact_source"]
    state: Literal["invalid"] = "invalid"
    validation_error: str
    raw: dict[str, Any]


class Evidence(Attachment):
    id: str
    claim_id: str
    kind: EvidenceKind
    document_id: str | None = None
    excerpt: str = ""
    reference_name: str = ""
    reference_contact: str = ""
    assertion: ClaimAssertion | None = None


class FactSource(Attachment):
    id: str
    record_type: RecordType
    record_id: str
    document_id: str
    excerpt: str = ""
    assertion: FactAssertion | None = None

    @model_validator(mode="before")
    @classmethod
    def typed_assertion(cls, values: Any) -> Any:
        if isinstance(values, dict) and isinstance(values.get("assertion"), dict):
            model = FACT_MODELS[RecordType(values["record_type"])]
            values = {**values, "assertion": model.model_validate(values["assertion"])}
        return values


FACT_MODELS: dict[RecordType, type[Position | Education | Language | Certificate | Award]] = {
    RecordType.POSITION: Position,
    RecordType.EDUCATION: Education,
    RecordType.LANGUAGE: Language,
    RecordType.CERTIFICATE: Certificate,
    RecordType.AWARD: Award,
}


class SourceDocument(Model):
    id: str
    kind: DocumentKind
    filename: str
    sha256: str
    mime_type: str
    language: str = ""
    issuer: str = ""
    position_id: str | None = None
    extracted_text: str = ""
    received_at: datetime
