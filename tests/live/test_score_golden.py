"""Live: score the hand-written CV against the Nordwind JD (records with CVOPT_RECORD=1)."""

import pytest

from cvopt.bootstrap import Container
from cvopt.context import UserContext
from tests.scenarios import check_handwritten, score_handwritten

pytestmark = pytest.mark.live


async def test_score_handwritten(golden_live: Container, alice: UserContext) -> None:
    first, second, _ = await score_handwritten(golden_live, alice)
    check_handwritten(first, second)
