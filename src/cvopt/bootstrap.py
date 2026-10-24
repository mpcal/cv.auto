"""Composition root: configuration, engine, adapters, services."""

from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncEngine

from cvopt.adapters.anthropic_client import AnthropicLLMClient
from cvopt.adapters.clock import SystemClock
from cvopt.adapters.db.engine import open_database
from cvopt.adapters.db.repository import RepositoryFactory
from cvopt.adapters.extractors import LocalTextExtractor
from cvopt.adapters.fetcher import HttpFetcher
from cvopt.adapters.files import LocalFileStore
from cvopt.adapters.language import LinguaDetector
from cvopt.adapters.typst_renderer import TypstRenderer
from cvopt.config import Settings
from cvopt.llm.runner import TaskRunnerFactory
from cvopt.ports import Clock, LLMClient, WebFetcher
from cvopt.services.account import AccountService
from cvopt.services.data import DataService
from cvopt.services.generation import GenerationService
from cvopt.services.ingestion import IngestionService
from cvopt.services.jd import JdService
from cvopt.services.render import RenderService
from cvopt.services.scoring import ScoringService


@dataclass(frozen=True)
class Container:
    settings: Settings
    engine: AsyncEngine
    files: LocalFileStore
    repos: RepositoryFactory
    runners: TaskRunnerFactory
    data: DataService
    ingestion: IngestionService
    jd: JdService
    scoring: ScoringService
    render: RenderService
    generation: GenerationService
    account: AccountService


async def build_container(
    settings: Settings,
    clock: Clock | None = None,
    llm: LLMClient | None = None,
    fetcher: WebFetcher | None = None,
) -> Container:
    engine = await open_database(settings.app.database_url)  # pragmas, then migrations
    clock = clock or SystemClock()
    repos = RepositoryFactory(engine, clock)
    files = LocalFileStore(settings.app.data_root)
    extractor = LocalTextExtractor(settings.documents)
    runners = TaskRunnerFactory(llm or AnthropicLLMClient(settings.llm), repos, settings)
    scoring = ScoringService(runners, clock, settings)
    render = RenderService(TypstRenderer(), settings)
    return Container(
        settings=settings,
        engine=engine,
        files=files,
        repos=repos,
        runners=runners,
        data=DataService(repos, files),
        ingestion=IngestionService(
            repos,
            runners,
            files,
            extractor,
            LinguaDetector(),
            clock,
            settings,
        ),
        scoring=scoring,
        render=render,
        account=AccountService(repos, files),
        generation=GenerationService(repos, runners, files, scoring, render, settings),
        jd=JdService(
            runners, files, fetcher or HttpFetcher(settings.fetch), extractor, clock, settings
        ),
    )
