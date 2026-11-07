"""Resumed proposals cannot certify changed assertions or bypass dependencies."""

import pytest
from sqlalchemy import update

from cvopt.adapters.db import schema as s
from cvopt.domain.enums import (
    ClaimStatus,
    ClaimType,
    Decision,
    EvidenceKind,
    Provability,
    RecordType,
)
from cvopt.domain.review import (
    AddEvidence,
    CreateClaim,
    CreatePosition,
    EvidenceDraft,
    LinkFactSource,
    Note,
    SetProfileField,
)
from cvopt.errors import ConflictError, InputError
from tests.pipeline.test_evidence_assertions import document


async def batch_with(container, alice, *payloads):
    repo = container.repos.for_user(alice)
    doc = await document(repo)
    batch = await repo.insert_batch(doc.id)
    for seq, payload in enumerate(payloads, 1):
        await repo.insert_item(batch.id, seq, payload)
    return repo, batch


async def claim(container, alice):
    return (
        await container.data.create_claim(
            alice, type=ClaimType.EXPERIENCE, text="Led two teams", text_lang="en"
        )
    ).claim


@pytest.mark.parametrize("action", ["evidence", "fact", "profile"])
async def test_apply_preflight_persists_pending_conflicts_without_any_records(
    container, alice, action
):
    repo = container.repos.for_user(alice)
    if action == "evidence":
        current = await claim(container, alice)
        payload = AddEvidence(
            claim_id=current.id,
            evidence=EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER, excerpt="Led two teams"),
        )
    elif action == "fact":
        current = await repo.insert_position(
            dict(employer="Acme", title="Lead", start_month="2020-01", end_month=None)
        )
        payload = LinkFactSource(
            record_type=RecordType.POSITION, record_id=current.id, excerpt="Led two teams"
        )
    else:
        payload = SetProfileField(field="city", value="Zurich")
    repo, batch = await batch_with(
        container, alice, SetProfileField(field="name", value="Alice"), payload
    )
    await container.ingestion.accept_all(alice, batch.id)
    if action == "evidence":
        await repo.update_claim(current.id, {"text": "Managed two teams"})
    elif action == "fact":
        await repo.update_position(current.id, {"title": "Director"})
    else:
        await repo.save_profile((await repo.get_profile()).model_copy(update={"city": "Bern"}))
    with pytest.raises(ConflictError):
        await container.ingestion.apply_batch(alice, batch.id)
    assert (await repo.get_profile()).name == ""
    items = await repo.list_items(batch.id)
    assert items[1].decision is Decision.PENDING
    assert items[1].review_context.conflict is not None
    assert not await repo.list_fact_sources()
    assert not [e for e in await repo.list_evidence() if e.kind is EvidenceKind.REFERENCE_LETTER]
    # Human review, unlike bulk acceptance, explicitly binds to the current target.
    if action != "profile":
        with pytest.raises(ConflictError):
            await container.ingestion.accept_all(alice, batch.id)
        await container.ingestion.decide_item(alice, batch.id, 2, Decision.ACCEPT)
        await container.ingestion.apply_batch(alice, batch.id)
        if action == "evidence":
            refreshed = await repo.get_claim(current.id)
            assert refreshed.status is ClaimStatus.CONFIRMED
            assert refreshed.provability is Provability.HIGH
        else:
            assert len(await repo.list_fact_sources(active_only=True)) == 1


async def test_legacy_attachment_proposal_requires_review_without_inventing_old_identity(
    container, alice
):
    c = await claim(container, alice)
    repo, batch = await batch_with(
        container,
        alice,
        AddEvidence(
            claim_id=c.id,
            evidence=EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER, excerpt="Led two teams"),
        ),
    )
    async with container.engine.begin() as conn:
        # A row originating before snapshot support has no review context.
        await conn.execute(update(s.proposal_item).values(review_context=None))
    with pytest.raises(ConflictError):
        await container.ingestion.accept_all(alice, batch.id)
    [item] = await repo.list_items(batch.id)
    assert item.decision is Decision.PENDING
    assert item.review_context.conflict.prior_assertion is None
    await container.ingestion.decide_item(alice, batch.id, 1, Decision.ACCEPT)
    await container.ingestion.apply_batch(alice, batch.id)
    assert (await repo.get_claim(c.id)).provability is Provability.HIGH


async def test_dependencies_cannot_be_accepted_after_parent_rejected_or_skipped(container, alice):
    position = CreatePosition(employer="Acme", title="Lead", start_month="2020-01", end_month=None)
    child = CreateClaim(
        type=ClaimType.SKILL,
        text="Led two teams",
        text_lang="en",
        position_id="$item:1",
        keywords=[],
        evidence=EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER, excerpt="Led two teams"),
    )
    repo, batch = await batch_with(container, alice, position, child)
    with pytest.raises(ConflictError):
        await container.ingestion.decide_item(alice, batch.id, 2, Decision.ACCEPT)
    await container.ingestion.decide_item(alice, batch.id, 1, Decision.REJECT)
    with pytest.raises(ConflictError):
        await container.ingestion.decide_item(alice, batch.id, 2, Decision.ACCEPT)
    # Repointing the payload to an owned existing record repairs the dependency.
    stored = await repo.insert_position(
        dict(employer="Other", title="Lead", start_month="2020-01", end_month=None)
    )
    data = child.model_dump(mode="json")
    await container.ingestion.decide_item(
        alice, batch.id, 2, Decision.ACCEPT, {**data, "position_id": stored.id}
    )
    await container.ingestion.apply_batch(alice, batch.id)
    assert (await repo.list_claims())[0].position_id == stored.id


async def test_apply_refuses_corrupt_accepted_dependency_instead_of_silently_skipping(
    container, alice
):
    repo, batch = await batch_with(
        container,
        alice,
        Note(text="not a record"),
        CreateClaim(
            type=ClaimType.SKILL,
            text="Led two teams",
            text_lang="en",
            position_id="$item:1",
            keywords=[],
            evidence=EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER, excerpt="Led two teams"),
        ),
    )
    async with container.engine.begin() as conn:
        await conn.execute(update(s.proposal_item).values(decision="accept"))
    with pytest.raises(ConflictError):
        await container.ingestion.apply_batch(alice, batch.id)
    assert not await repo.list_claims()
    assert any(i.decision is Decision.PENDING for i in await repo.list_items(batch.id))


async def test_material_reference_proposal_edit_retains_extracted_history_without_high(
    container, alice
):
    original = CreateClaim(
        type=ClaimType.EXPERIENCE,
        text="Led two teams",
        text_lang="en",
        position_id=None,
        keywords=[],
        evidence=EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER, excerpt="Led two teams"),
    )
    repo, batch = await batch_with(container, alice, original)
    data = original.model_dump(mode="json")
    await container.ingestion.decide_item(
        alice, batch.id, 1, Decision.ACCEPT, {**data, "text": "Managed two teams"}
    )
    await container.ingestion.apply_batch(alice, batch.id)
    [c] = await repo.list_claims()
    assert c.text == "Managed two teams"
    assert c.provability is Provability.MEDIUM
    history = await repo.list_evidence()
    [old] = [e for e in history if e.kind is EvidenceKind.REFERENCE_LETTER]
    assert old.state == "stale" and old.assertion.text == "Led two teams"
    assert [e.kind for e in await repo.list_evidence(active_only=True)] == [
        EvidenceKind.SELF_DECLARED
    ]
    [item] = await repo.list_items(batch.id)
    assert item.review_context.original_payload.text == "Led two teams"
    # Supplying metadata through the edit API cannot rewrite the extracted assertion.


async def test_profile_identical_value_is_harmless(container, alice):
    repo, batch = await batch_with(container, alice, SetProfileField(field="city", value="Zurich"))
    await repo.save_profile((await repo.get_profile()).model_copy(update={"city": "Zurich"}))
    await container.ingestion.accept_all(alice, batch.id)
    await container.ingestion.apply_batch(alice, batch.id)
    assert (await repo.get_profile()).city == "Zurich"


async def test_material_fixed_proposal_edit_keeps_only_stale_document_support(container, alice):
    original = CreatePosition(
        employer="Acme",
        title="Lead",
        start_month="2020-01",
        end_month=None,
        excerpt="Led two teams",
    )
    repo, batch = await batch_with(container, alice, original)
    await container.ingestion.decide_item(
        alice,
        batch.id,
        1,
        Decision.ACCEPT,
        {**original.model_dump(mode="json"), "title": "Director"},
    )
    await container.ingestion.apply_batch(alice, batch.id)
    [pos] = await repo.list_positions()
    assert pos.title == "Director"
    assert not await repo.list_fact_sources(active_only=True)
    [source] = await repo.list_fact_sources()
    assert source.state == "stale" and source.assertion.title == "Lead"


async def test_snapshot_metadata_cannot_be_supplied_by_payload_editor(container, alice):
    c = await claim(container, alice)
    payload = AddEvidence(
        claim_id=c.id,
        evidence=EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER, excerpt="Led two teams"),
    )
    repo, batch = await batch_with(container, alice, payload)
    with pytest.raises(InputError):
        await container.ingestion.decide_item(
            alice,
            batch.id,
            1,
            Decision.ACCEPT,
            {**payload.model_dump(mode="json"), "review_context": {"original_payload": None}},
        )
    [item] = await repo.list_items(batch.id)
    assert item.review_context.original_payload == payload


async def test_projected_update_then_link_requires_review_and_renews_without_loop(container, alice):
    from cvopt.domain.review import UpdatePosition

    repo = container.repos.for_user(alice)
    pos = await repo.insert_position(
        dict(employer="Acme", title="Lead", start_month="2020-01", end_month=None)
    )
    repo, batch = await batch_with(
        container,
        alice,
        SetProfileField(field="name", value="Alice"),
        UpdatePosition(position_id=pos.id, employer_location="Zurich", excerpt="Led two teams"),
        LinkFactSource(record_type=RecordType.POSITION, record_id=pos.id, excerpt="Led two teams"),
    )
    with pytest.raises(ConflictError):
        await container.ingestion.accept_all(alice, batch.id)
    assert (await repo.get_profile()).name == ""
    assert (await repo.get_position(pos.id)).employer_location == ""
    [_, _, link] = await repo.list_items(batch.id)
    assert link.review_context.conflict.current_assertion["employer_location"] == "Zurich"
    # Accept the prior actions before explicitly renewing the projected link.
    await container.ingestion.decide_item(alice, batch.id, 1, Decision.ACCEPT)
    await container.ingestion.decide_item(alice, batch.id, 2, Decision.ACCEPT)
    await container.ingestion.decide_item(alice, batch.id, 3, Decision.ACCEPT)
    await container.ingestion.apply_batch(alice, batch.id)
    assert (await repo.get_position(pos.id)).employer_location == "Zurich"
    assert len(await repo.list_fact_sources(active_only=True)) == 1


async def test_parent_proposal_edit_does_not_recertify_dependent_evidence(container, alice):
    original = CreateClaim(
        type=ClaimType.EXPERIENCE,
        text="Led two teams",
        text_lang="en",
        position_id=None,
        keywords=[],
        evidence=EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER, excerpt="Led two teams"),
    )
    repo, batch = await batch_with(
        container,
        alice,
        original,
        AddEvidence(
            claim_id="$item:1",
            evidence=EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER, excerpt="Led two teams"),
        ),
    )
    await container.ingestion.decide_item(
        alice,
        batch.id,
        1,
        Decision.ACCEPT,
        {**original.model_dump(mode="json"), "text": "Managed two teams"},
    )
    with pytest.raises(ConflictError):
        await container.ingestion.accept_all(alice, batch.id)
    assert not await repo.list_claims()
    [_, dep] = await repo.list_items(batch.id)
    assert dep.review_context.conflict.prior_assertion["text"] == "Led two teams"
    assert dep.review_context.conflict.current_assertion["text"] == "Managed two teams"
    await container.ingestion.decide_item(alice, batch.id, 2, Decision.REJECT)
    await container.ingestion.apply_batch(alice, batch.id)
    assert (await repo.list_claims())[0].provability is Provability.MEDIUM


async def test_item_reference_literals_in_claim_text_are_not_dependencies(container, alice):
    original = CreateClaim(
        type=ClaimType.SKILL,
        text="$item:999",
        text_lang="en",
        position_id=None,
        keywords=[],
        evidence=EvidenceDraft(kind=EvidenceKind.SELF_DECLARED),
    )
    repo, batch = await batch_with(container, alice, original)
    await container.ingestion.accept_all(alice, batch.id)
    await container.ingestion.apply_batch(alice, batch.id)
    assert (await repo.list_claims())[0].text == "$item:999"


async def test_cli_rejection_refreshes_pending_list_before_default_accept(
    container, alice, settings
):
    from tests.pipeline.test_cli import acli

    pos = CreatePosition(employer="Acme", title="Lead", start_month="2020-01", end_month=None)
    child = CreateClaim(
        type=ClaimType.EXPERIENCE,
        text="Led two teams",
        text_lang="en",
        position_id="$item:1",
        keywords=[],
        evidence=EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER, excerpt="Led two teams"),
    )
    repo, batch = await batch_with(container, alice, pos, child, Note(text="Still pending"))
    out = await acli(settings, "review", batch.id, input="r\n\ny\n")
    assert "Applied 1 item(s)" in out
    assert not await repo.list_claims() and not await repo.list_positions()
    assert "Led two teams" not in out


async def test_cli_conflict_shows_human_assertions_and_quote_without_metadata(
    container, alice, settings
):
    from tests.pipeline.test_cli import acli

    c = await claim(container, alice)
    repo, batch = await batch_with(
        container,
        alice,
        AddEvidence(
            claim_id=c.id,
            evidence=EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER, excerpt="Led two teams"),
        ),
    )
    await repo.update_claim(c.id, {"text": "Managed two teams"})
    with pytest.raises(ConflictError):
        await container.ingestion.accept_all(alice, batch.id)
    [item] = await repo.list_items(batch.id)
    out = await acli(settings, "review", batch.id, input="q\n")
    assert "Prior assertion" in out and "Current assertion" in out
    assert "Led two teams" in out and "Managed two teams" in out
    assert item.review_context.target_fingerprint not in out
    assert "review_context" not in out and "original_payload" not in out


async def test_renewed_review_retains_original_target_identity(container, alice):
    c = await claim(container, alice)
    repo, batch = await batch_with(
        container,
        alice,
        AddEvidence(
            claim_id=c.id,
            evidence=EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER, excerpt="Led two teams"),
        ),
    )
    await repo.update_claim(c.id, {"text": "Managed two teams"})
    with pytest.raises(ConflictError):
        await container.ingestion.accept_all(alice, batch.id)
    await container.ingestion.decide_item(alice, batch.id, 1, Decision.ACCEPT)
    [item] = await repo.list_items(batch.id)
    assert item.review_context.original_target_assertion["text"] == "Led two teams"
    assert item.review_context.target_assertion["text"] == "Managed two teams"
    assert item.review_context.original_payload.claim_id == c.id


async def test_old_fill_update_cannot_certify_a_changed_fixed_record(container, alice):
    from cvopt.domain.review import UpdatePosition

    repo = container.repos.for_user(alice)
    pos = await repo.insert_position(
        dict(employer="Acme", title="Lead", start_month="2020-01", end_month=None)
    )
    repo, batch = await batch_with(
        container,
        alice,
        UpdatePosition(position_id=pos.id, employer_location="Zurich", excerpt="Led two teams"),
    )
    await repo.update_position(pos.id, {"title": "Director"})
    with pytest.raises(ConflictError):
        await container.ingestion.accept_all(alice, batch.id)
    assert (await repo.get_position(pos.id)).employer_location == ""
    assert not await repo.list_fact_sources()


async def test_first_individual_accept_of_stale_target_requires_a_second_review(container, alice):
    c = await claim(container, alice)
    repo, batch = await batch_with(
        container,
        alice,
        AddEvidence(
            claim_id=c.id,
            evidence=EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER, excerpt="Led two teams"),
        ),
    )
    await repo.update_claim(c.id, {"text": "Managed two teams"})
    with pytest.raises(ConflictError):
        await container.ingestion.decide_item(alice, batch.id, 1, Decision.ACCEPT)
    [item] = await repo.list_items(batch.id)
    assert item.decision is Decision.PENDING
    assert item.review_context.conflict.current_assertion["text"] == "Managed two teams"
    await container.ingestion.decide_item(alice, batch.id, 1, Decision.ACCEPT)
    await container.ingestion.apply_batch(alice, batch.id)
    assert (await repo.get_claim(c.id)).provability is Provability.HIGH


async def test_changed_again_target_refreshes_conflict_before_individual_accept(container, alice):
    c = await claim(container, alice)
    repo, batch = await batch_with(
        container,
        alice,
        AddEvidence(
            claim_id=c.id,
            evidence=EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER, excerpt="Led two teams"),
        ),
    )
    await repo.update_claim(c.id, {"text": "Managed two teams"})
    with pytest.raises(ConflictError):
        await container.ingestion.accept_all(alice, batch.id)
    await repo.update_claim(c.id, {"text": "Coordinated two teams"})
    with pytest.raises(ConflictError):
        await container.ingestion.decide_item(alice, batch.id, 1, Decision.ACCEPT)
    [item] = await repo.list_items(batch.id)
    assert item.review_context.conflict.current_assertion["text"] == "Coordinated two teams"
    await container.ingestion.decide_item(alice, batch.id, 1, Decision.ACCEPT)
    await container.ingestion.apply_batch(alice, batch.id)
    assert (await repo.get_claim(c.id)).provability is Provability.HIGH


async def test_terminal_editor_can_repoint_evidence_without_editing_context(
    container, alice, monkeypatch
):
    from cvopt.cli.review import edit_payload

    old = await claim(container, alice)
    new = await claim(container, alice)
    repo, batch = await batch_with(
        container,
        alice,
        AddEvidence(
            claim_id=old.id,
            evidence=EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER, excerpt="Led two teams"),
        ),
    )
    [item] = await repo.list_items(batch.id)
    names = []

    def edit(name, default, **kwargs):
        names.append(name)
        return new.id if name == "claim_id" else default

    monkeypatch.setattr("cvopt.cli.review.Prompt.ask", edit)
    payload = edit_payload(item)
    await container.ingestion.decide_item(alice, batch.id, 1, Decision.ACCEPT, payload)
    await container.ingestion.apply_batch(alice, batch.id)
    assert (await repo.get_claim(new.id)).provability is Provability.HIGH
    assert (await repo.get_claim(old.id)).provability is Provability.MEDIUM
    assert not any(
        "assertion" in name or "fingerprint" in name or "context" in name for name in names
    )
    [after] = await repo.list_items(batch.id)
    assert after.review_context.original_target_id == old.id


async def test_retargeted_legacy_decision_requires_review_even_for_identical_assertions(
    container, alice
):
    old = await claim(container, alice)
    new = await claim(container, alice)
    p = AddEvidence(
        claim_id=old.id,
        evidence=EvidenceDraft(kind=EvidenceKind.REFERENCE_LETTER, excerpt="Led two teams"),
    )
    repo, batch = await batch_with(container, alice, p)
    [item] = await repo.list_items(batch.id)
    # Simulate an earlier application changing the payload without refreshing target metadata.
    await repo.update_item(item.id, Decision.PENDING, p.model_copy(update={"claim_id": new.id}))
    with pytest.raises(ConflictError):
        await container.ingestion.decide_item(alice, batch.id, 1, Decision.ACCEPT)
    assert (await repo.list_items(batch.id))[0].decision is Decision.PENDING


@pytest.mark.parametrize("kind", ["position", "education", "certificate", "award"])
async def test_retargeted_fixed_update_history_uses_protected_original_record(
    container, alice, kind
):
    from cvopt.domain.review import UpdateAward, UpdateCertificate, UpdateEducation, UpdatePosition

    repo = container.repos.for_user(alice)
    if kind == "position":
        old = await repo.insert_position(
            dict(employer="Old Employer", title="Engineer", start_month="2020-01", end_month=None)
        )
        new = await repo.insert_position(
            dict(employer="New Employer", title="Director", start_month="2024-01", end_month=None)
        )
        original = UpdatePosition(
            position_id=old.id, employer_location="Zurich", excerpt="Led two teams"
        )
        edited = {**original.model_dump(mode="json"), "position_id": new.id}
        historical_values = {**old.model_dump(), "employer_location": "Zurich"}
        changed_field, declared_value = "employer_location", "Zurich"
    elif kind == "education":
        old = await repo.insert_education(
            dict(institution="Old School", degree_level="master", area="Physics")
        )
        new = await repo.insert_education(
            dict(institution="New School", degree_level="bachelor", area="Chemistry")
        )
        original = UpdateEducation(
            education_id=old.id, summary="Original summary", excerpt="Led two teams"
        )
        edited = {**original.model_dump(mode="json"), "education_id": new.id}
        historical_values = {**old.model_dump(), "summary": "Original summary"}
        changed_field, declared_value = "summary", "Original summary"
    elif kind == "certificate":
        old = await repo.insert_certificate(dict(name="Old Credential", issuer="Old Institute"))
        new = await repo.insert_certificate(dict(name="New Credential", issuer="New Institute"))
        original = UpdateCertificate(
            certificate_id=old.id, issued_month="2020-01", excerpt="Led two teams"
        )
        edited = {**original.model_dump(mode="json"), "certificate_id": new.id}
        historical_values = {**old.model_dump(), "issued_month": "2020-01"}
        changed_field, declared_value = "issued_month", "2020-01"
    else:
        old = await repo.insert_award(dict(title="Old Prize", awarder="Old Society"))
        new = await repo.insert_award(dict(title="New Prize", awarder="New Society"))
        original = UpdateAward(award_id=old.id, awarded_month="2020-01", excerpt="Led two teams")
        edited = {**original.model_dump(mode="json"), "award_id": new.id}
        historical_values = {**old.model_dump(), "awarded_month": "2020-01"}
        changed_field, declared_value = "awarded_month", "2020-01"
    repo, batch = await batch_with(container, alice, original)
    await container.ingestion.decide_item(alice, batch.id, 1, Decision.ACCEPT, edited)
    await container.ingestion.apply_batch(alice, batch.id)
    getter = getattr(repo, f"get_{kind}")
    assert getattr(await getter(new.id), changed_field) == declared_value
    assert await getter(old.id) == old
    [source] = await repo.list_fact_sources()
    assert source.state == "stale" and source.record_id == old.id
    assert source.assertion.model_dump() == historical_values
    assert source.document_id == batch.document_id and source.excerpt == "Led two teams"
    assert not await repo.list_fact_sources(record_id=new.id, active_only=True)
    [item] = await repo.list_items(batch.id)
    assert item.review_context.original_target_id == old.id
    assert item.review_context.original_target_assertion == old.model_dump(mode="json")


@pytest.mark.parametrize("missing_original", ["deleted", "rejected"])
async def test_retargeted_update_keeps_missing_original_history_only_in_context(
    container, alice, missing_original
):
    from cvopt.domain.review import UpdatePosition

    repo = container.repos.for_user(alice)
    new = await repo.insert_position(
        dict(employer="New Employer", title="Director", start_month="2024-01", end_month=None)
    )
    if missing_original == "deleted":
        old = await repo.insert_position(
            dict(employer="Old Employer", title="Engineer", start_month="2020-01", end_month=None)
        )
        original = UpdatePosition(
            position_id=old.id, employer_location="Zurich", excerpt="Led two teams"
        )
        repo, batch = await batch_with(container, alice, original)
        seq = 1
        await repo.delete_position(old.id)
    else:
        position = CreatePosition(
            employer="Old Employer",
            title="Engineer",
            start_month="2020-01",
            end_month=None,
            excerpt="Led two teams",
        )
        original = UpdatePosition(
            position_id="$item:1", employer_location="Zurich", excerpt="Led two teams"
        )
        repo, batch = await batch_with(container, alice, position, original)
        seq = 2
        await container.ingestion.decide_item(alice, batch.id, 1, Decision.REJECT)
    [item] = [i for i in await repo.list_items(batch.id) if i.seq == seq]
    original_context = item.review_context
    await container.ingestion.decide_item(
        alice,
        batch.id,
        seq,
        Decision.ACCEPT,
        {**original.model_dump(mode="json"), "position_id": new.id},
    )
    await container.ingestion.apply_batch(alice, batch.id)
    assert (await repo.get_position(new.id)).employer_location == "Zurich"
    assert not await repo.list_fact_sources()
    [after] = [i for i in await repo.list_items(batch.id) if i.seq == seq]
    assert after.review_context.original_payload == original_context.original_payload
    assert (
        after.review_context.original_target_assertion == original_context.original_target_assertion
    )
    assert after.review_context.original_target_assertion["employer"] == "Old Employer"
