"""LinkedIn, GitHub and awards survive extraction, storage, generation and the PDF."""

import sqlite3
from datetime import date
from pathlib import Path

from alembic import command

from cvopt.adapters.db.engine import alembic_config, migrate
from cvopt.adapters.typst_renderer import TypstRenderer
from cvopt.bootstrap import Container
from cvopt.config import Settings
from cvopt.context import UserContext
from cvopt.domain.cv import JSONResume
from cvopt.domain.enums import DocumentKind
from cvopt.domain.jd import JobDescription
from cvopt.services.generation import _Pipeline
from cvopt.services.render import build_render_model
from tests.fakes import FakeLLMClient
from tests.pipeline.test_education_details import extraction
from tests.pipeline.test_render import pdf_text

AWARD = {
    "title": "Koenderink Prize",
    "awarder": "ECCV",
    "awarded_month": "2020-08",
    "summary": "Test of time award for fundamental contributions",
}


def with_links_and_awards() -> dict:
    out = extraction()
    out["education"] = []
    out["profile"]["name"] = "Alice Example"
    out["profile"]["profiles"] = [
        {"network": "LinkedIn", "url": "linkedin.com/in/alice-example"},
        {"network": "GitHub", "url": "https://github.com/alice"},
    ]
    out["awards"] = [{**AWARD, "excerpt": "Koenderink Prize"}]
    return out


async def ingest(container: Container, alice: UserContext, path: Path) -> None:
    result = await container.ingestion.ingest_document(alice, path, DocumentKind.CV)
    assert result.batch_id
    await container.ingestion.accept_all(alice, result.batch_id)
    await container.ingestion.apply_batch(alice, result.batch_id)


async def test_links_and_awards_reach_the_pdf(
    container: Container, alice: UserContext, fake_llm: FakeLLMClient, tmp_path: Path
) -> None:
    source = tmp_path / "cv.txt"
    source.write_text("Awards\nKoenderink Prize\nlinkedin.com/in/alice-example")
    fake_llm.script("end_turn", with_links_and_awards())
    await ingest(container, alice, source)
    profile = await container.data.get_profile(alice)
    assert profile.linkedin == "linkedin.com/in/alice-example"
    assert profile.github == "https://github.com/alice"
    [award] = await container.data.list_awards(alice)
    assert award.model_dump(exclude={"id"}) == AWARD

    # The same award in another document links to the stored record.
    again = tmp_path / "cv-v2.txt"
    again.write_text("Awards (v2)\nKoenderink Prize")
    fake_llm.script("end_turn", with_links_and_awards())
    await ingest(container, alice, again)
    assert len(await container.data.list_awards(alice)) == 1

    pipeline = _Pipeline(
        container.generation,
        alice,
        JobDescription(
            title="Engineer", company="Example", language="en", requirements=[], logistics=[]
        ),
        date(2026, 11, 4),
    )
    facts = await pipeline.load()
    cv, _ = await pipeline.assemble(facts, [], {})
    pipeline.check_schema(cv)
    assert cv["basics"]["profiles"] == [
        {
            "network": "LinkedIn",
            "username": "alice-example",
            "url": "https://linkedin.com/in/alice-example",
        },
        {"network": "GitHub", "username": "alice", "url": "https://github.com/alice"},
    ]
    assert cv["awards"] == [
        {
            "title": AWARD["title"],
            "date": AWARD["awarded_month"],
            "awarder": AWARD["awarder"],
            "summary": AWARD["summary"],
        }
    ]
    model = build_render_model(JSONResume.model_validate(cv), Settings())
    text = pdf_text(TypstRenderer().render(model, tmp_path / "cv.pdf").path)
    for expected in [
        "AWARDS",
        "Koenderink Prize · ECCV · Aug 2020",
        "alice-example",
        "Test of time",
    ]:
        assert expected in text


async def test_awards_reserve_page_budget(container: Container, alice: UserContext) -> None:
    pipeline = _Pipeline(
        container.generation,
        alice,
        JobDescription(
            title="Engineer", company="Example", language="en", requirements=[], logistics=[]
        ),
        date(2026, 11, 4),
    )
    facts = await pipeline.load()
    baseline = pipeline.budget(facts)
    await container.data.create_award(alice, {**AWARD, "awarded_month": None})
    facts = await pipeline.load()
    assert pipeline.budget(facts) < baseline - 3


def test_migration_adds_empty_links_and_award_table(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    command.upgrade(alembic_config(f"sqlite:///{path}"), "0002")
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users VALUES (?, ?, ?)", ("alice@example.com", "abc", "2026-01-01"))
        db.execute(
            "INSERT INTO profile (user_id, created_at, updated_at, name, email, phone, city, "
            "country, website, profiles) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("alice@example.com", "2026-01-01", "2026-01-01", "Alice", "", "", "", "", "", "[]"),
        )
    migrate(f"sqlite+aiosqlite:///{path}")
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT name, linkedin, github FROM profile").fetchone() == (
            "Alice",
            "",
            "",
        )
        assert db.execute("SELECT count(*) FROM award").fetchone() == (0,)
