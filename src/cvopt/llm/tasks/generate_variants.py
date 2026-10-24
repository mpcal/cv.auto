"""generate_variants: rephrased and exaggerated versions of 1–3 claims of one position."""

from typing import Literal

from cvopt.domain.base import Model
from cvopt.llm.task import Task
from cvopt.llm.tasks.common import tagged
from cvopt.ports import FilePart, TextPart

MAX_CHARS = 220


class ReqRef(Model):
    ref: str
    text: str
    keywords: list[str]


class ClaimText(Model):
    ref: str
    text: str


class VariantsInput(Model):
    position: str  # "Title at Employer"
    claims: list[ClaimText]
    requirements: list[ReqRef]
    language: str


class Variant(Model):
    text: str
    source_refs: list[str]
    fidelity: Literal["rephrased", "exaggerated"]


class VariantsOutput(Model):
    variants: list[Variant]


def render(inp: VariantsInput) -> list[TextPart | FilePart]:
    claims = "\n".join(f"{c.ref}: {c.text}" for c in inp.claims)
    reqs = "\n".join(
        f"{r.ref}: {r.text} [JD terms: {', '.join(r.keywords)}]" for r in inp.requirements
    )
    return [
        TextPart(
            f"Output language: {inp.language}\nPosition: {inp.position}\n\n"
            + tagged("claims", claims)
            + "\n\n"
            + tagged("requirements", reqs)
        )
    ]


def check(inp: VariantsInput, out: VariantsOutput) -> None:
    refs = {c.ref for c in inp.claims}
    for i, v in enumerate(out.variants):
        if not v.source_refs or not set(v.source_refs) <= refs:
            raise ValueError(f"variants[{i}].source_refs must name given claims: {sorted(refs)}")
        if len(v.text) > MAX_CHARS:
            raise ValueError(f"variants[{i}] is longer than {MAX_CHARS} characters")


GENERATE_VARIANTS = Task(
    name="generate_variants",
    version="1",
    tier="strong",
    input_model=VariantsInput,
    output_model=VariantsOutput,
    max_tokens=3000,
    render=render,
    check=check,
)
