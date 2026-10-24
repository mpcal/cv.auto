"""Live: the golden CV and reference letter against the real model (records with CVOPT_RECORD=1)."""

import pytest

from cvopt.bootstrap import Container
from cvopt.context import UserContext
from tests.scenarios import check_golden, ingest_golden

pytestmark = pytest.mark.live


async def test_golden_ingestion(golden_live: Container, alice: UserContext) -> None:
    check_golden(golden_live, await ingest_golden(golden_live, alice))
