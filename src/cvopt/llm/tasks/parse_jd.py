"""parse_jd: a job description into requirements (P1 §8.1)."""

from cvopt.domain.base import Model
from cvopt.domain.enums import DegreeLevel, Importance, RequirementCategory
from cvopt.domain.jd import JobDescription, Requirement
from cvopt.domain.rules import CEFR_LEVELS
from cvopt.llm.task import Task
from cvopt.llm.tasks.common import tagged
from cvopt.ports import FilePart, TextPart

C = RequirementCategory


class JDInput(Model):
    text: str
    title_hint: str  # e.g. the page title; may be ""


class ExRequirement(Model):
    text: str
    category: RequirementCategory
    importance: Importance
    keywords: list[str]
    synonyms: list[str]
    years: float  # years_experience only, else 0
    language_code: str  # language only, else ""
    language_level: str  # language only: A1–C2 or native, else ""
    degree_level: str  # education only: secondary … doctorate, else ""
    degree_fields: list[str]
    certificate_names: list[str]


class JDOutput(Model):
    is_job_description: bool
    title: str
    company: str
    language: str  # ISO 639-1 of the JD
    requirements: list[ExRequirement]
    logistics: list[str]


def render(inp: JDInput) -> list[TextPart | FilePart]:
    hint = f"Page title: {inp.title_hint}\n\n" if inp.title_hint else ""
    return [TextPart(hint + tagged("job_description", inp.text))]


def check(inp: JDInput, out: JDOutput) -> None:
    if not out.is_job_description:
        return
    if not out.requirements:
        raise ValueError("a job description needs at least one requirement")
    levels = {d.value for d in DegreeLevel}
    for i, r in enumerate(out.requirements):
        where = f"requirements[{i}]"
        if r.category is C.LOGISTICS:
            raise ValueError(f"{where}: put logistics items in `logistics`, not requirements")
        if not r.keywords:
            raise ValueError(f"{where}: at least one keyword is required")
        if r.category is C.YEARS_EXPERIENCE and r.years <= 0:
            raise ValueError(f"{where}: years_experience needs years > 0")
        if r.category is C.LANGUAGE and (
            len(r.language_code) != 2 or r.language_level not in CEFR_LEVELS
        ):
            raise ValueError(f"{where}: language needs language_code and a CEFR language_level")
        if r.category is C.EDUCATION and r.degree_level not in levels:
            raise ValueError(f"{where}: education needs degree_level, one of {sorted(levels)}")


def to_job_description(out: JDOutput) -> JobDescription:
    """Ids R01, R02, … in JD order; "" and 0 become None where the domain uses None."""
    return JobDescription(
        title=out.title,
        company=out.company,
        language=out.language,
        logistics=out.logistics,
        requirements=[
            Requirement(
                id=f"R{i:02d}",
                text=r.text,
                category=r.category,
                importance=r.importance,
                keywords=r.keywords,
                synonyms=r.synonyms,
                years=r.years if r.category is C.YEARS_EXPERIENCE else None,
                language_code=r.language_code or None,
                language_level=r.language_level or None,
                degree_level=DegreeLevel(r.degree_level) if r.degree_level else None,
                degree_fields=r.degree_fields,
                certificate_names=r.certificate_names,
            )
            for i, r in enumerate(out.requirements, start=1)
        ],
    )


PARSE_JD = Task(
    name="parse_jd",
    version="1",
    tier="strong",
    input_model=JDInput,
    output_model=JDOutput,
    max_tokens=6000,
    render=render,
    check=check,
)
