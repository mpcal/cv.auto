"""Scoring (F5) offline, from recordings."""

import json
from pathlib import Path

from typer.testing import CliRunner

from cvopt.bootstrap import Container
from cvopt.cli.main import app
from cvopt.context import UserContext
from cvopt.services.scoring import Scored
from tests.fakes import FakeLLMClient
from tests.scenarios import HANDWRITTEN_CV, check_handwritten, score_handwritten


async def test_score_handwritten_cv(
    golden: Container, alice: UserContext, fake_llm: FakeLLMClient
) -> None:
    first, second, run = await score_handwritten(golden, alice)
    check_handwritten(first, second)
    assert not fake_llm.calls  # everything came from the cache
    assert isinstance(first, Scored) and str(first.as_of) == "2026-06-30"


async def test_as_of_comes_from_run_json(
    golden: Container, alice: UserContext, tmp_path: Path
) -> None:
    _, _, run = await score_handwritten(golden, alice)
    cv_in_run = run.path / "cv.json"
    cv_in_run.write_text(HANDWRITTEN_CV.read_text())
    info = json.loads((run.path / "run.json").read_text())
    info["as_of"] = "2030-01-31"
    (run.path / "run.json").write_text(json.dumps(info))
    s = await golden.scoring.score(alice, cv_in_run, run.path / "requirements.json")
    assert str(s.as_of) == "2030-01-31"
    old = [e for e in s.elements if e.id.startswith("/work/2/")]
    assert old and all(e.placement == 0.85 for e in old)


def test_score_cli_rejects_bad_files(tmp_path: Path) -> None:
    bad = tmp_path / "cv.json"
    bad.write_text("{not json")
    result = CliRunner().invoke(
        app,
        [
            "--user",
            "a@example.com",
            "--data-root",
            str(tmp_path),
            "score",
            str(bad),
            "--requirements",
            str(bad),
        ],
    )
    assert result.exit_code == 1 and "Cannot read CV" in result.output
