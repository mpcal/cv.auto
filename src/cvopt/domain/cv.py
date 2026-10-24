"""The JSON Resume subset cvopt reads and writes, and the element parser (P2 §7.1)."""

import re
from datetime import date
from typing import Any

from pydantic import BaseModel, ConfigDict

from cvopt.domain.base import Model, Month
from cvopt.domain.enums import ElementKind
from cvopt.domain.rules import cefr_level, degree_level, language_code
from cvopt.domain.text import split_sentences


class Lenient(BaseModel):
    """Reads any JSON Resume file: unknown fields are ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class JRProfile(Lenient):
    network: str = ""
    url: str = ""
    username: str = ""


class JRLocation(Lenient):
    city: str = ""
    countryCode: str = ""
    region: str = ""


class JRBasics(Lenient):
    name: str = ""
    label: str = ""
    email: str = ""
    phone: str = ""
    url: str = ""
    summary: str = ""
    location: JRLocation = JRLocation()
    profiles: list[JRProfile] = []


class JRWork(Lenient):
    name: str = ""
    location: str = ""
    position: str = ""
    startDate: str = ""
    endDate: str = ""
    highlights: list[str] = []


class JREducation(Lenient):
    institution: str = ""
    area: str = ""
    studyType: str = ""
    startDate: str = ""
    endDate: str = ""
    score: str = ""


class JRSkill(Lenient):
    name: str = ""
    keywords: list[str] = []


class JRLanguage(Lenient):
    language: str = ""
    fluency: str = ""


class JRCertificate(Lenient):
    name: str = ""
    date: str = ""
    issuer: str = ""
    url: str = ""


class JSONResume(Lenient):
    basics: JRBasics = JRBasics()
    work: list[JRWork] = []
    education: list[JREducation] = []
    skills: list[JRSkill] = []
    languages: list[JRLanguage] = []
    certificates: list[JRCertificate] = []
    meta: dict[str, Any] = {}


class CVElement(Model):
    id: str  # JSON pointer; summary sentences append "#<index>"
    kind: ElementKind
    text: str
    position_ref: str | None = (
        None  # highlights: stored position id, or "/work/<i>" for a parsed file
    )
    start_month: Month | None = None
    end_month: Month | None = None
    placement: float
    meta: dict[str, str] = {}  # structured values: language code and level, degree level, area


class Placement(Model):
    recent_highlight: float = 1.0
    older_highlight: float = 0.85
    headline_summary: float = 0.8
    skills: float = 0.7
    recency_years: int = 5


def to_month(value: str) -> str | None:
    """JSON Resume dates may be YYYY, YYYY-MM or YYYY-MM-DD; all are read as months."""
    m = re.match(r"^(\d{4})(?:-(\d{2}))?", value.strip())
    if not m:
        return None
    return f"{m.group(1)}-{m.group(2) or '01'}"


def month_of(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def months_between(a: str, b: str) -> int:
    """Whole months from month a to month b."""
    ya, ma = map(int, a.split("-"))
    yb, mb = map(int, b.split("-"))
    return (yb - ya) * 12 + (mb - ma)


def highlight_placement(end: str | None, as_of: date, p: Placement) -> float:
    if end is None or months_between(end, month_of(as_of)) < 12 * p.recency_years:
        return p.recent_highlight
    return p.older_highlight


def parse_elements(cv: JSONResume, as_of: date, lang: str, p: Placement) -> list[CVElement]:
    """Every scorable element of a CV, in document order."""
    els: list[CVElement] = []
    b = cv.basics
    if b.label.strip():
        els.append(
            CVElement(
                id="/basics/label",
                kind=ElementKind.HEADLINE,
                text=b.label,
                placement=p.headline_summary,
            )
        )
    for i, sentence in enumerate(split_sentences(b.summary, lang)):
        els.append(
            CVElement(
                id=f"/basics/summary#{i}",
                kind=ElementKind.SUMMARY,
                text=sentence,
                placement=p.headline_summary,
            )
        )
    for i, w in enumerate(cv.work):
        start = to_month(w.startDate)
        end = to_month(w.endDate) if w.endDate else None
        for j, h in enumerate(w.highlights):
            els.append(
                CVElement(
                    id=f"/work/{i}/highlights/{j}",
                    kind=ElementKind.HIGHLIGHT,
                    text=h,
                    position_ref=f"/work/{i}",
                    start_month=start,
                    end_month=end,
                    placement=highlight_placement(end, as_of, p),
                )
            )
    for i, s in enumerate(cv.skills):
        for j, k in enumerate(s.keywords):
            els.append(
                CVElement(
                    id=f"/skills/{i}/keywords/{j}",
                    kind=ElementKind.SKILL,
                    text=k,
                    placement=p.skills,
                )
            )
    for i, e in enumerate(cv.education):
        text = ", ".join(x for x in (f"{e.studyType} {e.area}".strip(), e.institution) if x)
        els.append(
            CVElement(
                id=f"/education/{i}",
                kind=ElementKind.EDUCATION,
                text=text,
                placement=1.0,
                meta={"degree_level": degree_level(e.studyType).value, "area": e.area},
            )
        )
    for i, lg in enumerate(cv.languages):
        meta = {
            "language_code": language_code(lg.language) or "",
            "level": cefr_level(lg.fluency) or "",
        }
        els.append(
            CVElement(
                id=f"/languages/{i}",
                kind=ElementKind.LANGUAGE,
                text=f"{lg.language}: {lg.fluency}".strip(": "),
                placement=1.0,
                meta=meta,
            )
        )
    for i, c in enumerate(cv.certificates):
        els.append(
            CVElement(
                id=f"/certificates/{i}",
                kind=ElementKind.CERTIFICATE,
                text=c.name,
                placement=1.0,
                meta={"issuer": c.issuer},
            )
        )
    return els
