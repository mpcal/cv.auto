"""Education facts survive extraction, storage, generation and PDF presentation."""

import json
import sqlite3
from datetime import date
from pathlib import Path

import docx
import pytest
from alembic import command
from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from pypdf import PdfReader, PdfWriter
from pypdf.annotations import Link

from cvopt.adapters.db.engine import alembic_config, migrate
from cvopt.adapters.extractors import LocalTextExtractor
from cvopt.adapters.typst_renderer import TypstRenderer
from cvopt.bootstrap import Container
from cvopt.cli.review import edit_payload
from cvopt.config import Settings, load_settings
from cvopt.context import UserContext
from cvopt.domain.cv import JSONResume
from cvopt.domain.enums import DocumentKind
from cvopt.domain.jd import JobDescription
from cvopt.domain.records import Education
from cvopt.domain.review import CreateEducation, ProposalItem, UpdateEducation
from cvopt.llm.task import cache_key, canonical_json
from cvopt.llm.tasks.extract_cv import EXTRACT_CV, CVInput, CVOutput
from cvopt.services.generation import _Pipeline
from cvopt.services.render import build_render_model
from tests.conftest import TEST_NOW
from tests.fakes import FakeLLMClient
from tests.pipeline.test_render import REPORT, pdf_text

DETAILS = {
    "institution": "Example Technical University",
    "area": "Computer Vision",
    "degree_type": "PhD",
    "degree_level": "doctorate",
    "start_month": "2006-03",
    "end_month": "2010-10",
    "grade": "With distinction",
    "summary": "Computer Vision Group, Prof. Ada Example",
    "highlights": [
        "Topic: Loop closure in SLAM for robotic visual navigation",
        "Author of the EXAMPLE algorithm, published in 2010",
        "Dissertation nominated for Thesis of the Year",
        "Publications with 8,700+ citations",
        "Winner of the Example Prize 2020",
        "Cited as prior art in 93 patent documents",
    ],
    "links": [{"label": "Research demo", "url": "https://example.org/demo"}],
}


def cv_json() -> dict:
    return {
        "basics": {"name": "Alice Example"},
        "education": [
            {
                "institution": DETAILS["institution"],
                "area": "Computer Vision",
                "studyType": "PhD",
                "startDate": "2006-03",
                "endDate": "2010-10",
                "score": "With distinction",
                "summary": DETAILS["summary"],
                "highlights": DETAILS["highlights"],
                "links": DETAILS["links"],
                "courses": ["Numerical Methods"],
            }
        ],
    }


@pytest.mark.parametrize("template", ["altacv-mod-1"])
@pytest.mark.parametrize("annotated", [False, True])
def test_full_education_is_the_default_in_clean_and_annotated_pdfs(
    tmp_path: Path, template: str, annotated: bool
) -> None:
    settings = Settings()
    settings.render.template = template
    cv = JSONResume.model_validate(cv_json())
    model = build_render_model(
        cv, settings, annotations=[] if annotated else None, report=REPORT if annotated else None
    )
    result = TypstRenderer().render(model, tmp_path / "full.pdf")
    text = pdf_text(result.path)
    for detail in [
        DETAILS["summary"],
        *DETAILS["highlights"],
        "With distinction",
        "Numerical Methods",
    ]:
        assert detail in text
    assert "Research demo" in text
    urls = [
        a.get_object().get("/A", {}).get("/URI")
        for p in PdfReader(result.path).pages
        for a in p.get("/Annots", [])
    ]
    assert "https://example.org/demo" in urls


@pytest.mark.parametrize("template", ["altacv-mod-1"])
def test_compact_mode_hides_only_education_details(tmp_path: Path, template: str) -> None:
    config = tmp_path / "config.toml"
    config.write_text(f'[render]\ntemplate = "{template}"\neducation_details = "compact"\n')
    settings = load_settings(config)
    model = build_render_model(JSONResume.model_validate(cv_json()), settings)
    text = pdf_text(TypstRenderer().render(model, tmp_path / "compact.pdf").path)
    assert "PhD Computer Vision" in text and "Mar 2006 – Oct 2010" in text
    assert "Computer Vision Group" not in text
    assert "With distinction" not in text and "Research demo" not in text


@pytest.mark.parametrize("template", ["altacv-mod-1"])
def test_education_entry_stays_together_at_page_boundary(tmp_path: Path, template: str) -> None:
    data = cv_json()
    data["work"] = [
        {
            "name": "Example",
            "position": "Engineer",
            "startDate": "2020-01",
            "highlights": [
                f"Work detail {i}: Developed and maintained data pipelines." for i in range(44)
            ],
        }
    ]
    settings = Settings()
    settings.render.template = template
    result = TypstRenderer().render(
        build_render_model(JSONResume.model_validate(data), settings), tmp_path / "boundary.pdf"
    )
    pages = [page.extract_text() or "" for page in PdfReader(result.path).pages]
    assert any("Loop closure" in text and "93 patent" in text for text in pages)


@pytest.mark.parametrize("template", ["altacv-mod-1"])
def test_education_longer_than_a_page_remains_readable(tmp_path: Path, template: str) -> None:
    data = cv_json()
    data["education"][0]["highlights"] = [f"Research detail {i:03d}" for i in range(150)]
    settings = Settings()
    settings.render.template = template
    result = TypstRenderer().render(
        build_render_model(JSONResume.model_validate(data), settings), tmp_path / "long.pdf"
    )
    assert result.pages >= 2
    text = pdf_text(result.path)
    for i in range(150):
        assert f"Research detail {i:03d}" in text


def extraction() -> dict:
    return {
        "profile": {
            "name": "",
            "email": "",
            "phone": "",
            "city": "",
            "country": "",
            "website": "",
            "profiles": [],
        },
        "positions": [],
        "education": [{**DETAILS, "excerpt": DETAILS["institution"]}],
        "languages": [],
        "certificates": [],
        "claims": [],
    }


async def test_education_survives_ingestion_storage_and_generation(
    container: Container, alice: UserContext, fake_llm: FakeLLMClient, tmp_path: Path
) -> None:
    source = tmp_path / "cv.txt"
    source.write_text("Education\n" + json.dumps(DETAILS))
    fake_llm.script("end_turn", extraction())
    result = await container.ingestion.ingest_document(alice, source, DocumentKind.CV)
    assert result.batch_id
    await container.ingestion.accept_all(alice, result.batch_id)
    await container.ingestion.apply_batch(alice, result.batch_id)
    [education] = await container.data.list_education(alice)
    assert education.summary == DETAILS["summary"]
    assert education.highlights == DETAILS["highlights"]
    assert education.links[0].url == "https://example.org/demo"
    pipeline = _Pipeline(
        container.generation,
        alice,
        JobDescription(
            title="Engineer", company="Example", language="en", requirements=[], logistics=[]
        ),
        date(2026, 11, 4),
    )
    facts = await pipeline.load()
    generated, _ = await pipeline.assemble(facts, [], {})
    entry = generated["education"][0]
    assert entry["summary"] == DETAILS["summary"]
    assert entry["highlights"] == DETAILS["highlights"]
    assert entry["links"] == DETAILS["links"]
    assert entry["score"] == "With distinction"
    pipeline.check_schema(generated)
    pipeline.check_facts(facts, generated)


async def test_long_education_reserves_budget_without_trimming(
    container: Container, alice: UserContext
) -> None:
    pipeline = _Pipeline(
        container.generation,
        alice,
        JobDescription(
            title="Engineer", company="Example", language="en", requirements=[], logistics=[]
        ),
        date(2026, 11, 4),
    )
    facts = await pipeline.load()
    plain = Education(id="edu", institution="University", degree_level="master")
    facts.education = [plain]
    baseline = pipeline.budget(facts)
    facts.education = [
        plain.model_copy(update={"summary": "Department", "highlights": ["Detail " * 80] * 12})
    ]
    assert pipeline.budget(facts) < baseline - 40
    assert len(pipeline.fact_sections(facts)["education"][0]["highlights"]) == 12
    pipeline.s.render.education_details = "compact"
    assert pipeline.budget(facts) == baseline


async def test_pdf_extraction_preserves_hyperlink_targets(tmp_path: Path) -> None:
    path = tmp_path / "linked.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=300, height=300)
    writer.add_annotation(0, Link(rect=(10, 10, 100, 25), url="https://example.org/research"))
    writer.write(path)
    extracted = await LocalTextExtractor(Settings().documents).extract(path)
    assert "https://example.org/research" in extracted.text


async def test_docx_extraction_preserves_hyperlink_targets(tmp_path: Path) -> None:
    document = docx.Document()
    paragraph = document.add_paragraph("Education: PhD in Computer Vision. ")
    rel = document.part.relate_to(
        "https://example.org/research", RELATIONSHIP_TYPE.HYPERLINK, is_external=True
    )
    hyperlink = OxmlElement("w:hyperlink")
    hyperlink.set(qn("r:id"), rel)
    run = OxmlElement("w:r")
    text = OxmlElement("w:t")
    text.text = "Research"
    run.append(text)
    hyperlink.append(run)
    paragraph._p.append(hyperlink)
    path = tmp_path / "linked.docx"
    document.save(path)
    extracted = await LocalTextExtractor(Settings().documents).extract(path)
    assert "Research" in extracted.text
    assert "https://example.org/research" in extracted.text


async def test_refresh_recovers_existing_education_without_duplicates_or_overwrites(
    container: Container, alice: UserContext, fake_llm: FakeLLMClient, tmp_path: Path
) -> None:
    source = tmp_path / "cv.txt"
    source.write_text("Education\n" + json.dumps(DETAILS))
    legacy = extraction()
    legacy["education"] = [
        {k: v for k, v in DETAILS.items() if k not in {"summary", "highlights", "links"}}
    ]
    stored = await container.data.create_education(
        alice, {**legacy["education"][0], "summary": "My confirmed group name"}
    )
    # Refresh uses the upgraded prompt, not the original, incomplete cache entry.
    repo = container.repos.for_user(alice)
    document = await repo.insert_document(
        {
            "kind": DocumentKind.CV,
            "filename": "cv.txt",
            "sha256": "a" * 64,
            "mime_type": "text/plain",
            "language": "en",
            "extracted_text": source.read_text(),
            "received_at": TEST_NOW,
        }
    )
    key = cache_key(
        EXTRACT_CV.name,
        "1",  # The original extraction cached before education details were supported.
        container.settings.llm.strong_model,
        canonical_json(CVInput(text=document.extracted_text, language=document.language)),
    )
    await repo.cache_put(
        key,
        EXTRACT_CV.name,
        "1",
        container.settings.llm.strong_model,
        CVOutput.model_validate(legacy).model_dump_json(),
    )
    fake_llm.script("end_turn", extraction())
    refreshed = await container.ingestion.refresh_education(alice, document.id)
    assert refreshed.batch_id
    [before] = await container.data.list_education(alice)
    assert before.highlights == []  # Changes still need review.
    await container.ingestion.accept_all(alice, refreshed.batch_id)
    await container.ingestion.apply_batch(alice, refreshed.batch_id)
    [after] = await container.data.list_education(alice)
    assert after.id == stored.id and after.summary == "My confirmed group name"
    assert after.highlights == DETAILS["highlights"]
    assert after.links[0].url == "https://example.org/demo"
    again = await container.ingestion.refresh_education(alice, document.id)
    assert again.batch_id is None
    assert len(await container.data.list_documents(alice)) == 1


def test_migration_preserves_legacy_education(tmp_path: Path) -> None:
    path = tmp_path / "legacy.sqlite"
    url = f"sqlite:///{path}"
    command.upgrade(alembic_config(url), "0001")
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO users VALUES (?, ?, ?)", ("alice@example.com", "abc", "2026-01-01"))
        db.execute(
            "INSERT INTO education (id, user_id, created_at, updated_at, institution, area, "
            "degree_type, degree_level, start_month, end_month, grade) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                "edu",
                "alice@example.com",
                "2026-01-01",
                "2026-01-01",
                "University",
                "CS",
                "MSc",
                "master",
                "2001-10",
                "2006-02",
                "5.5 out of 6",
            ),
        )
    migrate(f"sqlite+aiosqlite:///{path}")
    with sqlite3.connect(path) as db:
        assert db.execute(
            "SELECT institution, grade, summary, highlights, links FROM education"
        ).fetchone() == ("University", "5.5 out of 6", "", "[]", "[]")


def test_review_editor_preserves_education_links_and_commas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    item = ProposalItem(
        id="proposal",
        batch_id="batch",
        seq=1,
        payload=CreateEducation(**{**DETAILS, "highlights": ["Research, teaching and awards"]}),
    )
    seen = set()

    def keep(prompt, **kwargs):
        assert prompt not in seen, f"Cannot preserve unchanged {prompt}"
        seen.add(prompt)
        return kwargs["default"]

    monkeypatch.setattr("cvopt.cli.review.Prompt.ask", keep)
    edited = edit_payload(item)
    assert edited["highlights"] == ["Research, teaching and awards"]
    assert edited["links"] == DETAILS["links"]


async def test_details_from_different_degree_at_same_institution_are_not_merged(
    container: Container, alice: UserContext, fake_llm: FakeLLMClient, tmp_path: Path
) -> None:
    old = await container.data.create_education(
        alice,
        {
            "institution": DETAILS["institution"],
            "degree_type": "MSc",
            "degree_level": "master",
            "area": "Mathematics",
            "start_month": "2000-09",
            "end_month": "2002-06",
        },
    )
    incoming = extraction()
    incoming["education"] = [
        {
            **DETAILS,
            "excerpt": DETAILS["institution"],
            "degree_type": "MSc",
            "degree_level": "master",
            "area": "Computer Science",
            "start_month": "2010-09",
            "end_month": "2012-06",
        }
    ]
    fake_llm.script("end_turn", incoming)
    path = tmp_path / "second-degree.txt"
    path.write_text("Education\n" + json.dumps(incoming["education"]))
    result = await container.ingestion.ingest_document(alice, path, DocumentKind.CV)
    await container.ingestion.accept_all(alice, result.batch_id)
    await container.ingestion.apply_batch(alice, result.batch_id)
    entries = await container.data.list_education(alice)
    assert len(entries) == 2
    assert next(e for e in entries if e.id == old.id).highlights == []
    assert next(e for e in entries if e.id != old.id).area == "Computer Science"


async def test_review_apply_deduplicates_against_current_education(
    container: Container, alice: UserContext
) -> None:
    stored = await container.data.create_education(
        alice,
        {
            "institution": "University",
            "degree_level": "master",
            "highlights": ["Research  and teaching"],
        },
    )
    repo = container.repos.for_user(alice)
    document = await repo.insert_document(
        {
            "kind": DocumentKind.CV,
            "filename": "cv.txt",
            "sha256": "b" * 64,
            "mime_type": "text/plain",
            "received_at": TEST_NOW,
        }
    )
    batch = await repo.insert_batch(document.id)
    await repo.insert_item(
        batch.id,
        1,
        UpdateEducation(education_id=stored.id, highlights=["research and teaching", "New detail"]),
        stored.id,
    )
    await container.ingestion.accept_all(alice, batch.id)
    await container.ingestion.apply_batch(alice, batch.id)
    [after] = await container.data.list_education(alice)
    assert after.highlights == ["Research  and teaching", "New detail"]
