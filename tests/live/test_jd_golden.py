"""Live: the golden JDs against the real model (records with CVOPT_RECORD=1)."""

import pytest

from cvopt.bootstrap import Container
from cvopt.context import UserContext
from cvopt.errors import InputError
from tests.scenarios import JD, check_golden_runs, golden_runs

pytestmark = pytest.mark.live


async def test_golden_jds(golden_live: Container, alice: UserContext) -> None:
    check_golden_runs(await golden_runs(golden_live, alice))
    with pytest.raises(InputError):
        await golden_live.jd.create_run(alice, text=JD / "not_a_jd.txt")
