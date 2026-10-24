"""The flat render model the Typst template lays out (P3 §8.1)."""

from typing import Literal

from cvopt.domain.base import Model
from cvopt.domain.report import ReportRow


class RStatement(Model):
    text: str
    mark: Literal["", "E", "I"]  # exaggerated, invented; always "" in clean mode
    row: int  # report row number; 0 in clean mode


class RPosition(Model):
    title: str
    employer: str
    location: str
    dates: str  # "04/2019 – 12/2022", or "03/2023 – present" from labels
    highlights: list[RStatement]


class REntry(Model):  # education and certificates
    title: str
    detail: str
    dates: str


class RSkillGroup(Model):
    name: str
    keywords: list[RStatement]


class RenderModel(Model):
    mode: Literal["annotated", "clean"]
    lang: str
    font: str
    font_size_pt: float
    margin_mm: float
    labels: dict[str, str]  # labels.toml section for lang, English as fallback
    name: str
    contact: list[str]
    headline: RStatement | None
    summary: list[RStatement]
    work: list[RPosition]
    education: list[REntry]
    skills: list[RSkillGroup]
    languages: list[str]  # "German (C1)"
    certificates: list[REntry]
    legend: str  # annotated only
    report_summary: list[str]  # annotated only: score, splits, logistics, timeline gaps
    report: list[ReportRow]  # annotated only
