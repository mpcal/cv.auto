"""RenderService (F7): clean and annotated PDFs from a JSON Resume file."""

from pathlib import Path
from typing import Literal, cast

from pydantic import TypeAdapter, ValidationError

from cvopt.config import Settings
from cvopt.domain.candidates import Annotation
from cvopt.domain.cv import JSONResume
from cvopt.domain.enums import Fidelity
from cvopt.domain.records import EducationLink
from cvopt.domain.render import (
    RenderModel,
    REntry,
    REvidence,
    RPosition,
    RRequirement,
    RSkillGroup,
    RStatement,
)
from cvopt.domain.report import Report, RequirementSupport
from cvopt.domain.rules import cefr_level
from cvopt.domain.tables import load_table
from cvopt.domain.text import normalize, split_sentences
from cvopt.errors import InputError
from cvopt.ports import Renderer, RenderResult
from cvopt.services.publication import pin_paths
from cvopt.services.scoring import load_cv, read_json

ANNOTATIONS = TypeAdapter(list[Annotation])
MARKS = {Fidelity.EXAGGERATED: "E", Fidelity.INVENTED: "I"}


def labels(lang: str) -> dict[str, str]:
    table = cast(dict[str, dict[str, str]], load_table("labels"))
    return {**table["en"], **table.get(lang, {})}


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


def display_value(lab: dict[str, str], prefix: str, value: str) -> str:
    """Localize presentation values while JSON/CSV retain stable machine codes."""
    return lab.get(f"{prefix}_{value}", value)


def report_summary(report: Report, lab: dict[str, str]) -> list[str]:
    def split(d: dict[str, float]) -> str:
        return ", ".join(
            f"{display_value(lab, 'fidelity', display_value(lab, 'provability', k))} {v:.1f}"
            for k, v in d.items()
        )

    lines = [
        f"{lab['target_score']}: {report.total:.1f} / 100 · {lab['gaps']}: {report.gap_points:.1f}",
        lab["target_explanation"],
        f"{lab['profile_score']}: {report.profile_supported_score:.1f} / 100"
        if report.profile_supported_score is not None
        else f"{lab['profile_score']}: {lab['unavailable']}",
        lab["profile_explanation"],
        f"{lab['score_difference']}: {report.target_minus_profile:+.1f} pp"
        if report.target_minus_profile is not None
        else f"{lab['score_difference']}: {lab['unavailable']}",
        lab["difference_explanation"],
        lab["uncertainty"],
        f"{lab['by_fidelity']}: {split(report.by_fidelity)}",
        f"{lab['by_provability']}: {split(report.by_provability)}",
    ]
    if report.logistics:
        lines.append(f"{lab['logistics']}: {'; '.join(report.logistics)}")
    if report.timeline_gaps:
        lines.append(f"{lab['timeline_gaps']}: {'; '.join(report.timeline_gaps)}")
    return lines


def report_details(report: Report, lab: dict[str, str]) -> dict[str, object]:
    """Readable references; each original assertion and quotation body appears once."""
    assertions = {a.ref: a for a in report.profile_assertions}
    assertions.update({a.ref: a for p in report.provenance for a in p.sources})
    assertion_refs = {ref: f"A{i}" for i, ref in enumerate(assertions, 1)}
    quote_refs: dict[str, str] = {}
    quotes = []
    originals = []
    for ref, a in assertions.items():
        lines = [
            f"{ref} · {display_value(lab, 'status', a.status)} · "
            f"{display_value(lab, 'provability', a.provability.value)}",
            a.original_text,
        ]
        for source in a.sources:
            quote_ref = lab["no_excerpt"]
            if source.excerpt:
                if source.excerpt not in quote_refs:
                    quote_refs[source.excerpt] = f"E{len(quote_refs) + 1}"
                    quotes.append(REvidence(ref=quote_refs[source.excerpt], text=source.excerpt))
                quote_ref = quote_refs[source.excerpt]
            lines.append(
                f"{source.id} · {display_value(lab, 'source_kind', source.kind)} · "
                f"{display_value(lab, 'source_state', source.state)} · "
                f"{source.document_name or source.reference_name or lab['no_document']} · "
                f"{quote_ref} · {display_value(lab, 'snapshot_origin', source.snapshot_origin)}"
            )
        if not a.sources:
            lines.append(lab["no_source"])
        originals.append(REvidence(ref=assertion_refs[ref], text="\n".join(lines)))
    rows = {}
    by_element = {p.element_id: p for p in report.provenance}
    for row in report.rows:
        matches = (
            [by_element[row.element_id]]
            if row.element_id in by_element
            else [p for p in by_element.values() if p.text == row.claim]
            if row.element_id is None
            else []
        )
        refs = list(dict.fromkeys(assertion_refs[a.ref] for p in matches for a in p.sources))
        reasons = list(
            dict.fromkeys(
                display_value(
                    lab, "reason", display_value(lab, "fidelity", p.transformation_reason)
                )
                for p in matches
                if p.transformation_reason
            )
        )
        # Source/drop reasons remain visible even for legacy reports with no structure.
        reason = display_value(lab, "reason", row.source)
        if reason.startswith("rule:"):
            reason = lab["reason_validation"]
        rows[str(row.row)] = "; ".join([*refs, *reasons] or [reason])
    requirements = []

    element_rows = {row.element_id: row.row for row in report.rows if row.element_id}

    def hit_text(hit: RequirementSupport, *, target: bool = False) -> str:
        refs = ", ".join(
            assertion_refs[ref] for ref in hit.provenance_refs if ref in assertion_refs
        )
        text = hit.text if len(hit.text) <= 160 else hit.text[:157] + "..."
        row = element_rows.get(hit.element_id) if target else None
        heading = f"{lab['cv_row']} {row}" if row is not None else refs
        position = assertion_refs.get(hit.position_ref or "", lab["unavailable"])
        period = (
            f" · {lab['qualifying_period']}: {position} · {hit.start_month} - {hit.end_month}"
            if hit.position_ref
            else ""
        )
        return (
            (f"{heading}: " if heading else "") + f"{text} ({100 * hit.value:.0f}%; "
            f"{lab['winning_share']} {100 * hit.winning_share:.0f}%)"
            + (f" [{refs}]" if refs and row is not None else "")
            + period
        )

    for r in report.requirements:
        supported = (
            lab["unavailable"] if r.profile_coverage is None else f"{100 * r.profile_coverage:.1f}%"
        )
        requirements.append(
            RRequirement(
                heading=(
                    f"{r.requirement_id} · {r.requirement_text} · "
                    f"{display_value(lab, 'category', r.category.value)} / "
                    f"{display_value(lab, 'importance', r.importance.value)}"
                ),
                coverage=(
                    f"{lab['profile_score']}: {supported} · "
                    f"{lab['target_score']}: {100 * r.target_coverage:.1f}%"
                ),
                support=[hit_text(hit) for hit in r.supporting_elements],
                target=[hit_text(hit, target=True) for hit in r.target_statements],
                context=[hit_text(hit) for hit in r.unconfirmed_context],
                actions=[
                    f"{lab.get('gap_' + label.replace(' ', '_'), label)}: "
                    f"{lab.get('action_' + label.replace(' ', '_'), action)}"
                    for label, action in zip(r.labels, r.next_actions, strict=True)
                ],
            )
        )
    return dict(
        report_requirements=requirements,
        report_assertions=originals,
        report_sources=quotes,
        report_row_sources=rows,
    )


def build_render_model(
    cv: JSONResume,
    settings: Settings,
    *,
    lang: str = "en",
    annotations: list[Annotation] | None = None,
    report: Report | None = None,
) -> RenderModel:
    lab = labels(lang)
    month_names = cast(dict[str, list[str]], load_table("labels")["months"])
    marks = Marks(annotations)
    b = cv.basics
    legend = ""
    if annotations is not None and report is not None:
        legend = lab["legend"].format(
            title=report.title, company=report.company, date=report.created.isoformat()
        )
    return RenderModel(
        template=settings.render.template,
        mode="annotated" if annotations is not None else "clean",
        lang=lang,
        font=settings.render.font,
        font_size_pt=settings.render.font_size_pt,
        margin_mm=settings.render.margin_mm,
        labels=lab,
        months=month_names.get(lang, month_names["en"]),
        basics=b,
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
                start_date=w.startDate,
                end_date=w.endDate,
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
                start_date=e.startDate,
                end_date=e.endDate,
                summary=e.summary if settings.render.education_details == "all" else "",
                highlights=e.highlights if settings.render.education_details == "all" else [],
                courses=e.courses if settings.render.education_details == "all" else [],
                score=(
                    f"{lab['grade']}: {e.score}"
                    if e.score and settings.render.education_details == "all"
                    else ""
                ),
                links=(
                    [*e.links, *([EducationLink(label=e.institution, url=e.url)] if e.url else [])]
                    if settings.render.education_details == "all"
                    else []
                ),
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
            lg.model_copy(update={"fluency": cefr_level(lg.fluency) or lg.fluency})
            for lg in cv.languages
        ],
        certificates=cv.certificates,
        awards=cv.awards,
        legend=legend,
        report_summary=report_summary(report, lab) if report and annotations is not None else [],
        report=report.rows if report and annotations is not None else [],
        **(report_details(report, lab) if report and annotations is not None else {}),
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
        cv_path, out_path = pin_paths(cv_path, out_path)
        if annotations_path is not None:
            _, annotations_path = pin_paths(cv_path, annotations_path)
        if report_path is not None:
            _, report_path = pin_paths(cv_path, report_path)
        cv = load_cv(cv_path)
        model = build_render_model(
            cv,
            self._settings,
            lang=cv_language(cv_path, cv),
            annotations=load_annotations(annotations_path) if annotations_path else None,
            report=load_report(report_path) if report_path else None,
        )
        return self._renderer.render(model, out_path)
