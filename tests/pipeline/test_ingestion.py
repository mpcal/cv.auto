"""Ingestion (F1) offline, from recordings."""

import pytest

from cvopt.bootstrap import Container
from cvopt.context import UserContext
from cvopt.domain.enums import Decision, DocumentKind, ProposalAction
from cvopt.errors import ConflictError, InputError
from tests.scenarios import CV, check_golden, ingest_golden


async def test_golden_cv_and_reference_letter(golden: Container, alice: UserContext) -> None:
    check_golden(golden, await ingest_golden(golden, alice))


async def test_same_file_twice_creates_nothing(golden: Container, alice: UserContext) -> None:
    await ingest_golden(golden, alice)
    before = (await golden.data.list_claims(alice), await golden.data.list_positions(alice))
    res = await golden.ingestion.ingest_document(alice, CV, DocumentKind.CV)
    assert res.duplicate_of and res.batch_id is None
    after = (await golden.data.list_claims(alice), await golden.data.list_positions(alice))
    assert before == after
    assert len(await golden.data.list_documents(alice)) == 2


async def test_rejecting_a_position_rejects_its_claims(
    golden: Container, alice: UserContext
) -> None:
    res = await golden.ingestion.ingest_document(alice, CV, DocumentKind.CV)
    assert res.batch_id
    items = await golden.ingestion.list_items(alice, res.batch_id)
    pos = next(i for i in items if i.action is ProposalAction.CREATE_POSITION)
    decided = await golden.ingestion.decide_item(alice, res.batch_id, pos.seq, Decision.REJECT)
    assert decided.rejected_dependents
    assert all(
        d.payload.position_id == f"$item:{pos.seq}"  # type: ignore[union-attr]
        for d in decided.rejected_dependents
    )
    with pytest.raises(ConflictError):  # items still pending
        await golden.ingestion.apply_batch(alice, res.batch_id)
    await golden.ingestion.accept_all(alice, res.batch_id)
    applied = await golden.ingestion.apply_batch(alice, res.batch_id)
    assert applied.rejected == 1 + len(decided.rejected_dependents)
    assert len(await golden.data.list_positions(alice)) == 2
    with pytest.raises(ConflictError):  # applied batches are closed
        await golden.ingestion.apply_batch(alice, res.batch_id)


async def test_edit_must_keep_action_and_refer_backwards(
    golden: Container, alice: UserContext
) -> None:
    res = await golden.ingestion.ingest_document(alice, CV, DocumentKind.CV)
    assert res.batch_id
    items = await golden.ingestion.list_items(alice, res.batch_id)
    claim = next(i for i in items if i.action is ProposalAction.CREATE_CLAIM)
    data = claim.payload.model_dump(mode="json")
    with pytest.raises(InputError):
        await golden.ingestion.decide_item(
            alice,
            res.batch_id,
            claim.seq,
            Decision.ACCEPT,
            {**data, "position_id": f"$item:{claim.seq + 1}"},
        )
    with pytest.raises(InputError):
        await golden.ingestion.decide_item(
            alice, res.batch_id, claim.seq, Decision.ACCEPT, {**data, "type": "hobby"}
        )
    res2 = await golden.ingestion.decide_item(
        alice, res.batch_id, claim.seq, Decision.ACCEPT, {**data, "text": "Edited text"}
    )
    assert res2.item.decision is Decision.ACCEPT
    assert res2.item.payload.text == "Edited text"  # type: ignore[union-attr]
