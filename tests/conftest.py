import os
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest

from cvopt.adapters.clock import FixedClock
from cvopt.adapters.db.repository import UserRepository
from cvopt.adapters.fetcher import HttpFetcher
from cvopt.bootstrap import Container, build_container
from cvopt.config import Settings, load_settings
from cvopt.context import UserContext
from tests import recordings
from tests.fakes import FakeLLMClient


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep tests away from the real config file, data root and environment."""
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("CVOPT_CONFIG", raising=False)
    monkeypatch.delenv("CVOPT_USER", raising=False)


TEST_NOW = datetime(2026, 6, 30, 12, 0, tzinfo=UTC)


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return load_settings(data_root=tmp_path / "data")


JOBS_HOST = "https://jobs.fjordlight.example"
GOLDEN_JD = Path(__file__).parent / "golden" / "jd"


def _serve(request: httpx.Request) -> httpx.Response:
    """A mock web host for JD pages."""
    path = request.url.path
    if request.url.host == "jobs.fjordlight.example":
        if path == "/ml-engineer":
            html = (GOLDEN_JD / "jd_html_injection.html").read_bytes()
            return httpx.Response(
                200, content=html, headers={"content-type": "text/html; charset=utf-8"}
            )
        if path == "/old-link":
            return httpx.Response(301, headers={"location": "/ml-engineer"})
        if path == "/login-wall":
            return httpx.Response(
                200,
                content=b"<html><body>Please log in.</body></html>",
                headers={"content-type": "text/html"},
            )
        if path == "/image":
            return httpx.Response(200, content=b"GIF89a", headers={"content-type": "image/gif"})
    return httpx.Response(404, content=b"not found")


async def fake_resolver(host: str) -> list[str]:
    return ["10.0.0.5"] if host.startswith("intranet.") else ["93.184.216.34"]


@pytest.fixture
def fetcher() -> HttpFetcher:
    return HttpFetcher(
        load_settings(data_root=Path("/unused")).fetch,
        resolver=fake_resolver,
        transport=httpx.MockTransport(_serve),
    )


@pytest.fixture
def fake_llm() -> FakeLLMClient:
    return FakeLLMClient()


@pytest.fixture
async def container(
    settings: Settings, fake_llm: FakeLLMClient, fetcher: HttpFetcher
) -> AsyncIterator[Container]:
    """Real adapters, except a FakeLLMClient, a FixedClock and a mock web host."""
    c = await build_container(settings, clock=FixedClock(TEST_NOW), llm=fake_llm, fetcher=fetcher)
    yield c
    await c.engine.dispose()


@pytest.fixture
async def live_container(settings: Settings, fetcher: HttpFetcher) -> AsyncIterator[Container]:
    """Real adapters including the Anthropic client (web pages still come from the mock
    host); live tests only."""
    if not os.environ.get("ANTHROPIC_API_KEY"):
        pytest.skip("ANTHROPIC_API_KEY is not set")
    c = await build_container(settings, clock=FixedClock(TEST_NOW), fetcher=fetcher)
    yield c
    await c.engine.dispose()


def _repo(c: Container, user: UserContext) -> UserRepository:
    repo = c.repos.for_user(user)
    assert isinstance(repo, UserRepository)
    return repo


@pytest.fixture
async def golden(container: Container, alice: UserContext) -> AsyncIterator[Container]:
    """The offline container with every recording loaded into Alice's LLM cache."""
    await recordings.load(_repo(container, alice))
    yield container


@pytest.fixture(scope="session", autouse=True)
def _fresh_recordings() -> None:
    """CVOPT_RECORD=fresh: drop all recordings first, so the live suite re-records every
    call from the real model (tests then build on what earlier tests recorded)."""
    if os.environ.get("CVOPT_RECORD") == "fresh":
        for path in recordings.RECORDINGS.glob("*.jsonl"):
            path.unlink()


@pytest.fixture
async def golden_live(live_container: Container, alice: UserContext) -> AsyncIterator[Container]:
    """The live container. Without recording, the cache starts empty so every call
    reaches the model. With CVOPT_RECORD=1, the existing recordings are loaded first, so
    only calls not yet recorded reach the model and are added: answers recorded earlier
    stay consistent with those recorded now."""
    if os.environ.get("CVOPT_RECORD") in ("1", "fresh"):
        repo = _repo(live_container, alice)
        known = await recordings.load(repo)
        yield live_container
        await recordings.write_new(repo, known)
    else:
        yield live_container


@pytest.fixture
def alice() -> UserContext:
    return UserContext.from_email("alice@example.com")


@pytest.fixture
def bob() -> UserContext:
    return UserContext.from_email("bob@example.com")
