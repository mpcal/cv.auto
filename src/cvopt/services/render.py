"""RenderService (F7): clean and annotated PDFs from a JSON Resume file."""

from pathlib import Path
from typing import Literal, cast

from pydantic import TypeAdapter, ValidationError

from cvopt.config import Settings
from cvopt.domain.candidates import Annotation
from cvopt.domain.cv import JSONResume, to_month
from cvopt.domain.enums import Fidelity
from cvopt.domain.render import RenderModel, REntry, RPosition, RSkillGroup, RStatement
from cvopt.domain.report import Report
from cvopt.domain.rules import cefr_level
from cvopt.domain.tables import load_table
from cvopt.domain.text import normalize, split_sentences
from cvopt.errors import InputError
from cvopt.ports import Renderer, RenderResult
from cvopt.services.scoring import load_cv, read_json

ANNOTATIONS = TypeAdapter(list[Annotation])
MARKS = {Fidelity.EXAGGERATED: "E", Fidelity.INVENTED: "I"}


def labels(lang: str) -> dict[str, str]:
    table = cast(dict[str, dict[str, str]], load_table("labels"))
    return {**table["en"], **table.get(lang, {})}


def fmt_month(value: str) -> str:
    month = to_month(value) if value else None
    return f"{month[5:]}/{month[:4]}" if month else ""


def fmt_dates(start: str, end: str, present: str, *, open_ended: bool) -> str:
    a = fmt_month(start)
    b = fmt_month(end) if end else (present if open_ended else "")
    return f"{a} – {b}" if a and b else a or b


class Marks:
    """Looks up annotation marks by pointer and text; a changed text gets no mark."""

    def __init__(self, annotations: list[Annotation] | None) -> None:
        self._by_key = {(a.pointer, a.sentence): a for a in annotations or []}
        self.annotated = annotations is not None

    def stmt(self, text: str, pointer: str, sentence: int | None = None) -> RStatement:
        a = self._by_key.get((pointer, sentence))
        if not self.annotated or a is None or normalize(a.text) != normalize(text):
            return RStatement(text=text, mark="", row=0)
        mark = cast(Literal["", "E", "I"], MARKS.get(a.fidelity, ""))
        return RStatement(text=text, mark=mark, row=a.row)


def report_summary(report: Report, lab: dict[str, str]) -> list[str]:
    def split(d: dict[str, float]) -> str:
        return ", ".join(f"{k} {v:.1f}" for k, v in d.items())

    lines = [
        f"{lab['score']}: {report.total:.1f} / 100 · {lab['gaps']}: {report.gap_points:.1f}",
        f"{lab['by_fidelity']}: {split(report.by_fidelity)}",
        f"{lab['by_provability']}: {split(report.by_provability)}",
    ]
    if report.logistics:
        lines.append(f"{lab['logistics']}: {'; '.join(report.logistics)}")
    if report.timeline_gaps:
        lines.append(f"{lab['timeline_gaps']}: {'; '.join(report.timeline_gaps)}")
    return lines


def build_render_model(
    cv: JSONResume,
    settings: Settings,
    *,
    lang: str = "en",
    annotations: list[Annotation] | None = None,
    report: Report | None = None,
) -> RenderModel:
    lab = labels(lang)
    marks = Marks(annotations)
    b = cv.basics
    location = ", ".join(
        x for x in (b.location.city, b.location.countryCode or b.location.region) if x
    )
    contact = [x for x in (b.email, b.phone, location, b.url) if x]
    contact += [p.url for p in b.profiles if p.url]
    legend = ""
    if annotations is not None and report is not None:
        legend = lab["legend"].format(
            title=report.title, company=report.company, date=report.created.isoformat()
        )
    return RenderModel(
        mode="annotated" if annotations is not None else "clean",
        lang=lang,
        font=settings.render.font,
        font_size_pt=settings.render.font_size_pt,
        margin_mm=settings.render.margin_mm,
        labels=lab,
        name=b.name,
        contact=contact,
        headline=marks.stmt(b.label, "/basics/label") if b.label else None,
        summary=[
            marks.stmt(s, "/basics/summary", i)
            for i, s in enumerate(split_sentences(b.summary, lang))
        ],
        work=[
            RPosition(
                title=w.position,
                employer=w.name,
                location=w.location,
                dates=fmt_dates(w.startDate, w.endDate, lab["present"], open_ended=True),
                highlights=[
                    marks.stmt(h, f"/work/{i}/highlights/{j}") for j, h in enumerate(w.highlights)
                ],
            )
            for i, w in enumerate(cv.work)
        ],
        education=[
            REntry(
                title=" ".join(x for x in (e.studyType, e.area) if x) or e.institution,
                detail=e.institution if (e.studyType or e.area) else "",
                dates=fmt_dates(e.startDate, e.endDate, "", open_ended=False),
            )
            for e in cv.education
        ],
        skills=[
            RSkillGroup(
                name=s.name,
                keywords=[
                    marks.stmt(k, f"/skills/{i}/keywords/{j}") for j, k in enumerate(s.keywords)
                ],
            )
            for i, s in enumerate(cv.skills)
        ],
        languages=[
            f"{lg.language} ({cefr_level(lg.fluency) or lg.fluency})" if lg.fluency else lg.language
            for lg in cv.languages
        ],
        certificates=[
            REntry(title=c.name, detail=c.issuer, dates=fmt_month(c.date)) for c in cv.certificates
        ],
        legend=legend,
        report_summary=report_summary(report, lab) if report and annotations is not None else [],
        report=report.rows if report and annotations is not None else [],
    )


def load_annotations(path: Path) -> list[Annotation]:
    try:
        return ANNOTATIONS.validate_python(read_json(path, "annotations"))
    except ValidationError as err:
        raise InputError(f"{path} is not an annotation file: {err}") from err


def load_report(path: Path) -> Report:
    try:
        return Report.model_validate(read_json(path, "report"))
    except ValidationError as err:
        raise InputError(f"{path} is not a report file: {err}") from err


def cv_language(cv_path: Path, cv: JSONResume) -> str:
    """The run's language when the CV sits in a run folder, else the CV's meta, else en."""
    run_json = cv_path.parent / "run.json"
    if run_json.is_file():
        info = read_json(run_json, "run.json")
        if isinstance(info, dict) and isinstance(info.get("language"), str):
            return str(info["language"])
    lang = cv.meta.get("language")
    return lang if isinstance(lang, str) else "en"


class RenderService:
    def __init__(self, renderer: Renderer, settings: Settings) -> None:
        self._renderer = renderer
        self._settings = settings

    def render_model(self, model: RenderModel, out_path: Path) -> RenderResult:
        return self._renderer.render(model, out_path)

    def render(
        self,
        cv_path: Path,
        out_path: Path,
        *,
        annotations_path: Path | None = None,
        report_path: Path | None = None,
    ) -> RenderResult:
        """Clean without annotations; annotated needs both annotations and the report."""
        if (annotations_path is None) != (report_path is None):
            raise InputError("An annotated render needs both --annotations and --report.")
        cv = load_cv(cv_path)
        model = build_render_model(
            cv,
            self._settings,
            lang=cv_language(cv_path, cv),
            annotations=load_annotations(annotations_path) if annotations_path else None,
            report=load_report(report_path) if report_path else None,
        )
        return self._renderer.render(model, out_path)
