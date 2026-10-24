"""extract_cv: profile, fact records and claims from a CV."""

from cvopt.domain.base import Model
from cvopt.domain.enums import ClaimType
from cvopt.llm.task import Task
from cvopt.llm.tasks.common import (
    ExCertificate,
    ExEducation,
    ExLanguage,
    ExPosition,
    check_claim_text,
    check_education,
    check_language,
    check_month,
    check_position,
    tagged,
)
from cvopt.ports import FilePart, TextPart


class CVInput(Model):
    text: str
    language: str  # ISO 639-1 of the document


class ExProfileLink(Model):
    network: str
    url: str


class ExProfile(Model):
    name: str
    email: str
    phone: str
    city: str
    country: str
    website: str
    profiles: list[ExProfileLink]


class ExCVClaim(Model):
    type: ClaimType
    text: str
    keywords: list[str]
    position: int  # index into positions, -1 = not tied to a position


class CVOutput(Model):
    profile: ExProfile
    positions: list[ExPosition]
    education: list[ExEducation]
    languages: list[ExLanguage]
    certificates: list[ExCertificate]
    claims: list[ExCVClaim]


def render(inp: CVInput) -> list[TextPart | FilePart]:
    return [TextPart(f"Document language: {inp.language}\n\n" + tagged("document", inp.text))]


def check(inp: CVInput, out: CVOutput) -> None:
    for i, p in enumerate(out.positions):
        check_position(p, f"positions[{i}]")
    for i, e in enumerate(out.education):
        check_education(e, f"education[{i}]")
    for i, lang in enumerate(out.languages):
        check_language(lang, f"languages[{i}]")
    for i, c in enumerate(out.certificates):
        check_month(c.issued_month, f"certificates[{i}].issued_month")
    for i, cl in enumerate(out.claims):
        check_claim_text(cl.text, cl.keywords, f"claims[{i}]")
        if not -1 <= cl.position < len(out.positions):
            raise ValueError(f"claims[{i}].position {cl.position} is not a position index")


EXTRACT_CV = Task(
    name="extract_cv",
    version="1",
    tier="strong",
    input_model=CVInput,
    output_model=CVOutput,
    max_tokens=8000,
    render=render,
    check=check,
)
