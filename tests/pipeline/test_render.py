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
from cvopt.config import load_settings
from cvopt.domain.candidates import Annotation
from cvopt.domain.cv import JSONResume
from cvopt.domain.enums import Fidelity, Provability, RowKind
from cvopt.domain.report import Report, ReportRow
from cvopt.domain.text import split_sentences
from cvopt.errors import InputError
from cvopt.services.render import RenderService, build_render_model
from tests.scenarios import HANDWRITTEN_CV

SETTINGS = load_settings(data_root=Path("/unused"))
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
    return re.sub(r"\s+", " ", " ".join(p.extract_text() or "" for p in PdfReader(path).pages))


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


@pytest.mark.parametrize("template", ["atsthetic", "classic"])
def test_clean_pdf_has_every_statement_in_order_and_no_marks(tmp_path: Path, template: str) -> None:
    settings = SETTINGS.model_copy(deep=True)
    settings.render = settings.render.model_copy(update={"template": template})
    model = build_render_model(CV, settings)
    assert model.template == template
    res = TypstRenderer().render(model, tmp_path / "cv.pdf")
    assert res.pages == 1
    text = pdf_text(res.path)
    assert walk(text, CV) == [""] * len(statements(CV))
    for heading in ("PROFESSIONAL EXPERIENCE", "EDUCATION", "SKILLS", "LANGUAGES"):
        assert heading in text
    assert "03/2021 – present" in text and "Report" not in text


@pytest.mark.parametrize("template", ["atsthetic", "classic"])
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
    assert "Score: 78.5 / 100" in appendix and "Swiss work permit" in appendix


def test_changed_text_loses_its_mark(tmp_path: Path) -> None:
    edited = CV.model_copy(deep=True)
    edited.work[0].highlights[1] = "Rewritten by hand."
    model = build_render_model(edited, SETTINGS, annotations=annotations(), report=REPORT)
    [stmt] = [s for s in model.work[0].highlights if s.text == "Rewritten by hand."]
    assert (stmt.mark, stmt.row) == ("", 0)


def test_german_labels(tmp_path: Path) -> None:
    model = build_render_model(CV, SETTINGS, lang="de")
    text = pdf_text(TypstRenderer().render(model, tmp_path / "de.pdf").path)
    assert "BERUFSERFAHRUNG" in text and "03/2021 – heute" in text


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
