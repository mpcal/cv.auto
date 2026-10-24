"""Profile, fact records, claims, evidence and source documents (P2 §4.3)."""

from datetime import datetime

from cvopt.domain.base import CefrLevel, Model, Month
from cvopt.domain.enums import (
    ClaimStatus,
    ClaimType,
    DegreeLevel,
    DocumentKind,
    EvidenceKind,
    Origin,
    Provability,
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
    profiles: list[OnlineProfile] = []


class Position(Model):
    id: str
    employer: str
    employer_location: str = ""
    title: str
    start_month: Month
    end_month: Month | None  # None = current


class Education(Model):
    id: str
    institution: str
    area: str = ""
    degree_type: str = ""  # as written
    degree_level: DegreeLevel
    start_month: Month | None = None
    end_month: Month | None = None
    grade: str = ""


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


class Evidence(Model):
    id: str
    claim_id: str
    kind: EvidenceKind
    document_id: str | None = None
    excerpt: str = ""
    reference_name: str = ""
    reference_contact: str = ""


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
