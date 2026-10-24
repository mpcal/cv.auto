"""JD intake (F3) offline, from recordings."""

import json

import pytest

from cvopt.bootstrap import Container
from cvopt.context import UserContext
from cvopt.errors import InputError
from tests.scenarios import JD, check_golden_runs, golden_runs


def run_dirs(c: Container, user: UserContext) -> list[str]:
    runs = c.files.user_dir(user) / "runs"
    return sorted(p.name for p in runs.iterdir()) if runs.exists() else []


async def test_golden_jds(golden: Container, alice: UserContext) -> None:
    runs = await golden_runs(golden, alice)
    check_golden_runs(runs)
    info = json.loads((runs["html"].path / "run.json").read_text())
    assert info["jd_source"] == {
        "type": "url",
        "url": "https://jobs.fjordlight.example/ml-engineer",
        "final_url": "https://jobs.fjordlight.example/ml-engineer",
        "filename": "",
    }
    assert info["as_of"] == "2026-06-30" and info["language"] == "en"
    assert info["models"]["strong"] == "claude-sonnet-5-5"
    assert info["prompt_versions"] == {"parse_jd": "1"}
    assert info["config"]["optimizer"]["page_budget"] == 2
    assert runs["en"].name == "2026-06-30T1200-nordwind-retail-ag-senior-data-scientist-m-f-d"
    saved = json.loads((runs["de"].path / "requirements.json").read_text())
    assert saved["requirements"][0]["id"] == "R01"


@pytest.mark.parametrize(
    ("url", "why"),
    [
        ("https://jobs.fjordlight.example/gone", "answered 404"),
        ("https://jobs.fjordlight.example/login-wall", "too little text"),
        ("https://jobs.fjordlight.example/image", "not HTML or PDF"),
        ("https://intranet.fjordlight.example/job", "private or local address"),
        ("ftp://jobs.fjordlight.example/job", "only http and https"),
    ],
)
async def test_unreadable_url_creates_no_run(
    container: Container, alice: UserContext, url: str, why: str
) -> None:
    with pytest.raises(InputError, match=why) as err:
        await container.jd.create_run(alice, url=url)
    assert "--pdf or --text" in str(err.value)
    assert run_dirs(container, alice) == []


async def test_redirects_are_followed(golden: Container, alice: UserContext) -> None:
    run = await golden.jd.create_run(alice, url="https://jobs.fjordlight.example/old-link")
    info = json.loads((run.path / "run.json").read_text())
    assert info["jd_source"]["final_url"].endswith("/ml-engineer")


async def test_text_that_is_not_a_jd_creates_no_run(golden: Container, alice: UserContext) -> None:
    with pytest.raises(InputError, match="does not read as a job description"):
        await golden.jd.create_run(alice, text=JD / "not_a_jd.txt")
    assert run_dirs(golden, alice) == []


async def test_exactly_one_source(container: Container, alice: UserContext) -> None:
    with pytest.raises(InputError):
        await container.jd.create_run(alice)
