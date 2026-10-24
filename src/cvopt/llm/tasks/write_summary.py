"""write_summary: headline and summary-sentence candidates from the selected statements."""

from typing import Literal

from cvopt.domain.base import Model
from cvopt.llm.task import Task
from cvopt.llm.tasks.common import tagged
from cvopt.llm.tasks.generate_variants import ClaimText, ReqRef
from cvopt.ports import FilePart, TextPart


class SummaryInput(Model):
    title: str  # the JD's job title
    statements: list[ClaimText]  # selected statements, refs S1, S2, ...
    requirements: list[ReqRef]
    language: str


class SummaryCandidate(Model):
    text: str
    source_refs: list[str]
    fidelity: Literal["rephrased", "exaggerated"]


class SummaryOutput(Model):
    headlines: list[SummaryCandidate]
    sentences: list[SummaryCandidate]


def render(inp: SummaryInput) -> list[TextPart | FilePart]:
    stmts = "\n".join(f"{s.ref}: {s.text}" for s in inp.statements)
    reqs = "\n".join(
        f"{r.ref}: {r.text} [JD terms: {', '.join(r.keywords)}]" for r in inp.requirements
    )
    return [
        TextPart(
            f"Output language: {inp.language}\nJob title: {inp.title}\n\n"
            + tagged("statements", stmts)
            + "\n\n"
            + tagged("requirements", reqs)
        )
    ]


def check(inp: SummaryInput, out: SummaryOutput) -> None:
    refs = {s.ref for s in inp.statements}
    if not out.headlines or len(out.headlines) > 3 or len(out.sentences) > 6:
        raise ValueError("give 1–3 headlines and at most 6 sentences")
    for kind, items in (("headlines", out.headlines), ("sentences", out.sentences)):
        for i, c in enumerate(items):
            if not c.source_refs or not set(c.source_refs) <= refs:
                raise ValueError(f"{kind}[{i}].source_refs must name given statements")
    for i, h in enumerate(out.headlines):
        if len(h.text.split()) > 8:
            raise ValueError(f"headlines[{i}] has more than 8 words")


WRITE_SUMMARY = Task(
    name="write_summary",
    version="3",
    tier="strong",
    input_model=SummaryInput,
    output_model=SummaryOutput,
    max_tokens=3000,
    render=render,
    check=check,
)
