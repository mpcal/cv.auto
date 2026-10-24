"""Live: generation for the golden JDs (records with CVOPT_RECORD=1)."""

import pytest

from cvopt.bootstrap import Container
from cvopt.context import UserContext
from tests.scenarios import check_generation, generate_golden

pytestmark = pytest.mark.live


async def test_generation(golden_live: Container, alice: UserContext) -> None:
    runs = await generate_golden(golden_live, alice)
    for run_dir in runs.values():
        await check_generation(golden_live, alice, run_dir)
