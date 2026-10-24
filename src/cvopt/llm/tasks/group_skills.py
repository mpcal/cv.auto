"""group_skills: skill keywords into 2–5 named groups."""

from cvopt.domain.base import Model
from cvopt.llm.task import Task
from cvopt.llm.tasks.common import tagged
from cvopt.ports import FilePart, TextPart


class GroupInput(Model):
    keywords: list[str]
    language: str


class SkillGroup(Model):
    name: str
    keywords: list[str]


class GroupOutput(Model):
    groups: list[SkillGroup]


def render(inp: GroupInput) -> list[TextPart | FilePart]:
    return [
        TextPart(
            f"Language of the group names: {inp.language}\n\n"
            + tagged("keywords", "\n".join(inp.keywords))
        )
    ]


def check(inp: GroupInput, out: GroupOutput) -> None:
    got = sorted(k for g in out.groups for k in g.keywords)
    if got != sorted(inp.keywords):
        raise ValueError("use every keyword exactly once, spelled exactly as given")
    if not 1 <= len(out.groups) <= 5:
        raise ValueError("give 2–5 groups (1 if there are fewer than four keywords)")


GROUP_SKILLS = Task(
    name="group_skills",
    version="1",
    tier="fast",
    input_model=GroupInput,
    output_model=GroupOutput,
    max_tokens=1000,
    render=render,
    check=check,
)
