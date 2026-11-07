"""Explicit fixed-record support maintenance, separate from claim evidence."""

import pytest

from cvopt.domain.enums import RecordType
from cvopt.errors import InputError, NotFoundError
from tests.pipeline.test_evidence_assertions import document


@pytest.mark.parametrize("correction", ["Engineer", "Engineer II"])
async def test_fixed_source_reconfirm_preserves_stale_history(container, alice, bob, correction):
    repo = container.repos.for_user(alice)
    position = await repo.insert_position(
        dict(employer="Example", title="Engineer", start_month="2020-01", end_month=None)
    )
    doc = await document(repo, text="Engineer and Engineer II at Example since 2020-01")
    old = await repo.insert_fact_source(
        RecordType.POSITION, position.id, doc.id, excerpt="Engineer"
    )
    await repo.update_position(position.id, {"title": "Unrelated"})
    await repo.update_position(position.id, {"title": correction})
    assert not await repo.list_fact_sources(active_only=True)
    method = getattr(container.data, "reconfirm_fact_source", None)
    assert method is not None, "Fixed-source reconfirm API missing"
    with pytest.raises(NotFoundError):
        await method(bob, old.id)
    fresh = await method(alice, old.id, excerpt=correction)
    assert fresh.id != old.id and fresh.state == "active"
    assert (await repo.get_fact_source(old.id)).state == "stale"
    assert [s.id for s in await repo.list_fact_sources(active_only=True)] == [fresh.id]
    await repo.update_position(position.id, {"start_month": "2030-01"})
    with pytest.raises(InputError):
        await method(alice, old.id, excerpt="fabricated")
    assert not await repo.list_fact_sources(active_only=True)


async def test_explicit_fact_source_cli_flag_keeps_claim_default(container, alice, settings):
    from cvopt.domain.enums import ClaimType
    from tests.pipeline.test_cli import acli

    repo = container.repos.for_user(alice)
    p = await repo.insert_position(
        dict(employer="Example", title="Engineer", start_month="2020-01", end_month=None)
    )
    doc = await document(repo, text="Engineer at Example")
    old = await repo.insert_fact_source(RecordType.POSITION, p.id, doc.id, excerpt="Engineer")
    await repo.update_position(p.id, {"title": "Other"})
    await repo.update_position(p.id, {"title": "Engineer"})
    out = await acli(settings, "evidence", "reconfirm", old.id, "--fact-source")
    assert "Reconfirmed fact source" in out
    assert (await repo.get_fact_source(old.id)).state == "stale"
    assert len(await repo.list_fact_sources(active_only=True)) == 1
    claim = await container.data.create_claim(
        alice, type=ClaimType.SKILL, text="Python", text_lang="en"
    )
    assert "Reconfirmed evidence" in await acli(
        settings, "evidence", "reconfirm", claim.evidence[0].id
    )
