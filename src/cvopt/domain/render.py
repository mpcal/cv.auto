"""The annotated presentation model adapted to AltaCV by the Typst template."""

from typing import Literal

from cvopt.domain.base import Model
from cvopt.domain.cv import JRAward, JRBasics, JRCertificate, JRLanguage
from cvopt.domain.records import EducationLink
from cvopt.domain.report import ReportRow


class RStatement(Model):
    text: str
    mark: Literal["", "E", "I"]  # exaggerated, invented; always "" in clean mode
    row: int  # report row number; 0 in clean mode


class RPosition(Model):
    title: str
    employer: str
    location: str
    start_date: str
    end_date: str
    highlights: list[RStatement]


class REntry(Model):  # education, including presentation-filtered details
    title: str
    detail: str
    start_date: str
    end_date: str
    summary: str = ""
    highlights: list[str] = []
    courses: list[str] = []
    score: str = ""
    links: list[EducationLink] = []


class RSkillGroup(Model):
    name: str
    keywords: list[RStatement]


class RenderModel(Model):
    template: Literal["altacv-mod-1"] = "altacv-mod-1"
    mode: Literal["annotated", "clean"]
    lang: str
    font: str
    font_size_pt: float
    margin_mm: float | None
    labels: dict[str, str]  # labels.toml section for lang, English as fallback
    months: list[str]
    basics: JRBasics
    headline: RStatement | None
    summary: list[RStatement]
    work: list[RPosition]
    education: list[REntry]
    skills: list[RSkillGroup]
    languages: list[JRLanguage]
    certificates: list[JRCertificate]
    awards: list[JRAward]
    legend: str  # annotated only
    report_summary: list[str]  # annotated only: score, splits, logistics, timeline gaps
    report: list[ReportRow]  # annotated only
