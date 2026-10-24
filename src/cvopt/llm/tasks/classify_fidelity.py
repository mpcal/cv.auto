"""classify_fidelity: how each candidate relates to its source claims."""

from typing import Literal

from cvopt.domain.base import Model
from cvopt.llm.task import Task
from cvopt.llm.tasks.common import tagged
from cvopt.ports import FilePart, TextPart


class FidelityItem(Model):
    ref: str
    text: str
    sources: list[str]


class FidelityInput(Model):
    items: list[FidelityItem]


class FidelityVerdict(Model):
    ref: str
    fidelity: Literal["unmodified", "rephrased", "exaggerated", "invented"]
    reason: str


class FidelityOutput(Model):
    verdicts: list[FidelityVerdict]


def render(inp: FidelityInput) -> list[TextPart | FilePart]:
    blocks = []
    for it in inp.items:
        sources = "\n".join(f"- {s}" for s in it.sources)
        blocks.append(f"{it.ref}\nstatement: {it.text}\nsources:\n{sources}")
    return [TextPart(tagged("statements", "\n\n".join(blocks)))]


def check(inp: FidelityInput, out: FidelityOutput) -> None:
    if sorted(v.ref for v in out.verdicts) != sorted(i.ref for i in inp.items):
        raise ValueError("give exactly one verdict per statement ref")


CLASSIFY_FIDELITY = Task(
    name="classify_fidelity",
    version="1",
    tier="strong",
    input_model=FidelityInput,
    output_model=FidelityOutput,
    max_tokens=3000,
    render=render,
    check=check,
)
