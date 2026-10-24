"""generate_inventions: statements for a requirement no confirmed claim covers."""

from typing import Literal

from cvopt.domain.base import Model
from cvopt.domain.enums import RequirementCategory
from cvopt.llm.task import Task
from cvopt.llm.tasks.common import tagged
from cvopt.llm.tasks.generate_variants import ClaimText
from cvopt.ports import FilePart, TextPart


class PositionText(Model):
    ref: str
    text: str  # "Title at Employer, 2021-03 to current"


class InventionsInput(Model):
    requirement: str
    category: RequirementCategory
    keywords: list[str]
    positions: list[PositionText]
    claims: list[ClaimText]
    language: str


class Invention(Model):
    slot: Literal["skill", "highlight"]
    position_ref: str  # highlights only, else ""
    text: str
    inferable: bool
    inferred_from: list[str]  # claim refs


class InventionsOutput(Model):
    candidates: list[Invention]


def render(inp: InventionsInput) -> list[TextPart | FilePart]:
    positions = "\n".join(f"{p.ref}: {p.text}" for p in inp.positions)
    claims = "\n".join(f"{c.ref}: {c.text}" for c in inp.claims)
    req = f"{inp.requirement}\ncategory: {inp.category.value}\nJD terms: {', '.join(inp.keywords)}"
    return [
        TextPart(
            f"Output language: {inp.language}\n\n"
            + tagged("requirement", req)
            + "\n\n"
            + tagged("positions", positions)
            + "\n\n"
            + tagged("claims", claims)
        )
    ]


def check(inp: InventionsInput, out: InventionsOutput) -> None:
    positions = {p.ref for p in inp.positions}
    claims = {c.ref for c in inp.claims}
    if len(out.candidates) > 3:
        raise ValueError("give at most three candidates")
    for i, c in enumerate(out.candidates):
        if c.slot == "highlight" and c.position_ref not in positions:
            raise ValueError(f"candidates[{i}].position_ref must be one of {sorted(positions)}")
        if c.slot == "skill" and len(c.text) > 40:
            raise ValueError(f"candidates[{i}] is a skill: give the keyword only")
        if not set(c.inferred_from) <= claims:
            raise ValueError(f"candidates[{i}].inferred_from must name given claims")
        if c.inferable and not c.inferred_from:
            raise ValueError(f"candidates[{i}] is inferable: name the claims it is inferred from")
        if any(ch.isdigit() for ch in c.text):
            raise ValueError(f"candidates[{i}] contains a figure; invented statements have none")


GENERATE_INVENTIONS = Task(
    name="generate_inventions",
    version="1",
    tier="strong",
    input_model=InventionsInput,
    output_model=InventionsOutput,
    max_tokens=3000,
    render=render,
    check=check,
)
