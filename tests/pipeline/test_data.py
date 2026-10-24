"""DataService rules: refused position delete, provability recomputation, document delete."""

import pytest

from cvopt.bootstrap import Container
from cvopt.context import UserContext
from cvopt.domain.enums import ClaimStatus, ClaimType, DocumentKind, EvidenceKind, Provability
from cvopt.errors import ConflictError, InputError
from tests.conftest import TEST_NOW

POSITION = {"employer": "Acme", "title": "Dev", "start_month": "2020-01", "end_month": None}


async def test_deleting_position_with_claims_is_refused(
    container: Container, alice: UserContext
) -> None:
    d = container.data
    pos = await d.create_position(alice, POSITION)
    claim = await d.create_claim(
        alice, type=ClaimType.SKILL, text="Go", text_lang="en", position=pos.id
    )
    with pytest.raises(ConflictError):
        await d.delete_position(alice, pos.id)
    await d.delete_claim(alice, claim.claim.id)
    await d.delete_position(alice, pos.id)
    assert await d.list_positions(alice) == []


async def test_provability_follows_evidence(container: Container, alice: UserContext) -> None:
    d = container.data
    r = await d.create_claim(
        alice, type=ClaimType.SKILL, text="Rust", text_lang="en", learnable=True
    )
    assert r.claim.provability is Provability.LOW
    r = await d.add_evidence(alice, r.claim.id, kind=EvidenceKind.SELF_DECLARED)
    assert r.claim.provability is Provability.MEDIUM
    r = await d.add_evidence(
        alice, r.claim.id, kind=EvidenceKind.REFERENCE_PERSON, reference_name="Carol"
    )
    assert r.claim.provability is Provability.HIGH
    for ev in r.evidence:
        r = await d.remove_evidence(alice, ev.id)
    assert r.claim.provability is Provability.NONE
    assert r.claim.status is ClaimStatus.PROPOSED


async def test_reference_person_needs_name(container: Container, alice: UserContext) -> None:
    r = await container.data.create_claim(alice, type=ClaimType.SKILL, text="x", text_lang="en")
    with pytest.raises(InputError):
        await container.data.add_evidence(alice, r.claim.id, kind=EvidenceKind.REFERENCE_PERSON)


async def test_deleting_document_recomputes_claims(
    container: Container, alice: UserContext
) -> None:
    d = container.data
    doc = await container.repos.for_user(alice).insert_document(
        {
            "kind": DocumentKind.REFERENCE_LETTER,
            "filename": "ref.pdf",
            "sha256": "cd" * 32,
            "mime_type": "application/pdf",
            "received_at": TEST_NOW,
        }
    )
    r = await d.create_claim(
        alice, type=ClaimType.ACHIEVEMENT, text="Cut costs 20%", text_lang="en"
    )
    r = await d.add_evidence(
        alice, r.claim.id, kind=EvidenceKind.REFERENCE_LETTER, document=doc.id, excerpt="..."
    )
    assert r.claim.provability is Provability.HIGH
    [affected] = await d.delete_document(alice, doc.id)
    assert affected.provability is Provability.MEDIUM
    assert affected.status is ClaimStatus.CONFIRMED
    assert await d.list_documents(alice) == []


async def test_invalid_values_raise_input_error(container: Container, alice: UserContext) -> None:
    with pytest.raises(InputError):
        await container.data.create_position(alice, {**POSITION, "start_month": "2020-13"})
    with pytest.raises(InputError):
        await container.data.update_profile(alice, {"nickname": "x"})
