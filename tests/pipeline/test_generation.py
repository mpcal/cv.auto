"""Generation and report (F4, F6) offline, from recordings: every generation and report
criterion of P1 §10 on the golden scenarios."""

import json

import pytest

from cvopt.bootstrap import Container
from cvopt.context import UserContext
from cvopt.domain.enums import ClaimStatus, DocumentKind
from cvopt.errors import InputError
from tests.scenarios import CV, JD, check_generation, generate_golden


async def test_golden_generation_meets_p1_criteria(golden: Container, alice: UserContext) -> None:
    runs = await generate_golden(golden, alice)
    for run_dir in runs.values():
        await check_generation(golden, alice, run_dir)

    de = json.loads((runs["de"] / "cv.json").read_text())
    assert de["languages"][0]["language"] in {"Englisch", "Deutsch", "Französisch"}
    # The doctorate the JD requires stays an uncovered requirement, listed as a gap.
    phd = json.loads((runs["phd"] / "report.json").read_text())
    reqs = json.loads((runs["phd"] / "requirements.json").read_text())["requirements"]
    doctorate = next(r["id"] for r in reqs if r["degree_level"] == "doctorate")
    assert any(r["kind"] == "gap" and r["requirements"] == [doctorate] for r in phd["rows"])
    info = json.loads((runs["en"] / "run.json").read_text())
    assert {"parse_jd", "generate_variants", "write_summary"} <= set(info["prompt_versions"])


async def test_unconfirmed_claims_are_not_used(golden: Container, alice: UserContext) -> None:
    res = await golden.ingestion.ingest_document(alice, CV, DocumentKind.CV)
    assert res.batch_id  # proposals exist, but nothing is applied yet
    assert await golden.data.list_claims(alice, status=ClaimStatus.CONFIRMED) == []
    run = await golden.jd.create_run(alice, text=JD / "jd_en_nordwind.txt")
    with pytest.raises(InputError, match="No confirmed claims"):
        await golden.generation.generate(alice, run.name)
