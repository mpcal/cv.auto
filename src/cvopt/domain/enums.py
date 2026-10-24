"""Domain enums. Parsing is case-insensitive, since structured outputs do not
guarantee the casing of enum values."""

from enum import StrEnum
from typing import Self


class CIStrEnum(StrEnum):
    @classmethod
    def _missing_(cls, value: object) -> Self | None:
        if isinstance(value, str):
            folded = value.strip().casefold()
            for member in cls:
                if member.value == folded:
                    return member
        return None


class ClaimType(CIStrEnum):
    SKILL = "skill"
    EXPERIENCE = "experience"
    ACHIEVEMENT = "achievement"


class ClaimStatus(CIStrEnum):
    CONFIRMED = "confirmed"
    PROPOSED = "proposed"


class Origin(CIStrEnum):
    EXTRACTED = "extracted"
    MANUAL = "manual"


class EvidenceKind(CIStrEnum):
    REFERENCE_LETTER = "reference_letter"
    CERTIFICATE_OR_DIPLOMA = "certificate_or_diploma"
    REFERENCE_PERSON = "reference_person"
    OWN_CV = "own_cv"
    SELF_DECLARED = "self_declared"
    LEARNABLE_DECLARED = "learnable_declared"


class Provability(CIStrEnum):
    NONE = "none"
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class Fidelity(CIStrEnum):
    UNMODIFIED = "unmodified"
    REPHRASED = "rephrased"
    EXAGGERATED = "exaggerated"
    INVENTED = "invented"


class MatchLevel(CIStrEnum):
    NONE = "none"
    PARTIAL = "partial"
    SEMANTIC = "semantic"
    SYNONYM = "synonym"
    EXACT = "exact"


class RequirementCategory(CIStrEnum):
    HARD_SKILL = "hard_skill"
    SOFT_SKILL = "soft_skill"
    EXPERIENCE = "experience"
    DOMAIN = "domain"
    YEARS_EXPERIENCE = "years_experience"
    EDUCATION = "education"
    CERTIFICATION = "certification"
    LANGUAGE = "language"
    LOGISTICS = "logistics"


class Importance(CIStrEnum):
    MUST = "must"
    NICE = "nice"


class Slot(CIStrEnum):
    HEADLINE = "headline"
    SUMMARY = "summary"
    HIGHLIGHT = "highlight"
    SKILL = "skill"


class ElementKind(CIStrEnum):
    HEADLINE = "headline"
    SUMMARY = "summary"
    HIGHLIGHT = "highlight"
    SKILL = "skill"
    EDUCATION = "education"
    LANGUAGE = "language"
    CERTIFICATE = "certificate"


class DocumentKind(CIStrEnum):
    CV = "cv"
    REFERENCE_LETTER = "reference_letter"
    DIPLOMA = "diploma"
    CERTIFICATE = "certificate"
    OTHER = "other"


class DegreeLevel(CIStrEnum):
    """Ordered secondary < ... < doctorate; `other` is outside the order."""

    SECONDARY = "secondary"
    VOCATIONAL = "vocational"
    BACHELOR = "bachelor"
    MASTER = "master"
    DOCTORATE = "doctorate"
    OTHER = "other"


class ProposalAction(CIStrEnum):
    SET_PROFILE_FIELD = "set_profile_field"
    CREATE_POSITION = "create_position"
    UPDATE_POSITION = "update_position"
    CREATE_EDUCATION = "create_education"
    CREATE_LANGUAGE = "create_language"
    CREATE_CERTIFICATE = "create_certificate"
    LINK_FACT_SOURCE = "link_fact_source"
    CREATE_CLAIM = "create_claim"
    ADD_EVIDENCE = "add_evidence"
    CONFLICT = "conflict"
    NOTE = "note"


class Decision(CIStrEnum):
    PENDING = "pending"
    ACCEPT = "accept"
    REJECT = "reject"


class BatchStatus(CIStrEnum):
    OPEN = "open"
    APPLIED = "applied"
    DISCARDED = "discarded"


class RecordType(CIStrEnum):
    """Fact records that can be linked to source documents."""

    POSITION = "position"
    EDUCATION = "education"
    LANGUAGE = "language"
    CERTIFICATE = "certificate"


class RowKind(CIStrEnum):
    IN_CV = "in_cv"
    GAP = "gap"
    DROPPED = "dropped"


PROVABILITY_RANK = {p: i for i, p in enumerate(Provability)}
FIDELITY_RANK = {f: i for i, f in enumerate(Fidelity)}
MATCH_RANK = {m: i for i, m in enumerate(MatchLevel)}
DEGREE_RANK = {d: i for i, d in enumerate(DegreeLevel) if d is not DegreeLevel.OTHER}
