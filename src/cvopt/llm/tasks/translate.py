"""translate: faithful translation of claim texts into the JD's language."""

from cvopt.domain.base import Model
from cvopt.domain.facts import translation_quantities_match
from cvopt.llm.task import Task
from cvopt.llm.tasks.common import tagged
from cvopt.ports import FilePart, TextPart


class TextRef(Model):
    ref: str
    text: str


class TranslateInput(Model):
    texts: list[TextRef]
    target_language: str  # ISO 639-1
    feedback: str = ""


class TranslateOutput(Model):
    translations: list[TextRef]


def render(inp: TranslateInput) -> list[TextPart | FilePart]:
    body = "\n".join(f"{t.ref}: {t.text}" for t in inp.texts)
    return [
        TextPart(
            f"Target language: {inp.target_language}\n\n"
            + tagged("texts", body)
            + (f"\nCorrection required: {inp.feedback}" if inp.feedback else "")
        )
    ]


def check(inp: TranslateInput, out: TranslateOutput) -> None:
    if sorted(t.ref for t in out.translations) != sorted(t.ref for t in inp.texts):
        raise ValueError("give exactly one translation per input ref")
    sources = {t.ref: t.text for t in inp.texts}
    for t in out.translations:
        if not translation_quantities_match(sources[t.ref], t.text):
            raise ValueError(f"{t.ref}: preserve every original numeric quantity")


TRANSLATE = Task(
    name="translate",
    version="2",
    tier="fast",
    input_model=TranslateInput,
    output_model=TranslateOutput,
    max_tokens=4000,
    render=render,
    check=check,
)
