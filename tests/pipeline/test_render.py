"""Rendering (F7): text extracted with pypdf holds every statement in order; annotated
PDFs carry I and E marks exactly on invented and exaggerated statements; clean PDFs
carry none."""

import json
import re
from datetime import date
from pathlib import Path

import pytest
from pypdf import PdfReader

from cvopt.adapters.typst_renderer import TypstRenderer
from cvopt.config import Settings
from cvopt.domain.candidates import Annotation
from cvopt.domain.cv import JSONResume
from cvopt.domain.enums import Fidelity, Provability, RowKind
from cvopt.domain.report import Report, ReportRow
from cvopt.domain.text import split_sentences
from cvopt.errors import InputError
from cvopt.services.render import RenderService, build_render_model
from tests.scenarios import HANDWRITTEN_CV

SETTINGS = Settings()
CV = JSONResume.model_validate_json(HANDWRITTEN_CV.read_text())
FID = {
    "/work/0/highlights/1": Fidelity.EXAGGERATED,
    "/work/1/highlights/1": Fidelity.INVENTED,
    "/skills/0/keywords/3": Fidelity.INVENTED,
    "/basics/label": Fidelity.EXAGGERATED,
}


def statements(cv: JSONResume) -> list[tuple[str, int | None, str]]:
    """(pointer, sentence, text) in reading order."""
    out: list[tuple[str, int | None, str]] = [("/basics/label", None, cv.basics.label)]
    out += [
        ("/basics/summary", i, s) for i, s in enumerate(split_sentences(cv.basics.summary, "en"))
    ]
    for i, w in enumerate(cv.work):
        out += [(f"/work/{i}/highlights/{j}", None, h) for j, h in enumerate(w.highlights)]
    for i, s in enumerate(cv.skills):
        out += [(f"/skills/{i}/keywords/{j}", None, k) for j, k in enumerate(s.keywords)]
    return out


def annotations() -> list[Annotation]:
    return [
        Annotation(
            pointer=p,
            sentence=s,
            text=t,
            fidelity=FID.get(p, Fidelity.UNMODIFIED if n % 2 else Fidelity.REPHRASED),
            provability=Provability.MEDIUM,
            source_claim_ids=[],
            inferred_from=[],
            requirement_ids=["R01"],
            impact=1.0,
            row=n,
        )
        for n, (p, s, t) in enumerate(statements(CV), start=1)
    ]


REPORT = Report(
    title="Senior Data Scientist",
    company="Nordwind Retail AG",
    created=date(2026, 6, 30),
    total=78.5,
    gap_points=21.5,
    by_fidelity={"unmodified": 60.0, "exaggerated": 18.5},
    by_provability={"medium": 78.5},
    logistics=["Swiss work permit"],
    timeline_gaps=[],
    rows=[
        ReportRow(
            row=1,
            kind=RowKind.IN_CV,
            claim="Lead Data Scientist",
            fidelity=Fidelity.EXAGGERATED,
            provability=Provability.MEDIUM,
            impact=4.0,
            requirements=["R06"],
            source="Lead Data Scientist",
        )
    ],
)


def pdf_text(path: Path) -> str:
    return re.sub(
        r"\s+",
        " ",
        " ".join(p.extract_text(extraction_mode="layout") or "" for p in PdfReader(path).pages),
    )


def walk(text: str, cv: JSONResume) -> list[str]:
    """Finds every statement in order; returns the mark found right after each."""
    pos, marks = 0, []
    for _, _, stmt in statements(cv):
        found = text.find(stmt, pos)
        assert found >= 0, f"missing or out of order: {stmt!r}"
        pos = found + len(stmt)
        m = re.match(r" ?([EI]?\d+)\b", text[pos:])
        marks.append(m.group(1) if m else "")
    return marks


@pytest.fixture
def service() -> RenderService:
    return RenderService(TypstRenderer(), SETTINGS)


@pytest.mark.parametrize("template", ["altacv-mod-1"])
def test_clean_pdf_has_every_statement_in_order_and_no_marks(tmp_path: Path, template: str) -> None:
    settings = SETTINGS.model_copy(deep=True)
    settings.render = settings.render.model_copy(update={"template": template})
    model = build_render_model(CV, settings)
    assert model.template == template
    res = TypstRenderer().render(model, tmp_path / "cv.pdf")
    assert res.pages == 1
    text = pdf_text(res.path)
    assert walk(text, CV) == [""] * len(statements(CV))
    for heading in ("EXPERIENCE", "EDUCATION", "SKILLS", "LANGUAGES"):
        assert heading in text
    assert "Mar 2021 – Present" in text and "Report" not in text


@pytest.mark.parametrize("template", ["altacv-mod-1"])
def test_annotated_pdf_marks_exactly_exaggerated_and_invented(
    tmp_path: Path, template: str
) -> None:
    settings = SETTINGS.model_copy(deep=True)
    settings.render = settings.render.model_copy(update={"template": template})
    model = build_render_model(CV, settings, annotations=annotations(), report=REPORT)
    assert model.template == template
    res = TypstRenderer().render(model, tmp_path / "cv-annotated.pdf")
    assert res.pages == 2  # the CV, then the report appendix
    cv_page = re.sub(r"\s+", " ", PdfReader(res.path).pages[0].extract_text())
    expected = [
        ({Fidelity.EXAGGERATED: "E", Fidelity.INVENTED: "I"}.get(a.fidelity, "") + str(a.row))
        for a in annotations()
    ]
    assert walk(cv_page, CV) == expected
    assert "Annotated target CV for Senior Data Scientist at Nordwind Retail AG" in cv_page
    appendix = pdf_text(res.path).split("REPORT", 1)[1]
    assert "Target CV score: 78.5 / 100" in appendix and "Swiss work permit" in appendix


def test_changed_text_loses_its_mark(tmp_path: Path) -> None:
    edited = CV.model_copy(deep=True)
    edited.work[0].highlights[1] = "Rewritten by hand."
    model = build_render_model(edited, SETTINGS, annotations=annotations(), report=REPORT)
    [stmt] = [s for s in model.work[0].highlights if s.text == "Rewritten by hand."]
    assert (stmt.mark, stmt.row) == ("", 0)


def test_german_labels(tmp_path: Path) -> None:
    model = build_render_model(CV, SETTINGS, lang="de")
    text = pdf_text(TypstRenderer().render(model, tmp_path / "de.pdf").path)
    assert "BERUFSERFAHRUNG" in text and "Mär 2021 – heute" in text


def test_render_service_files(service: RenderService, tmp_path: Path) -> None:
    (tmp_path / "annotations.json").write_text(
        json.dumps([a.model_dump(mode="json") for a in annotations()])
    )
    (tmp_path / "report.json").write_text(REPORT.model_dump_json())
    res = service.render(
        HANDWRITTEN_CV,
        tmp_path / "out.pdf",
        annotations_path=tmp_path / "annotations.json",
        report_path=tmp_path / "report.json",
    )
    assert res.pages == 2
    with pytest.raises(InputError):
        service.render(HANDWRITTEN_CV, tmp_path / "x.pdf", annotations_path=tmp_path / "a.json")


def test_structured_contacts_and_literal_text(tmp_path: Path) -> None:
    cv = JSONResume.model_validate(
        {
            "basics": {
                "name": "Alice #literal[*]",
                "label": "Research #literal[*]",
                "email": "alice@example.org",
                "summary": "Code #literal[*] remains literal.",
                "profiles": [
                    {
                        "network": "LinkedIn",
                        "username": "alice",
                        "url": "https://linkedin.com/in/alice",
                    },
                    {"network": "Custom Network", "url": "https://example.org/profile"},
                ],
            },
            "languages": [{"language": "English", "fluency": "C2"}, {"language": "German"}],
            "certificates": [
                {"name": "Certificate", "issuer": "Example Issuer", "date": "2022-05"}
            ],
        }
    )
    result = TypstRenderer().render(build_render_model(cv, SETTINGS), tmp_path / "literal.pdf")
    text = pdf_text(result.path)
    assert "ALICE #LITERAL[*]" in text
    assert "Research #literal[*]" in text and "Code #literal[*] remains literal." in text
    assert "alice" in text and "https://example.org/profile" in text.replace(" ", "")
    assert "English" in text and "C2" in text and "German" in text
    assert "Example Issuer" in text and "May 2022" in text
    uris = [
        a.get_object().get("/A", {}).get("/URI")
        for page in PdfReader(result.path).pages
        for a in page.get("/Annots", [])
    ]
    assert "https://linkedin.com/in/alice" in uris
    assert "https://example.org/profile" in uris
    reader = PdfReader(result.path)
    assert reader.metadata.author == "Alice #literal[*]"
    assert reader.metadata.subject == "Code #literal[*] remains literal."


def test_default_fonts_and_native_margins(tmp_path: Path) -> None:
    result = TypstRenderer().render(build_render_model(CV, Settings()), tmp_path / "native.pdf")
    page = PdfReader(result.path).pages[0]
    fonts = [str(font.get_object()["/BaseFont"]) for font in page["/Resources"]["/Font"].values()]
    assert any("Lato" in font for font in fonts)
    assert any("FontAwesome" in font for font in fonts)
    positions = []
    page.extract_text(
        visitor_text=lambda text, cm, tm, font, size: positions.append(
            (text, cm[4] + tm[4] * cm[0] + tm[5] * cm[2])
        )
    )
    name_x = next(x for text, x in positions if "ALICE" in text)
    assert name_x == pytest.approx(9 * 72 / 25.4, abs=0.5)


def test_typography_overrides_reach_pdf(tmp_path: Path) -> None:
    settings = Settings()
    settings.render.font = "Source Sans 3"
    settings.render.font_size_pt = 11
    settings.render.margin_mm = 20
    result = TypstRenderer().render(build_render_model(CV, settings), tmp_path / "override.pdf")
    page = PdfReader(result.path).pages[0]
    fonts = [str(font.get_object()["/BaseFont"]) for font in page["/Resources"]["/Font"].values()]
    assert any("SourceSans3" in font for font in fonts)
    positions = []
    page.extract_text(
        visitor_text=lambda text, cm, tm, font, size: positions.append(
            (text, cm[4] + tm[4] * cm[0] + tm[5] * cm[2], size)
        )
    )
    name_x = next(x for text, x, _ in positions if "ALICE" in text)
    assert name_x == pytest.approx(20 * 72 / 25.4, abs=0.5)
    assert any(size == pytest.approx(11) for text, _, size in positions if "Led a team" in text)


def test_username_only_profile_renders_without_an_empty_link(tmp_path: Path) -> None:
    cv = JSONResume.model_validate(
        {
            "basics": {
                "name": "Alice",
                "profiles": [{"network": "Custom", "username": "alice-handle"}],
            }
        }
    )
    result = TypstRenderer().render(build_render_model(cv, SETTINGS), tmp_path / "username.pdf")
    assert "alice-handle" in pdf_text(result.path)
    uris = [
        annotation.get_object().get("/A", {}).get("/URI")
        for page in PdfReader(result.path).pages
        for annotation in page.get("/Annots", [])
    ]
    assert "" not in uris


def test_free_text_language_fluency_is_preserved_without_rating_inference(tmp_path: Path) -> None:
    cv = JSONResume.model_validate(
        {
            "basics": {"name": "Alice"},
            "languages": [
                {"language": "French", "fluency": "Full Professional"},
                {"language": "Greek", "fluency": "Professional Working"},
            ],
        }
    )
    result = TypstRenderer().render(build_render_model(cv, SETTINGS), tmp_path / "languages.pdf")
    text = pdf_text(result.path)
    assert "French" in text and "Full Professional" in text
    assert "Greek" in text and "Professional Working" in text


def test_certificate_groups_flow_across_pages_without_clipping(tmp_path: Path) -> None:
    cv = JSONResume.model_validate(
        {
            "basics": {"name": "Alice"},
            "certificates": [
                {
                    "name": (
                        f"Certificate {i:03d}: Advanced Professional Certification "
                        "in Computer Science and Engineering"
                    ),
                    "issuer": "Example Academy",
                    "date": "2022-05",
                }
                for i in range(60)
            ],
        }
    )
    result = TypstRenderer().render(build_render_model(cv, SETTINGS), tmp_path / "certificates.pdf")
    reader = PdfReader(result.path)
    assert result.pages >= 2
    text = pdf_text(result.path)
    for i in range(60):
        assert f"Certificate {i:03d}" in text
    for page in reader.pages:
        positions = []
        page.extract_text(
            visitor_text=lambda text, cm, tm, font, size, positions=positions: positions.append(
                (text, cm[5] + tm[4] * cm[1] + tm[5] * cm[3])
            )
        )
        assert all(
            0 < y < float(page.mediabox.height) for text, y in positions if "Certificate" in text
        )


@pytest.mark.parametrize(
    "lang,profile_label,requirement_label",
    [
        ("en", "Profile-supported coverage", "Requirement coverage"),
        ("de", "Profilgestützte Abdeckung", "Anforderungsabdeckung"),
    ],
)
def test_explanatory_pdf_has_metrics_requirements_sources_and_deduplicates_quotes(
    tmp_path, lang, profile_label, requirement_label
):
    from cvopt.domain.provenance import AssertionProvenance, SourceProvenance, StatementProvenance
    from cvopt.domain.report import RequirementExplanation, RequirementSupport

    quote = "Unique source paragraph demonstrating supported delivery in Python."
    source = SourceProvenance(
        id="source",
        kind="reference_letter",
        state="stale",
        snapshot_origin="captured",
        document_id="doc",
        document_name="letter.pdf",
        excerpt=quote,
    )
    a = AssertionProvenance(
        ref="claim:a",
        original_text="Original Python delivery",
        eligible=True,
        provability=Provability.MEDIUM,
        sources=[source],
    )
    report = REPORT.model_copy(
        update={
            "profile_supported_score": 90.0,
            "target_minus_profile": -11.5,
            "requirements": [
                RequirementExplanation(
                    requirement_id="R01",
                    requirement_text="Python delivery",
                    category="hard_skill",
                    importance="must",
                    profile_coverage=0.9,
                    target_coverage=0.785,
                    supporting_elements=[
                        RequirementSupport(
                            element_id="claim:a",
                            text="Original Python delivery",
                            value=0.9,
                            winning_share=1,
                            provenance_refs=["claim:a"],
                        )
                    ],
                    target_statements=[
                        RequirementSupport(
                            element_id="/basics/label",
                            text="Lead Data Scientist",
                            value=0.785,
                            winning_share=1,
                            provenance_refs=["claim:a"],
                        )
                    ],
                    labels=["evidence", "partial support"],
                    next_actions=["Review recorded source.", "Clarify recorded scope."],
                )
            ],
            "provenance": [
                StatementProvenance(
                    element_id=f"statement:{i}",
                    text=f"Statement {i}",
                    source_claim_ids=["a"],
                    transformation_reason="rephrased",
                    sources=[a],
                )
                for i in range(8)
            ],
        }
    )
    model = build_render_model(CV, SETTINGS, lang=lang, annotations=annotations(), report=report)
    result = TypstRenderer().render(model, tmp_path / f"report-{lang}.pdf")
    text = pdf_text(result.path)
    assert profile_label in text and requirement_label in text
    assert "letter.pdf" in text
    expected = (
        ("Hard skill", "Required", "Selected", "Exaggerated", "Medium", "Reference letter", "Stale")
        if lang == "en"
        else (
            "Fachkompetenz",
            "Erforderlich",
            "Ausgewählt",
            "Übertrieben",
            "Mittel",
            "Arbeitszeugnis",
            "Veraltet",
        )
    )
    assert all(value in text for value in expected)
    assert "Original Python delivery" in text
    assert text.count(quote) == 1
    assert "-11.5" in text


@pytest.mark.parametrize("lang,row_label", [("en", "CV row"), ("de", "CV-Zeile")])
def test_pdf_requirement_support_uses_readable_refs_and_keeps_ids_in_appendix(
    tmp_path, lang, row_label
):
    from cvopt.domain.provenance import AssertionProvenance
    from cvopt.domain.report import RequirementExplanation, RequirementSupport

    claim_ref = "claim:01SYNTHETICCLAIMIDENTIFIER00"
    position_ref = "position:01SYNTHETICPOSITIONIDENT000"
    pointer = "/work/0/highlights/0"
    hit = RequirementSupport(
        element_id=claim_ref,
        text="Python delivery",
        value=1,
        winning_share=1,
        provenance_refs=[claim_ref, position_ref],
        position_ref=position_ref,
        start_month="2020-01",
        end_month="2022-12",
    )
    report = REPORT.model_copy(
        update={
            "rows": [
                REPORT.rows[0].model_copy(
                    update={"row": 7, "element_id": pointer, "claim": "Python delivery"}
                )
            ],
            "requirements": [
                RequirementExplanation(
                    requirement_id="R1",
                    requirement_text="Python experience",
                    category="years_experience",
                    importance="must",
                    profile_coverage=1,
                    target_coverage=1,
                    supporting_elements=[hit],
                    target_statements=[hit.model_copy(update={"element_id": pointer})],
                )
            ],
            "profile_assertions": [
                AssertionProvenance(
                    ref=claim_ref,
                    original_text="Python delivery",
                    eligible=True,
                    provability="medium",
                ),
                AssertionProvenance(
                    ref=position_ref,
                    original_text="Engineer · Example",
                    eligible=True,
                    provability="medium",
                ),
            ],
        }
    )
    model = build_render_model(CV, SETTINGS, lang=lang, annotations=annotations(), report=report)
    text = pdf_text(TypstRenderer().render(model, tmp_path / f"readable-{lang}.pdf").path)
    assert f"{row_label} 7" in text
    requirement_text = text.split("R1 · Python experience", 1)[1].split(
        "Original assertions and sources" if lang == "en" else "Originalaussagen und Quellen", 1
    )[0]
    assert (
        claim_ref not in requirement_text
        and position_ref not in requirement_text
        and pointer not in requirement_text
    )
    assert "A2 · 2020-01 - 2022-12" in requirement_text
    assert claim_ref in text and position_ref in text
    assert report.requirements[0].target_statements[0].element_id == pointer
