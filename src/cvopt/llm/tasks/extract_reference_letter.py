"""extract_reference_letter: issuer, period, position and graded passages."""

from cvopt.domain.base import Model
from cvopt.domain.enums import CIStrEnum
from cvopt.domain.text import normalize
from cvopt.llm.task import Task
from cvopt.llm.tasks.common import (
    ExClaim,
    ExPosition,
    check_claim_text,
    check_month,
    check_position,
    tagged,
)
from cvopt.ports import FilePart, TextPart

MAX_EXCERPT = 300


class Grade(CIStrEnum):
    VERY_GOOD = "very_good"
    GOOD = "good"
    SATISFACTORY = "satisfactory"
    POOR = "poor"
    NONE = "none"  # not evaluative


EVIDENCE_GRADES = {Grade.VERY_GOOD, Grade.GOOD}


class PositionRef(Model):
    ref: str  # "P1", "P2", ...
    employer: str
    title: str
    start_month: str
    end_month: str  # "" = current


class LetterInput(Model):
    text: str
    language: str
    positions: list[PositionRef]


class Passage(Model):
    excerpt: str  # verbatim, at most 300 characters
    grade: Grade
    claims: list[ExClaim]


class LetterOutput(Model):
    issuer: str  # employer issuing the letter
    author: str
    period_start: str
    period_end: str
    position_ref: str  # a given ref, or "" when no listed position matches
    new_position: ExPosition  # filled only when position_ref is ""
    passages: list[Passage]


def render(inp: LetterInput) -> list[TextPart | FilePart]:
    positions = "\n".join(
        f"{p.ref}: {p.title} at {p.employer}, {p.start_month} to {p.end_month or 'current'}"
        for p in inp.positions
    )
    return [
        TextPart(
            f"Document language: {inp.language}\n\n"
            + tagged("positions", positions or "(none)")
            + "\n\n"
            + tagged("document", inp.text)
        )
    ]


def check(inp: LetterInput, out: LetterOutput) -> None:
    refs = {p.ref for p in inp.positions}
    if out.position_ref and out.position_ref not in refs:
        raise ValueError(f"position_ref {out.position_ref!r} is not one of {sorted(refs)}")
    if not out.position_ref:
        check_position(out.new_position, "new_position")
    check_month(out.period_start, "period_start")
    check_month(out.period_end, "period_end")
    text = normalize(inp.text)
    for i, p in enumerate(out.passages):
        if len(p.excerpt) > MAX_EXCERPT:
            raise ValueError(f"passages[{i}].excerpt is longer than {MAX_EXCERPT} characters")
        if normalize(p.excerpt) not in text:
            raise ValueError(f"passages[{i}].excerpt is not a verbatim quote of the letter")
        if p.claims and p.grade not in EVIDENCE_GRADES:
            raise ValueError(
                f"passages[{i}] is graded {p.grade}; only good and very_good "
                "passages may carry claims"
            )
        for j, c in enumerate(p.claims):
            check_claim_text(c.text, c.keywords, f"passages[{i}].claims[{j}]")


EXTRACT_REFERENCE_LETTER = Task(
    name="extract_reference_letter",
    version="1",
    tier="strong",
    input_model=LetterInput,
    output_model=LetterOutput,
    max_tokens=8000,
    render=render,
    check=check,
)
