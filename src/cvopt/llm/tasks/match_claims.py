"""match_claims: is a new claim the same assertion as any existing claim?"""

from cvopt.domain.base import Model
from cvopt.llm.task import Task
from cvopt.llm.tasks.common import tagged
from cvopt.ports import FilePart, TextPart


class ClaimRef(Model):
    ref: str  # "C1", "C2", ...
    text: str
    keywords: list[str]


class MatchInput(Model):
    new: ClaimRef
    existing: list[ClaimRef]


class Verdict(Model):
    ref: str
    same: bool
    quantity_conflict: bool  # same assertion, but different numbers
    reason: str


class MatchOutput(Model):
    verdicts: list[Verdict]


def _line(c: ClaimRef) -> str:
    return f"{c.ref}: {c.text} [keywords: {', '.join(c.keywords)}]"


def render(inp: MatchInput) -> list[TextPart | FilePart]:
    existing = "\n".join(_line(c) for c in inp.existing)
    return [
        TextPart(tagged("new_claim", _line(inp.new)) + "\n\n" + tagged("existing_claims", existing))
    ]


def check(inp: MatchInput, out: MatchOutput) -> None:
    given = [c.ref for c in inp.existing]
    got = [v.ref for v in out.verdicts]
    if sorted(got) != sorted(given):
        raise ValueError(f"give exactly one verdict per existing claim: {', '.join(given)}")
    for v in out.verdicts:
        if v.quantity_conflict and not v.same:
            raise ValueError(f"{v.ref}: quantity_conflict requires same = true")


MATCH_CLAIMS = Task(
    name="match_claims",
    version="1",
    tier="fast",
    input_model=MatchInput,
    output_model=MatchOutput,
    max_tokens=2000,
    render=render,
    check=check,
)
