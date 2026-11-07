"""Assertions lose documentary support after edits; history remains reviewable."""

import pytest

from cvopt.bootstrap import Container
from cvopt.context import UserContext
from cvopt.domain.enums import (
    ClaimStatus,
    ClaimType,
    DocumentKind,
    EvidenceKind,
    Provability,
    RecordType,
)
from cvopt.errors import InputError, NotFoundError
from tests.conftest import TEST_NOW


async def document(repo, kind=DocumentKind.REFERENCE_LETTER, text="Led two teams"):
    return await repo.insert_document(
        dict(
            kind=kind,
            filename="ref.pdf",
            sha256=kind.value * 4,
            mime_type="application/pdf",
            received_at=TEST_NOW,
            extracted_text=text,
        )
    )


@pytest.mark.parametrize(
    ("source", "assertion"),
    [
        ("Managed two million CHF", "Verwaltete 2 Millionen CHF"),
        ("Managed 2.5 million CHF", "Verwaltete 2.500.000 CHF"),
        ("Served 2,000 users", "Betreute 2\u202f000 Nutzer"),
    ],
)
@pytest.mark.parametrize("reverse", [False, True])
async def test_documentary_evidence_accepts_complete_equivalent_quantities(
    container, alice, source, assertion, reverse
):
    if reverse:
        source, assertion = assertion, source
    doc = await document(container.repos.for_user(alice), text=source)
    result = await container.data.create_claim(
        alice, type=ClaimType.ACHIEVEMENT, text=assertion, text_lang="en" if reverse else "de"
    )
    supported = await container.data.add_evidence(
        alice,
        result.claim.id,
        kind=EvidenceKind.REFERENCE_LETTER,
        document=doc.id,
        excerpt=source,
    )
    assert supported.claim.provability is Provability.HIGH
    assert any(e.kind is EvidenceKind.REFERENCE_LETTER for e in supported.evidence)


async def test_edit_downgrades_high_and_retains_old_assertion(
    container: Container, alice: UserContext
):
    d, repo = container.data, container.repos.for_user(alice)
    r = await d.create_claim(alice, type=ClaimType.EXPERIENCE, text="Led two teams", text_lang="en")
    doc = await document(repo)
    r = await d.add_evidence(
        alice,
        r.claim.id,
        kind=EvidenceKind.REFERENCE_LETTER,
        document=doc.id,
        excerpt="Led two teams",
    )
    old = next(e for e in r.evidence if e.kind is EvidenceKind.REFERENCE_LETTER)
    edited = await d.update_claim(alice, r.claim.id, {"text": "Managed two teams"})
    assert edited.provability is Provability.MEDIUM
    history = await repo.get_evidence(old.id)
    assert history.state == "stale"
    assert history.assertion.text == "Led two teams"
    current = await d.get_claim(alice, r.claim.id)
    assert len([e for e in current.evidence if e.state == "active"]) == 1
    assert (
        next(e for e in current.evidence if e.state == "active").kind is EvidenceKind.SELF_DECLARED
    )
    restored = await d.reconfirm_evidence(alice, old.id)
    assert restored.claim.provability is Provability.HIGH
    assert (await repo.get_evidence(old.id)).state == "stale"
    assert len(restored.evidence) == 4
    assert (
        next(
            e
            for e in restored.evidence
            if e.state == "active" and e.kind is EvidenceKind.REFERENCE_LETTER
        ).assertion.text
        == "Managed two teams"
    )
    rows = await repo.export_rows()
    assert isinstance(rows["evidence"][0]["assertion"], dict)


async def test_learnable_edit_remains_low_and_keyword_edits_keep_support(container, alice):
    d = container.data
    r = await d.create_claim(
        alice, type=ClaimType.SKILL, text="Rust", text_lang="en", learnable=True
    )
    r2 = await d.update_claim(alice, r.claim.id, {"keywords": ["systems"]})
    assert r2.provability is Provability.LOW
    assert (await d.get_claim(alice, r.claim.id)).evidence == r.evidence
    r2 = await d.update_claim(alice, r.claim.id, {"text": "Go"})
    assert r2.provability is Provability.LOW
    ev = (await d.get_claim(alice, r.claim.id)).evidence
    assert [e.kind for e in ev if e.state == "active"] == [EvidenceKind.LEARNABLE_DECLARED]


async def test_proposed_needs_explicit_confirmation_and_valid_evidence(container, alice):
    d = container.data
    r = await d.create_claim(alice, type=ClaimType.SKILL, text="Go", text_lang="en")
    await d.remove_evidence(alice, r.evidence[0].id)
    with pytest.raises(InputError):
        await d.update_claim(alice, r.claim.id, {"status": ClaimStatus.CONFIRMED})
    with pytest.raises(InputError):
        await d.confirm_claim(alice, r.claim.id)
    r = await d.add_evidence(alice, r.claim.id, kind=EvidenceKind.SELF_DECLARED)
    assert r.claim.status is ClaimStatus.PROPOSED
    confirmed = await d.confirm_claim(alice, r.claim.id)
    assert confirmed.status is ClaimStatus.CONFIRMED


@pytest.mark.parametrize(
    "kind,doc_kind,excerpt,name",
    [
        (EvidenceKind.OWN_CV, None, "", ""),
        (EvidenceKind.CERTIFICATE_OR_DIPLOMA, None, "", ""),
        (EvidenceKind.REFERENCE_LETTER, None, "Led two teams", ""),
        (EvidenceKind.OWN_CV, DocumentKind.REFERENCE_LETTER, "", ""),
        (EvidenceKind.REFERENCE_LETTER, DocumentKind.REFERENCE_LETTER, "", ""),
        (EvidenceKind.REFERENCE_LETTER, DocumentKind.REFERENCE_LETTER, "invented quote", ""),
        (EvidenceKind.CERTIFICATE_OR_DIPLOMA, DocumentKind.CERTIFICATE, "invented quote", ""),
        (EvidenceKind.REFERENCE_PERSON, None, "", "  "),
    ],
)
async def test_reject_invalid_support(container, alice, kind, doc_kind, excerpt, name):
    d, repo = container.data, container.repos.for_user(alice)
    r = await d.create_claim(alice, type=ClaimType.SKILL, text="Go", text_lang="en")
    doc = await document(repo, doc_kind) if doc_kind else None
    with pytest.raises(InputError):
        await d.add_evidence(
            alice,
            r.claim.id,
            kind=kind,
            document=doc.id if doc else None,
            excerpt=excerpt,
            reference_name=name,
        )
    assert len((await d.get_claim(alice, r.claim.id)).evidence) == 1


async def test_document_is_owned_and_repository_cannot_bypass_validation(container, alice, bob):
    a, b = container.repos.for_user(alice), container.repos.for_user(bob)
    doc = await document(b)
    claim = (
        await container.data.create_claim(alice, type=ClaimType.SKILL, text="Go", text_lang="en")
    ).claim
    with pytest.raises(NotFoundError):
        await a.insert_evidence(
            dict(
                claim_id=claim.id,
                kind=EvidenceKind.REFERENCE_LETTER,
                document_id=doc.id,
                excerpt="Led two teams",
            )
        )
    with pytest.raises(InputError):
        await a.insert_evidence(dict(claim_id=claim.id, kind=EvidenceKind.REFERENCE_LETTER))


@pytest.mark.parametrize(
    "record_type,fields,edit",
    [
        (
            RecordType.POSITION,
            dict(employer="Acme", title="Dev", start_month="2020-01", end_month=None),
            {"title": "Lead"},
        ),
        (
            RecordType.EDUCATION,
            dict(institution="Uni", degree_level="master"),
            {"degree_level": "doctorate"},
        ),
        (RecordType.LANGUAGE, dict(language_code="en", level="B2"), {"level": "C2"}),
        (RecordType.CERTIFICATE, dict(name="Cert"), {"name": "Other"}),
        (RecordType.AWARD, dict(title="Award"), {"title": "Other"}),
    ],
)
async def test_fixed_record_edit_stales_support_and_same_document_can_reattach(
    container, alice, record_type, fields, edit
):
    repo = container.repos.for_user(alice)
    doc = await document(repo, DocumentKind.CV)
    rec = await getattr(repo, "insert_" + record_type.value)(fields)
    await repo.insert_fact_source(record_type, rec.id, doc.id)
    assert await repo.fact_source_documents(record_type, rec.id) == [doc.id]
    await getattr(repo, "update_" + record_type.value)(rec.id, edit)
    assert await repo.fact_source_documents(record_type, rec.id) == []
    [old] = await repo.list_fact_sources(record_type, rec.id)
    assert old.state == "stale"
    await repo.insert_fact_source(record_type, rec.id, doc.id)
    assert await repo.fact_source_documents(record_type, rec.id) == [doc.id]
    history = await repo.list_fact_sources(record_type, rec.id)
    assert len(history) == 2
    assert history[0].assertion != history[1].assertion


async def test_documentary_reconfirmation_cannot_override_numeric_contradiction(container, alice):
    repo, d = container.repos.for_user(alice), container.data
    doc = await document(repo)
    r = await d.create_claim(alice, type=ClaimType.EXPERIENCE, text="Led two teams", text_lang="en")
    r = await d.add_evidence(
        alice,
        r.claim.id,
        kind=EvidenceKind.REFERENCE_LETTER,
        document=doc.id,
        excerpt="Led two teams",
    )
    old = next(e for e in r.evidence if e.kind is EvidenceKind.REFERENCE_LETTER)
    await d.update_claim(alice, r.claim.id, {"text": "Led twenty teams"})
    with pytest.raises(InputError):
        await d.reconfirm_evidence(alice, old.id)
    with pytest.raises(InputError):
        await d.add_evidence(
            alice,
            r.claim.id,
            kind=EvidenceKind.REFERENCE_LETTER,
            document=doc.id,
            excerpt="Led two teams",
        )
    assert (await d.get_claim(alice, r.claim.id)).claim.provability is Provability.MEDIUM


async def test_restoring_text_does_not_reactivate_stale_evidence(container, alice):
    d, repo = container.data, container.repos.for_user(alice)
    r = await d.create_claim(alice, type=ClaimType.SKILL, text="Go", text_lang="en")
    r = await d.add_evidence(
        alice, r.claim.id, kind=EvidenceKind.REFERENCE_PERSON, reference_name="Carol"
    )
    original = next(e for e in r.evidence if e.kind is EvidenceKind.REFERENCE_PERSON)
    await d.update_claim(alice, r.claim.id, {"text": "Rust"})
    restored = await d.update_claim(alice, r.claim.id, {"text": "Go"})
    assert restored.provability is Provability.MEDIUM
    assert (await repo.get_evidence(original.id)).state == "stale"
    assert (await d.reconfirm_evidence(alice, original.id)).claim.provability is Provability.HIGH


@pytest.mark.parametrize(
    "field,value", [("type", ClaimType.EXPERIENCE), ("text_lang", "de"), ("position_id", "new")]
)
async def test_material_claim_fields_stale_support(container, alice, field, value):
    d, repo = container.data, container.repos.for_user(alice)
    r = await d.create_claim(alice, type=ClaimType.SKILL, text="Go", text_lang="en")
    if field == "position_id":
        value = (
            await d.create_position(
                alice, dict(employer="Acme", title="Dev", start_month="2020-01", end_month=None)
            )
        ).id
    edited = await d.update_claim(alice, r.claim.id, {field: value})
    assert edited.provability is Provability.MEDIUM
    assert (await repo.get_evidence(r.evidence[0].id)).state == "stale"


async def test_repository_edit_has_no_implicit_declaration_and_cannot_confirm(container, alice):
    repo = container.repos.for_user(alice)
    r = await container.data.create_claim(alice, type=ClaimType.SKILL, text="Rust", text_lang="en")
    edited = await repo.update_claim(r.claim.id, {"text": "Go"})
    assert edited.provability is Provability.NONE
    assert edited.status is ClaimStatus.PROPOSED
    with pytest.raises(InputError):
        await repo.update_claim(
            r.claim.id, {"status": ClaimStatus.CONFIRMED, "provability": Provability.HIGH}
        )


async def test_fact_sources_validate_owned_records_documents_and_quotes(container, alice, bob):
    a, b = container.repos.for_user(alice), container.repos.for_user(bob)
    doc = await document(a)
    foreign = await b.insert_language(dict(language_code="en", level="B2"))
    own = await a.insert_language(dict(language_code="de", level="C1"))
    with pytest.raises(NotFoundError):
        await a.insert_fact_source(RecordType.LANGUAGE, foreign.id, doc.id, excerpt="Led two teams")
    with pytest.raises(InputError):
        await a.insert_fact_source(RecordType.LANGUAGE, own.id, doc.id)
    with pytest.raises(InputError):
        await a.insert_fact_source(RecordType.LANGUAGE, own.id, doc.id, excerpt="invented")
    first = await a.insert_fact_source(RecordType.LANGUAGE, own.id, doc.id, excerpt="Led two teams")
    duplicate = await a.insert_fact_source(
        RecordType.LANGUAGE, own.id, doc.id, excerpt="Led two teams"
    )
    assert first.id == duplicate.id
    assert (await a.get_fact_source(first.id)).assertion.level == "C1"
    await a.update_language(own.id, {"level": "C1"})
    assert await a.fact_source_documents(RecordType.LANGUAGE, own.id) == [doc.id]
    await container.data.delete_language(alice, own.id)
    assert await a.list_fact_sources(RecordType.LANGUAGE, own.id) == []


async def test_failed_attachment_rolls_back_edit_and_invalidation(container, alice):
    repo = container.repos.for_user(alice)
    rec = await repo.insert_language(dict(language_code="en", level="B2"))
    doc = await document(repo, DocumentKind.CV)
    original = await repo.insert_fact_source(RecordType.LANGUAGE, rec.id, doc.id)
    with pytest.raises(InputError):
        async with repo.transaction():
            await repo.update_language(rec.id, {"level": "C2"})
            await repo.insert_fact_source(RecordType.LANGUAGE, rec.id, doc.id, excerpt="invented")
    assert (await repo.get_language(rec.id)).level == "B2"
    assert (await repo.get_fact_source(original.id)).state == "active"
    assert await repo.fact_source_documents(RecordType.LANGUAGE, rec.id) == [doc.id]


async def test_material_edit_respects_explicit_proposed_status(container, alice):
    d = container.data
    r = await d.create_claim(alice, type=ClaimType.SKILL, text="Rust", text_lang="en")
    edited = await d.update_claim(alice, r.claim.id, {"text": "Go", "status": ClaimStatus.PROPOSED})
    assert edited.status is ClaimStatus.PROPOSED


async def test_reconfirm_can_supply_correct_current_excerpt(container, alice):
    d, repo = container.data, container.repos.for_user(alice)
    doc = await document(repo, text="Led two teams. Later led twenty teams.")
    r = await d.create_claim(alice, type=ClaimType.EXPERIENCE, text="Led two teams", text_lang="en")
    r = await d.add_evidence(
        alice,
        r.claim.id,
        kind=EvidenceKind.REFERENCE_LETTER,
        document=doc.id,
        excerpt="Led two teams",
    )
    old = next(e for e in r.evidence if e.kind is EvidenceKind.REFERENCE_LETTER)
    await d.update_claim(alice, r.claim.id, {"text": "Led twenty teams"})
    r = await d.reconfirm_evidence(alice, old.id, excerpt="Later led twenty teams.")
    assert r.claim.provability is Provability.HIGH
    assert (await repo.get_evidence(old.id)).excerpt == "Led two teams"
    assert (await repo.get_evidence(old.id)).state == "stale"


@pytest.mark.parametrize("state", ["active", "stale"])
async def test_malformed_history_is_diagnostic_and_never_support(container, alice, bob, state):
    from sqlalchemy import update

    from cvopt.adapters.db import schema as s
    from cvopt.services.data import recompute_provability

    repo, d = container.repos.for_user(alice), container.data
    r = await d.create_claim(alice, type=ClaimType.SKILL, text="Rust", text_lang="en")
    bad = r.evidence[0]
    async with container.engine.begin() as conn:
        await conn.execute(
            update(s.evidence)
            .where(s.evidence.c.id == bad.id)
            .values(kind="not_a_kind", state=state, excerpt="sensitive source text")
        )
    assert await repo.list_evidence([r.claim.id], active_only=True) == []
    assert await repo.list_evidence([r.claim.id]) == []
    [diagnostic] = await repo.list_invalid_evidence([r.claim.id])
    assert diagnostic.id == bad.id
    assert diagnostic.state == "invalid"
    assert diagnostic.raw["kind"] == "not_a_kind"
    assert "kind" in diagnostic.validation_error
    assert "sensitive source text" not in diagnostic.validation_error
    assert "not_a_kind" not in diagnostic.validation_error
    other = container.repos.for_user(bob)
    assert await other.list_invalid_evidence() == []
    with pytest.raises(NotFoundError):
        await other.evidence_claim_id(bad.id)
    with pytest.raises(NotFoundError):
        await d.remove_evidence(bob, bad.id)
    with pytest.raises(InputError, match="invalid"):
        await repo.get_evidence(bad.id)
    with pytest.raises(InputError):
        await d.reconfirm_evidence(alice, bad.id)
    [claim] = await recompute_provability(repo, [r.claim.id])
    assert claim.provability is Provability.NONE
    assert claim.status is ClaimStatus.PROPOSED
    with pytest.raises(InputError):
        await d.confirm_claim(alice, claim.id)
    assert (await d.get_claim(alice, claim.id)).invalid_evidence[0].id == bad.id


async def test_malformed_fact_snapshot_preserves_raw_export_and_diagnostics(container, alice):
    from sqlalchemy import update

    from cvopt.adapters.db import schema as s

    repo = container.repos.for_user(alice)
    rec = await repo.insert_language(dict(language_code="en", level="B2"))
    doc = await document(repo, DocumentKind.CV)
    source = await repo.insert_fact_source(RecordType.LANGUAGE, rec.id, doc.id)
    async with container.engine.begin() as conn:
        await conn.execute(
            update(s.fact_source)
            .where(s.fact_source.c.id == source.id)
            .values(assertion="{malformed source text", state="active")
        )
    assert await repo.fact_source_documents(RecordType.LANGUAGE, rec.id) == []
    assert await repo.list_fact_sources() == []
    [diagnostic] = await repo.list_invalid_fact_sources(RecordType.LANGUAGE, rec.id)
    assert diagnostic.raw["assertion"] == "{malformed source text"
    assert "source text" not in diagnostic.validation_error
    with pytest.raises(InputError, match="invalid"):
        await repo.get_fact_source(source.id)
    exported = await repo.export_rows()
    assert exported["fact_source"][0]["assertion"] == "{malformed source text"


async def test_remove_malformed_evidence_recomputes_from_surviving_valid_support(container, alice):
    from sqlalchemy import update

    from cvopt.adapters.db import schema as s

    d, repo = container.data, container.repos.for_user(alice)
    r = await d.create_claim(alice, type=ClaimType.SKILL, text="Rust", text_lang="en")
    bad = r.evidence[0]
    await d.add_evidence(
        alice, r.claim.id, kind=EvidenceKind.REFERENCE_PERSON, reference_name="Carol"
    )
    async with container.engine.begin() as conn:
        await conn.execute(
            update(s.evidence).where(s.evidence.c.id == bad.id).values(kind="unknown_kind")
        )
    result = await d.remove_evidence(alice, bad.id)
    assert result.claim.provability is Provability.HIGH
    assert [e.kind for e in result.evidence] == [EvidenceKind.REFERENCE_PERSON]
    assert await repo.list_invalid_evidence([r.claim.id]) == []
