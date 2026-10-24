"""After Alice adds data, Bob sees none of it, and Alice's ids are unknown to Bob."""

from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from cvopt.bootstrap import Container
from cvopt.context import UserContext
from cvopt.domain.enums import ClaimType, DocumentKind, EvidenceKind
from cvopt.errors import NotFoundError
from tests.conftest import TEST_NOW


async def seed(c: Container, user: UserContext) -> dict[str, str]:
    d = c.data
    pos = await d.create_position(
        user, {"employer": "Acme", "title": "Dev", "start_month": "2020-01", "end_month": None}
    )
    edu = await d.create_education(
        user, {"institution": "ETH", "degree_type": "MSc", "degree_level": "master"}
    )
    lang = await d.create_language(user, {"language_code": "de", "level": "C1"})
    cert = await d.create_certificate(user, {"name": "AWS SA"})
    claim = await d.create_claim(
        user, type=ClaimType.SKILL, text="Python", text_lang="en", position=pos.id
    )
    doc = await c.repos.for_user(user).insert_document(
        {
            "kind": DocumentKind.CV,
            "filename": "cv.pdf",
            "sha256": "ab" * 32,
            "mime_type": "application/pdf",
            "received_at": TEST_NOW,
        }
    )
    await d.update_profile(user, {"name": "Alice"})
    return {
        "position": pos.id,
        "education": edu.id,
        "language": lang.id,
        "certificate": cert.id,
        "claim": claim.claim.id,
        "evidence": claim.evidence[0].id,
        "document": doc.id,
    }


async def test_bob_sees_nothing_of_alice(
    container: Container, alice: UserContext, bob: UserContext
) -> None:
    await seed(container, alice)
    d = container.data
    assert await d.list_positions(bob) == []
    assert await d.list_education(bob) == []
    assert await d.list_languages(bob) == []
    assert await d.list_certificates(bob) == []
    assert await d.list_claims(bob) == []
    assert await d.list_documents(bob) == []
    assert (await d.get_profile(bob)).name == ""
    assert len(await d.list_positions(alice)) == 1


async def test_alice_ids_raise_not_found_for_bob(
    container: Container, alice: UserContext, bob: UserContext
) -> None:
    ids = await seed(container, alice)
    d = container.data
    ops: list[Callable[[], Awaitable[Any]]] = [
        lambda: d.get_position(bob, ids["position"]),
        lambda: d.update_position(bob, ids["position"], {"title": "x"}),
        lambda: d.delete_position(bob, ids["position"]),
        lambda: d.update_education(bob, ids["education"], {"grade": "6"}),
        lambda: d.delete_education(bob, ids["education"]),
        lambda: d.delete_language(bob, ids["language"]),
        lambda: d.delete_certificate(bob, ids["certificate"]),
        lambda: d.get_claim(bob, ids["claim"]),
        lambda: d.update_claim(bob, ids["claim"], {"text": "x"}),
        lambda: d.delete_claim(bob, ids["claim"]),
        lambda: d.add_evidence(bob, ids["claim"], kind=EvidenceKind.OWN_CV),
        lambda: d.remove_evidence(bob, ids["evidence"]),
        lambda: d.delete_document(bob, ids["document"]),
        lambda: d.get_position(bob, ids["position"][-8:]),  # suffixes too
    ]
    for op in ops:
        with pytest.raises(NotFoundError):
            await op()
    bob_pos = await d.create_position(
        bob, {"employer": "B", "title": "T", "start_month": "2021-01", "end_month": None}
    )
    with pytest.raises(NotFoundError):  # Bob cannot tie a claim to Alice's position
        await d.update_claim(bob, ids["claim"], {"position_id": bob_pos.id})
    with pytest.raises(NotFoundError):
        await d.create_claim(
            bob, type=ClaimType.SKILL, text="x", text_lang="en", position=ids["position"]
        )
    assert len(await d.list_claims(alice)) == 1
