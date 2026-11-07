"""Awards and certificates match complete identity, retaining uncertain imports for review."""

import pytest

from cvopt.domain.review import CreateAward, CreateCertificate
from cvopt.llm.tasks.common import ExAward, ExCertificate
from cvopt.services.ingestion import _Builder
from tests.pipeline.test_review_safety import batch_with


async def builder(container, alice):
    return _Builder(
        container.repos.for_user(alice), container.runners.for_user(alice), container.settings, "en"
    )


@pytest.mark.parametrize("kind", ["award", "certificate"])
@pytest.mark.parametrize("difference", ["issuer", "date"])
async def test_known_distinct_identity_creates_another_record(container, alice, kind, difference):
    repo = container.repos.for_user(alice)
    b = await builder(container, alice)
    if kind == "award":
        await repo.insert_award(
            dict(title="Excellence", awarder="Institute", awarded_month="2020-01")
        )
        await b.award(
            ExAward(
                summary="",
                title=" excellence ",
                awarder="Other" if difference == "issuer" else "Institute",
                awarded_month="2021-01" if difference == "date" else "2020-01",
            )
        )
        expected = CreateAward
    else:
        await repo.insert_certificate(
            dict(name="Expert", issuer="Institute", issued_month="2020-01")
        )
        await b.certificate(
            ExCertificate(
                url="",
                name=" expert ",
                issuer="Other" if difference == "issuer" else "Institute",
                issued_month="2021-01" if difference == "date" else "2020-01",
            )
        )
        expected = CreateCertificate
    assert len(b.out.items) == 1 and isinstance(b.out.items[0][0], expected)


@pytest.mark.parametrize("kind", ["award", "certificate"])
async def test_exact_duplicate_and_missing_fields_enrich_without_overwrite(container, alice, kind):
    repo = container.repos.for_user(alice)
    b = await builder(container, alice)
    if kind == "award":
        stored = await repo.insert_award(
            dict(
                title="Excellence",
                awarder="Institute",
                awarded_month=None,
                summary="Confirmed summary",
            )
        )
        await b.award(
            ExAward(
                title="excellence",
                awarder="institute",
                awarded_month="2020-01",
                summary="Different summary",
                excerpt="Led two teams",
            )
        )
    else:
        stored = await repo.insert_certificate(
            dict(
                name="Expert",
                issuer="Institute",
                issued_month=None,
                url="https://confirmed.example",
            )
        )
        await b.certificate(
            ExCertificate(
                name="expert",
                issuer="institute",
                issued_month="2020-01",
                url="https://different.example",
                excerpt="Led two teams",
            )
        )
    # Apply the builder's actual proposals, not hand-crafted expected payloads.
    repo, batch = await batch_with(container, alice, *(p for p, _ in b.out.items))
    await container.ingestion.accept_all(alice, batch.id)
    await container.ingestion.apply_batch(alice, batch.id)
    records = await (repo.list_awards() if kind == "award" else repo.list_certificates())
    assert len(records) == 1 and records[0].id == stored.id
    if kind == "award":
        assert records[0].awarded_month == "2020-01" and records[0].summary == "Confirmed summary"
    else:
        assert (
            records[0].issued_month == "2020-01" and records[0].url == "https://confirmed.example"
        )
    b = await builder(container, alice)
    if kind == "award":
        await b.award(
            ExAward(
                summary="",
                title="EXCELLENCE",
                awarder="Institute",
                awarded_month="2020-01",
                excerpt="Led two teams",
            )
        )
    else:
        await b.certificate(
            ExCertificate(
                url="",
                name="EXPERT",
                issuer="Institute",
                issued_month="2020-01",
                excerpt="Led two teams",
            )
        )
    assert [p.action.value for p, _ in b.out.items] == ["link_fact_source"]


@pytest.mark.parametrize("kind", ["award", "certificate"])
@pytest.mark.parametrize("ambiguous", [False, True])
async def test_missing_or_ambiguous_identity_is_a_visible_review_conflict(
    container, alice, kind, ambiguous
):
    repo = container.repos.for_user(alice)
    for month in ["2020-01", "2021-01"] if ambiguous else ["2020-01"]:
        if kind == "award":
            await repo.insert_award(
                dict(title="Excellence", awarder="Institute", awarded_month=month)
            )
        else:
            await repo.insert_certificate(
                dict(name="Expert", issuer="Institute", issued_month=month)
            )
    b = await builder(container, alice)
    if kind == "award":
        await b.award(
            ExAward(
                summary="",
                title="Excellence",
                awarder="Institute",
                awarded_month="",
                excerpt="Led two teams",
            )
        )
    else:
        await b.certificate(
            ExCertificate(
                url="", name="Expert", issuer="Institute", issued_month="", excerpt="Led two teams"
            )
        )
    [p] = [p for p, _ in b.out.items]
    assert p.action.value == "conflict" and p.record_type == kind
    assert "2020-01" in p.stored_value


@pytest.mark.parametrize("kind", ["award", "certificate"])
async def test_pending_duplicate_is_deduplicated_against_current_records(container, alice, kind):
    repo = container.repos.for_user(alice)
    if kind == "award":
        p = CreateAward(
            title="Excellence",
            awarder="Institute",
            awarded_month="2020-01",
            excerpt="Led two teams",
        )
    else:
        p = CreateCertificate(
            name="Expert", issuer="Institute", issued_month="2020-01", excerpt="Led two teams"
        )
    repo, batch = await batch_with(container, alice, p)
    if kind == "award":
        stored = await repo.insert_award(p.model_dump(exclude={"action", "excerpt"}))
    else:
        stored = await repo.insert_certificate(p.model_dump(exclude={"action", "excerpt"}))
    await container.ingestion.accept_all(alice, batch.id)
    await container.ingestion.apply_batch(alice, batch.id)
    records = await (repo.list_awards() if kind == "award" else repo.list_certificates())
    assert [r.id for r in records] == [stored.id]


@pytest.mark.parametrize("kind", ["award", "certificate"])
@pytest.mark.parametrize("stored_incomplete", [False, True])
async def test_mixed_named_creations_and_enrichments_preserve_distinct_identities(
    container, alice, kind, stored_incomplete
):
    repo = container.repos.for_user(alice)
    b = await builder(container, alice)
    stored = None
    if stored_incomplete:
        stored = await (
            repo.insert_award(dict(title="Expert"))
            if kind == "award"
            else repo.insert_certificate(dict(name="Expert"))
        )
    # Without a stored record the first incomplete extraction creates it; the
    # full A identity enriches that proposal, and the B identity must stay distinct.
    identities = [("A", "2020-01"), ("B", "2021-01")]
    if not stored_incomplete:
        identities.insert(0, ("", ""))
    for issuer, month in identities:
        if kind == "award":
            await b.award(
                ExAward(
                    title="Expert",
                    awarder=issuer,
                    awarded_month=month,
                    summary="",
                    excerpt="Led two teams",
                )
            )
        else:
            await b.certificate(
                ExCertificate(
                    name="Expert",
                    issuer=issuer,
                    issued_month=month,
                    url="",
                    excerpt="Led two teams",
                )
            )
    repo, batch = await batch_with(container, alice, *(p for p, _ in b.out.items))
    await container.ingestion.accept_all(alice, batch.id)
    await container.ingestion.apply_batch(alice, batch.id)
    records = await (repo.list_awards() if kind == "award" else repo.list_certificates())
    assert len(records) == 2
    if kind == "award":
        assert {(r.awarder, r.awarded_month) for r in records} == {
            ("A", "2020-01"),
            ("B", "2021-01"),
        }
    else:
        assert {(r.issuer, r.issued_month) for r in records} == {("A", "2020-01"), ("B", "2021-01")}
    if stored:
        assert stored.id in {r.id for r in records}
    assert len(await repo.list_fact_sources(active_only=True)) == 2
    assert len(await repo.list_fact_sources()) == (2 if stored_incomplete else 3)
