"""Output models and checks shared by the extraction tasks (P3 §6.4: no nulls, no
length constraints; absent values are "", 0 or [])."""

import re

from cvopt.domain.base import Model
from cvopt.domain.enums import ClaimType, DegreeLevel
from cvopt.domain.rules import CEFR_LEVELS

MONTH = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


class ExPosition(Model):
    employer: str
    employer_location: str
    title: str
    start_month: str  # YYYY-MM
    end_month: str  # YYYY-MM, "" = current


class ExEducation(Model):
    institution: str
    area: str
    degree_type: str  # as written
    degree_level: DegreeLevel
    start_month: str
    end_month: str
    grade: str


class ExLanguage(Model):
    language_code: str  # ISO 639-1
    level: str  # A1–C2 or native


class ExCertificate(Model):
    name: str
    issuer: str
    issued_month: str
    url: str


class ExSkillClaim(Model):
    text: str
    keywords: list[str]


class ExClaim(Model):
    type: ClaimType
    text: str
    keywords: list[str]


def check_month(value: str, where: str, *, required: bool = False) -> None:
    if (value or required) and not MONTH.match(value):
        raise ValueError(f"{where}: {value!r} is not a month in YYYY-MM form")


def check_position(p: ExPosition, where: str) -> None:
    if not p.employer or not p.title:
        raise ValueError(f"{where}: employer and title are required")
    check_month(p.start_month, f"{where}.start_month", required=True)
    check_month(p.end_month, f"{where}.end_month")


def check_education(e: ExEducation, where: str) -> None:
    if not e.institution:
        raise ValueError(f"{where}: institution is required")
    check_month(e.start_month, f"{where}.start_month")
    check_month(e.end_month, f"{where}.end_month")


def check_language(lang: ExLanguage, where: str) -> None:
    if not re.fullmatch(r"[a-z]{2}", lang.language_code):
        raise ValueError(f"{where}: language_code must be a lower-case ISO 639-1 code")
    if lang.level not in CEFR_LEVELS:
        raise ValueError(f"{where}: level must be one of {', '.join(CEFR_LEVELS)}")


def check_claim_text(text: str, keywords: list[str], where: str) -> None:
    if not text.strip():
        raise ValueError(f"{where}: empty claim text")
    if not keywords:
        raise ValueError(f"{where}: at least one keyword is required")


def tagged(tag: str, text: str) -> str:
    return f"<{tag}>\n{text}\n</{tag}>"
