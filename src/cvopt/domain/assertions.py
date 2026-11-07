"""Stable assertion identity and documentary support validation, shared by all writers."""

import hashlib
import json

from cvopt.domain.enums import DocumentKind, EvidenceKind
from cvopt.domain.facts import sourced_quantities_match
from cvopt.domain.records import Claim, ClaimAssertion, FactAssertion, SourceDocument


def claim_assertion(claim: Claim) -> ClaimAssertion:
    return ClaimAssertion.model_validate(claim.model_dump(include=set(ClaimAssertion.model_fields)))


def assertion_fingerprint(assertion: Claim | ClaimAssertion | FactAssertion) -> str:
    value = claim_assertion(assertion) if isinstance(assertion, Claim) else assertion
    data = value.model_dump(mode="json", exclude={"id"})
    canonical = json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def check_excerpt(excerpt: str, document: SourceDocument, *, required: bool = False) -> None:
    if required and not excerpt.strip():
        raise ValueError("Supporting excerpt is required.")
    if excerpt and (not excerpt.strip() or excerpt not in document.extracted_text):
        raise ValueError("Excerpt must be a verbatim quote of the extracted document text.")


def check_evidence_support(
    kind: EvidenceKind,
    document: SourceDocument | None,
    excerpt: str = "",
    reference_name: str = "",
) -> None:
    allowed = {
        EvidenceKind.OWN_CV: {DocumentKind.CV},
        EvidenceKind.REFERENCE_LETTER: {DocumentKind.REFERENCE_LETTER},
        EvidenceKind.CERTIFICATE_OR_DIPLOMA: {DocumentKind.CERTIFICATE, DocumentKind.DIPLOMA},
    }
    if kind in allowed and (document is None or document.kind not in allowed[kind]):
        raise ValueError(f"{kind.value} evidence needs a document of the matching kind.")
    if kind is EvidenceKind.REFERENCE_PERSON and not reference_name.strip():
        raise ValueError("reference_person evidence needs a reference name.")
    if excerpt and document is None:
        raise ValueError("A documentary excerpt needs a document.")
    if document is not None:
        check_excerpt(excerpt, document, required=kind is EvidenceKind.REFERENCE_LETTER)


def check_fact_support(document: SourceDocument, excerpt: str = "") -> None:
    if document.kind is DocumentKind.OTHER:
        raise ValueError("Fact sources need a CV, reference letter, diploma or certificate.")
    check_excerpt(excerpt, document, required=document.kind is DocumentKind.REFERENCE_LETTER)


def check_documentary_assertion(
    claim: Claim | ClaimAssertion,
    kind: EvidenceKind,
    document: SourceDocument | None,
    excerpt: str = "",
) -> None:
    """Reject known numeric contradictions without claiming semantic verification."""
    if (
        document is not None
        and kind
        in {EvidenceKind.OWN_CV, EvidenceKind.REFERENCE_LETTER, EvidenceKind.CERTIFICATE_OR_DIPLOMA}
        and not sourced_quantities_match(excerpt or document.extracted_text, claim.text)
    ):
        raise ValueError("Documentary support does not contain the claim's quantity anchors.")
