"""Context verification supplements deterministic quantities with entities and associations."""

from typing import Literal

from cvopt.domain.base import Model
from cvopt.domain.facts import without_numeric_names
from cvopt.llm.task import Task
from cvopt.llm.tasks.common import tagged
from cvopt.ports import FilePart, TextPart


class FactItem(Model):
    ref: str
    text: str
    sources: list[str]
    mode: Literal["translation", "sourced", "invented"]
    name_hints: list[str] = []  # JD spellings, never original assertions


class FactsInput(Model):
    items: list[FactItem]


class FactVerdict(Model):
    ref: str
    facts_preserved: bool
    reason: str
    numeric_name_spans: list[str] = []


class FactsOutput(Model):
    verdicts: list[FactVerdict]


def render(inp: FactsInput) -> list[TextPart | FilePart]:
    return [TextPart(tagged("fact_checks", inp.model_dump_json()))]


def check(inp: FactsInput, out: FactsOutput) -> None:
    if sorted(v.ref for v in out.verdicts) != sorted(i.ref for i in inp.items):
        raise ValueError("give exactly one factual verdict per input ref")
    items = {it.ref: it for it in inp.items}
    for verdict in out.verdicts:
        item = items[verdict.ref]
        if verdict.numeric_name_spans and item.mode != "invented":
            raise ValueError("numeric-name exemptions apply only to invented statements")
        if without_numeric_names(item.text, verdict.numeric_name_spans) is None:
            raise ValueError(
                "numeric-name spans must be nonempty exact digit-free spans of the text"
            )


VERIFY_FACTS = Task(
    name="verify_facts",
    version="2",
    tier="strong",
    input_model=FactsInput,
    output_model=FactsOutput,
    max_tokens=3000,
    render=render,
    check=check,
)
