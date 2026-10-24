"""judge_match: does each CV element meet one requirement, in meaning?"""

from typing import Literal

from cvopt.domain.base import Model
from cvopt.domain.enums import RequirementCategory
from cvopt.llm.task import Task
from cvopt.llm.tasks.common import tagged
from cvopt.ports import FilePart, TextPart


class JudgedRequirement(Model):
    text: str
    category: RequirementCategory
    keywords: list[str]
    synonyms: list[str]


class ElementRef(Model):
    ref: str  # "E1", "E2", ...
    text: str


class JudgeInput(Model):
    requirement: JudgedRequirement
    elements: list[ElementRef]


class JudgeVerdict(Model):
    ref: str
    level: Literal["semantic", "partial", "none"]
    reason: str


class JudgeOutput(Model):
    verdicts: list[JudgeVerdict]


def render(inp: JudgeInput) -> list[TextPart | FilePart]:
    r = inp.requirement
    req = (
        f"{r.text}\ncategory: {r.category.value}\nkeywords: {', '.join(r.keywords)}\n"
        f"synonyms: {', '.join(r.synonyms)}"
    )
    els = "\n".join(f"{e.ref}: {e.text}" for e in inp.elements)
    return [TextPart(tagged("requirement", req) + "\n\n" + tagged("cv_elements", els))]


def check(inp: JudgeInput, out: JudgeOutput) -> None:
    given = sorted(e.ref for e in inp.elements)
    if sorted(v.ref for v in out.verdicts) != given:
        raise ValueError(f"give exactly one verdict per element: {', '.join(given)}")


JUDGE_MATCH = Task(
    name="judge_match",
    version="1",
    tier="fast",
    input_model=JudgeInput,
    output_model=JudgeOutput,
    max_tokens=4000,
    render=render,
    check=check,
)
